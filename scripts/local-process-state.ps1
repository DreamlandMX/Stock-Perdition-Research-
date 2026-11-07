function Get-LocalCommandLineSha256 {
    param([AllowEmptyString()][string]$CommandLine)

    $bytes = [Text.Encoding]::UTF8.GetBytes($CommandLine)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-LocalProcessSnapshot {
    param([int]$ProcessId)

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }

    $commandLine = [string]$process.CommandLine
    $startedAt = ([DateTime]$process.CreationDate).ToUniversalTime().ToString("o")
    return [ordered]@{
        pid = [int]$process.ProcessId
        process_name = [string]$process.Name
        executable_path = [string]$process.ExecutablePath
        started_at_utc = $startedAt
        command_line = $commandLine
        command_line_sha256 = Get-LocalCommandLineSha256 -CommandLine $commandLine
    }
}

function Test-LocalProcessMatchesTarget {
    param(
        [psobject]$ProcessSnapshot,
        [string[]]$ExpectedProcessNames,
        [string[]]$ExpectedExecutablePaths,
        [string[]]$ExpectedCommandFragments
    )

    if ($null -eq $ProcessSnapshot) { return $false }

    $actualName = [IO.Path]::GetFileNameWithoutExtension([string]$ProcessSnapshot.process_name)
    $nameMatches = $false
    foreach ($expectedName in @($ExpectedProcessNames)) {
        $normalizedExpectedName = [IO.Path]::GetFileNameWithoutExtension($expectedName)
        if ([string]::Equals($actualName, $normalizedExpectedName, [StringComparison]::OrdinalIgnoreCase)) {
            $nameMatches = $true
            break
        }
    }
    if (-not $nameMatches) { return $false }

    $pathMatches = $false
    foreach ($expectedPath in @($ExpectedExecutablePaths)) {
        if (
            -not [string]::IsNullOrWhiteSpace($expectedPath) -and
            [string]::Equals(
                [string]$ProcessSnapshot.executable_path,
                $expectedPath,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            $pathMatches = $true
            break
        }
    }
    if (-not $pathMatches) { return $false }

    $commandLine = [string]$ProcessSnapshot.command_line
    foreach ($fragment in @($ExpectedCommandFragments)) {
        if (
            [string]::IsNullOrWhiteSpace($fragment) -or
            $commandLine.IndexOf($fragment, [StringComparison]::OrdinalIgnoreCase) -lt 0
        ) {
            return $false
        }
    }
    return $true
}

function Read-LocalProcessState {
    param([string]$Path, [string]$Component)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [ordered]@{ status = "missing"; format = $null; pid = $null; state = $null }
    }

    $raw = (Get-Content -LiteralPath $Path -Raw -Encoding utf8).Trim()
    $legacyPid = 0
    if ([int]::TryParse($raw, [ref]$legacyPid) -and $legacyPid -gt 0) {
        return [ordered]@{ status = "parsed"; format = "legacy"; pid = $legacyPid; state = $null }
    }

    try {
        $state = $raw | ConvertFrom-Json -ErrorAction Stop
        $propertyNames = @($state.PSObject.Properties.Name)
        $requiredProperties = @(
            "schema_version", "component", "pid", "process_name", "executable_path",
            "started_at_utc", "command_line_sha256", "instance_id"
        )
        foreach ($property in $requiredProperties) {
            if ($propertyNames -notcontains $property) { throw "missing_property" }
        }

        $schemaVersion = 0
        $parsedPid = 0
        $startedAt = [DateTimeOffset]::MinValue
        $instanceId = [Guid]::Empty
        if (-not [int]::TryParse([string]$state.schema_version, [ref]$schemaVersion) -or $schemaVersion -ne 1) {
            throw "unsupported_schema"
        }
        if (-not [int]::TryParse([string]$state.pid, [ref]$parsedPid) -or $parsedPid -le 0) {
            throw "invalid_pid"
        }
        if (-not [string]::Equals([string]$state.component, $Component, [StringComparison]::Ordinal)) {
            throw "component_mismatch"
        }
        if (
            [string]::IsNullOrWhiteSpace([string]$state.process_name) -or
            [string]::IsNullOrWhiteSpace([string]$state.executable_path) -or
            -not [DateTimeOffset]::TryParse([string]$state.started_at_utc, [ref]$startedAt) -or
            ([string]$state.command_line_sha256) -notmatch '^[0-9a-fA-F]{64}$' -or
            -not [Guid]::TryParse([string]$state.instance_id, [ref]$instanceId)
        ) {
            throw "invalid_identity"
        }

        return [ordered]@{ status = "parsed"; format = "json"; pid = $parsedPid; state = $state }
    }
    catch {
        return [ordered]@{ status = "invalid"; format = $null; pid = $null; state = $null }
    }
}

function Test-LocalRecordedCoreIdentity {
    param([psobject]$State, [psobject]$ProcessSnapshot)

    if ($null -eq $State -or $null -eq $ProcessSnapshot) { return $false }

    $recordedStartedAt = if ($State.started_at_utc -is [DateTime]) {
        ([DateTimeOffset]([DateTime]$State.started_at_utc)).ToUniversalTime()
    }
    else {
        [DateTimeOffset]::Parse([string]$State.started_at_utc).ToUniversalTime()
    }
    $actualStartedAt = if ($ProcessSnapshot.started_at_utc -is [DateTime]) {
        ([DateTimeOffset]([DateTime]$ProcessSnapshot.started_at_utc)).ToUniversalTime()
    }
    else {
        [DateTimeOffset]::Parse([string]$ProcessSnapshot.started_at_utc).ToUniversalTime()
    }
    $startedAtDelta = ($recordedStartedAt - $actualStartedAt).Duration()
    return (
        [int]$State.pid -eq [int]$ProcessSnapshot.pid -and
        [string]::Equals(
            [string]$State.process_name,
            [string]$ProcessSnapshot.process_name,
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        $startedAtDelta -lt [TimeSpan]::FromSeconds(1)
    )
}

function Test-LocalRecordedIdentity {
    param([psobject]$State, [psobject]$ProcessSnapshot)

    if (-not (Test-LocalRecordedCoreIdentity -State $State -ProcessSnapshot $ProcessSnapshot)) {
        return $false
    }
    return (
        [string]::Equals(
            [string]$State.executable_path,
            [string]$ProcessSnapshot.executable_path,
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        [string]::Equals(
            [string]$State.command_line_sha256,
            [string]$ProcessSnapshot.command_line_sha256,
            [StringComparison]::OrdinalIgnoreCase
        )
    )
}

function Test-LocalRecordedStateMatchesTarget {
    param(
        [psobject]$State,
        [string[]]$ExpectedProcessNames,
        [string[]]$ExpectedExecutablePaths
    )

    if ($null -eq $State) { return $false }

    $actualName = [IO.Path]::GetFileNameWithoutExtension([string]$State.process_name)
    $nameMatches = $false
    foreach ($expectedName in @($ExpectedProcessNames)) {
        $normalizedExpectedName = [IO.Path]::GetFileNameWithoutExtension($expectedName)
        if ([string]::Equals($actualName, $normalizedExpectedName, [StringComparison]::OrdinalIgnoreCase)) {
            $nameMatches = $true
            break
        }
    }
    if (-not $nameMatches) { return $false }

    foreach ($expectedPath in @($ExpectedExecutablePaths)) {
        if (
            -not [string]::IsNullOrWhiteSpace($expectedPath) -and
            [string]::Equals(
                [string]$State.executable_path,
                $expectedPath,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $true
        }
    }
    return $false
}

function Get-LocalProcessStateStatus {
    param(
        [string]$Path,
        [string]$Component,
        [string[]]$ExpectedProcessNames,
        [string[]]$ExpectedExecutablePaths,
        [string[]]$ExpectedCommandFragments,
        [AllowNull()][psobject]$ProcessSnapshot
    )

    $parsed = Read-LocalProcessState -Path $Path -Component $Component
    if ($parsed.status -eq "missing") {
        return [ordered]@{ status = "missing"; format = $null; pid = $null; reason = "state_missing" }
    }
    if ($parsed.status -eq "invalid") {
        return [ordered]@{ status = "invalid"; format = $null; pid = $null; reason = "unsupported_or_malformed_state" }
    }

    if (-not $PSBoundParameters.ContainsKey("ProcessSnapshot")) {
        $ProcessSnapshot = Get-LocalProcessSnapshot -ProcessId ([int]$parsed.pid)
    }
    if ($null -eq $ProcessSnapshot) {
        return [ordered]@{ status = "stale"; format = $parsed.format; pid = $parsed.pid; reason = "process_missing" }
    }

    if ($parsed.format -eq "json") {
        $metadataUnavailable = (
            [string]::IsNullOrWhiteSpace([string]$ProcessSnapshot.executable_path) -and
            [string]::IsNullOrWhiteSpace([string]$ProcessSnapshot.command_line)
        )
        if ($metadataUnavailable) {
            if (-not (Test-LocalRecordedCoreIdentity -State $parsed.state -ProcessSnapshot $ProcessSnapshot)) {
                return [ordered]@{
                    status = "stale"
                    format = "json"
                    pid = $parsed.pid
                    reason = "process_identity_mismatch"
                }
            }
            if (-not (Test-LocalRecordedStateMatchesTarget `
                -State $parsed.state `
                -ExpectedProcessNames $ExpectedProcessNames `
                -ExpectedExecutablePaths $ExpectedExecutablePaths
            )) {
                return [ordered]@{
                    status = "stale"
                    format = "json"
                    pid = $parsed.pid
                    reason = "process_not_managed"
                }
            }
            return [ordered]@{
                status = "active"
                format = "json"
                pid = $parsed.pid
                reason = "identity_match_metadata_unavailable"
            }
        }
        if (-not (Test-LocalRecordedIdentity -State $parsed.state -ProcessSnapshot $ProcessSnapshot)) {
            return [ordered]@{
                status = "stale"
                format = "json"
                pid = $parsed.pid
                reason = "process_identity_mismatch"
            }
        }
        if (-not (Test-LocalProcessMatchesTarget `
            -ProcessSnapshot $ProcessSnapshot `
            -ExpectedProcessNames $ExpectedProcessNames `
            -ExpectedExecutablePaths $ExpectedExecutablePaths `
            -ExpectedCommandFragments $ExpectedCommandFragments
        )) {
            return [ordered]@{
                status = "stale"
                format = "json"
                pid = $parsed.pid
                reason = "process_not_managed"
            }
        }
        return [ordered]@{ status = "active"; format = "json"; pid = $parsed.pid; reason = "identity_match" }
    }

    if (-not (Test-LocalProcessMatchesTarget `
        -ProcessSnapshot $ProcessSnapshot `
        -ExpectedProcessNames $ExpectedProcessNames `
        -ExpectedExecutablePaths $ExpectedExecutablePaths `
        -ExpectedCommandFragments $ExpectedCommandFragments
    )) {
        return [ordered]@{
            status = "stale"
            format = "legacy"
            pid = $parsed.pid
            reason = "legacy_process_not_managed"
        }
    }
    return [ordered]@{ status = "active"; format = "legacy"; pid = $parsed.pid; reason = "legacy_target_match" }
}

function Write-LocalProcessState {
    param([string]$Path, [string]$Component, [int]$ProcessId)

    $snapshot = $null
    for ($attempt = 0; $attempt -lt 20 -and $null -eq $snapshot; $attempt++) {
        $snapshot = Get-LocalProcessSnapshot -ProcessId $ProcessId
        if ($null -eq $snapshot) { Start-Sleep -Milliseconds 50 }
    }
    if ($null -eq $snapshot) { throw "local_process_identity_unavailable" }
    if (
        [string]::IsNullOrWhiteSpace([string]$snapshot.process_name) -or
        [string]::IsNullOrWhiteSpace([string]$snapshot.executable_path) -or
        [string]::IsNullOrWhiteSpace([string]$snapshot.started_at_utc)
    ) {
        throw "local_process_identity_incomplete"
    }

    $state = [ordered]@{
        schema_version = 1
        component = $Component
        pid = [int]$snapshot.pid
        process_name = [string]$snapshot.process_name
        executable_path = [string]$snapshot.executable_path
        started_at_utc = [string]$snapshot.started_at_utc
        command_line_sha256 = [string]$snapshot.command_line_sha256
        instance_id = [Guid]::NewGuid().ToString()
    }
    $temporaryPath = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        $state | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporaryPath -Encoding utf8
        Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
    }
    finally {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
    }
    return $state
}
