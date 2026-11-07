param(
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 18766,
    [ValidateRange(1024, 65535)]
    [int]$UiPort = 18765,
    [ValidateSet("dev", "preview")]
    [string]$UiMode = "dev",
    [string]$ResearchRoot = "",
    [string]$LiveRoot = "",
    [switch]$AllowUnavailable,
    [switch]$EnableScheduler,
    [switch]$DisableScheduler
)

$ErrorActionPreference = "Stop"
if ($EnableScheduler -and $DisableScheduler) {
    [Console]::Error.WriteLine("scheduler_flags_conflict")
    exit 2
}

function Wait-EodWorker {
    param([string]$Url, [int]$TimeoutSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $worker = Invoke-RestMethod -Uri $Url -TimeoutSec 2
            if ($worker.state -eq "ready") {
                return $true
            }
        }
        catch { }
        Start-Sleep -Milliseconds 250
    }
    return $false
}
$startScheduler = -not $DisableScheduler
$repositoryRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$runRoot = Join-Path $repositoryRoot "var\run"
$logRoot = Join-Path $repositoryRoot "var\log"
$pythonCommand = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$liveCommand = Join-Path $repositoryRoot ".venv\Scripts\stock-prediction.exe"
$expectedNodeVersion = (Get-Content -LiteralPath (Join-Path $repositoryRoot ".node-version") -Raw).Trim()
$winGetNodeRoot = Join-Path $env:LOCALAPPDATA (
    "Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\" +
    "node-v$expectedNodeVersion-win-x64"
)
$nodeCommand = Join-Path $winGetNodeRoot "node.exe"
$viteCommand = Join-Path $repositoryRoot "node_modules\vite\bin\vite.js"
$sourceValidationCommand = Join-Path $PSScriptRoot "validate-local-source.py"
$apiPidPath = Join-Path $runRoot "api.pid"
$uiPidPath = Join-Path $runRoot "ui.pid"
$schedulerPidPath = Join-Path $runRoot "scheduler.pid"
. (Join-Path $PSScriptRoot "local-process-state.ps1")

function Test-LoopbackPort {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $result = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(250)) { return $false }
        $client.EndConnect($result)
        return $true
    }
    catch { return $false }
    finally { $client.Dispose() }
}
if (-not (Test-Path -LiteralPath $liveCommand -PathType Leaf)) {
    [Console]::Error.WriteLine("live_cli_unavailable")
    exit 2
}

function Wait-LocalUrl {
    param([string]$Url, [int]$TimeoutSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        }
        catch { }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

if (-not (Test-Path -LiteralPath $pythonCommand -PathType Leaf)) {
    [Console]::Error.WriteLine("python_311_environment_unavailable")
    exit 2
}
if (-not (Test-Path -LiteralPath $nodeCommand -PathType Leaf)) {
    [Console]::Error.WriteLine("node_toolchain_unavailable")
    exit 2
}
if ((& $nodeCommand --version).Trim() -ne "v$expectedNodeVersion") {
    [Console]::Error.WriteLine("node_version_mismatch")
    exit 2
}
if (-not (Test-Path -LiteralPath $viteCommand -PathType Leaf)) {
    [Console]::Error.WriteLine("frontend_dependencies_unavailable")
    exit 2
}
if (-not (Test-Path -LiteralPath $sourceValidationCommand -PathType Leaf)) {
    [Console]::Error.WriteLine("opend_history_runtime_invalid")
    exit 2
}
if ((Test-LoopbackPort $ApiPort) -or (Test-LoopbackPort $UiPort)) {
    [Console]::Error.WriteLine("local_port_already_in_use")
    exit 2
}

$researchRootCandidate = if ($ResearchRoot.Trim() -eq "") {
    Join-Path $repositoryRoot "var\research"
}
else { $ResearchRoot }
$liveRootCandidate = if ($LiveRoot.Trim() -eq "") {
    $activePointer = Join-Path $repositoryRoot "var\live-active.json"
    if (Test-Path -LiteralPath $activePointer -PathType Leaf) {
        try {
            $activeRevision = (Get-Content -LiteralPath $activePointer -Raw | ConvertFrom-Json).dataset_revision
            if ([string]::IsNullOrWhiteSpace($activeRevision)) { throw "invalid active dataset" }
            Join-Path $repositoryRoot ("var\live-datasets\" + $activeRevision)
        }
        catch {
            [Console]::Error.WriteLine("live_active_pointer_invalid")
            exit 2
        }
    }
    else { Join-Path $repositoryRoot "var\live" }
}
else { $LiveRoot }

if (-not (Test-Path -LiteralPath $researchRootCandidate -PathType Container)) {
    if ($AllowUnavailable) {
        New-Item -ItemType Directory -Force -Path $researchRootCandidate | Out-Null
    }
    else {
        [Console]::Error.WriteLine("research_snapshot_root_unavailable_run_live_publish_first")
        exit 2
    }
}
try {
    $resolvedResearchRoot = (Resolve-Path -LiteralPath $researchRootCandidate).Path
}
catch {
    [Console]::Error.WriteLine("research_snapshot_root_unavailable")
    exit 2
}

$validManifestFound = $false
foreach ($directory in @(Get-ChildItem -LiteralPath $resolvedResearchRoot -Directory -ErrorAction Stop)) {
    $manifestPath = Join-Path $directory.FullName "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { continue }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
        if (
            $null -ne $manifest.snapshot_id -and
            $null -ne $manifest.manifest_hash -and
            $null -ne $manifest.tables
        ) {
            $validManifestFound = $true
            break
        }
    }
    catch { continue }
}
if (-not $validManifestFound -and -not $AllowUnavailable) {
    [Console]::Error.WriteLine("research_snapshot_manifest_unavailable_run_live_publish_first")
    exit 2
}

New-Item -ItemType Directory -Force -Path $liveRootCandidate | Out-Null
$resolvedLiveRoot = (Resolve-Path -LiteralPath $liveRootCandidate).Path
$liveRootParent = Split-Path -Parent $resolvedLiveRoot
$schedulerWorkspaceRoot = if ((Split-Path -Leaf $liveRootParent) -eq "live-datasets") {
    Split-Path -Parent (Split-Path -Parent $liveRootParent)
}
else {
    Split-Path -Parent $liveRootParent
}

$processTargets = @(
    @{
        name = "api"
        path = $apiPidPath
        process_names = @("python")
        executable_paths = @($pythonCommand)
        command_fragments = @("-m", "uvicorn", "stock_prediction.api.runtime:app")
    },
    @{
        name = "ui"
        path = $uiPidPath
        process_names = @("node")
        executable_paths = @($nodeCommand)
        command_fragments = @($viteCommand, "--config", "frontend/vite.config.ts")
    },
    @{
        name = "scheduler"
        path = $schedulerPidPath
        process_names = @("stock-prediction", "python")
        executable_paths = @($liveCommand, $pythonCommand)
        command_fragments = @("live-scheduler", "--workspace-root", $schedulerWorkspaceRoot)
    }
)

foreach ($target in $processTargets) {
    $stateStatus = Get-LocalProcessStateStatus `
        -Path $target.path `
        -Component $target.name `
        -ExpectedProcessNames $target.process_names `
        -ExpectedExecutablePaths $target.executable_paths `
        -ExpectedCommandFragments $target.command_fragments
    if ($stateStatus.status -eq "invalid") {
        [Console]::Error.WriteLine("invalid_local_pid_state")
        exit 2
    }
    if ($stateStatus.status -eq "active") {
        [Console]::Error.WriteLine("local_process_state_active_run_stop_first")
        exit 2
    }
    if ($stateStatus.status -eq "stale") {
        Remove-Item -LiteralPath $target.path -Force
    }
}

New-Item -ItemType Directory -Force -Path $runRoot, $logRoot | Out-Null

$previousResearchRoot = $env:PREDICTION_STOCK_RESEARCH_ROOT
$previousLiveRoot = $env:PREDICTION_STOCK_LIVE_ROOT
$previousStrictStartup = $env:PREDICTION_STOCK_STRICT_STARTUP
$previousAllowUnavailable = $env:PREDICTION_STOCK_ALLOW_UNAVAILABLE
$previousProxyTarget = $env:VITE_API_PROXY_TARGET
$previousPythonPath = $env:PYTHONPATH
$apiProcess = $null
$uiProcess = $null
$schedulerProcess = $null
try {
    $env:PREDICTION_STOCK_RESEARCH_ROOT = $resolvedResearchRoot
    $env:PREDICTION_STOCK_LIVE_ROOT = $resolvedLiveRoot
    $env:PREDICTION_STOCK_STRICT_STARTUP = "1"
    if ($AllowUnavailable) { $env:PREDICTION_STOCK_ALLOW_UNAVAILABLE = "1" }
    else { Remove-Item Env:PREDICTION_STOCK_ALLOW_UNAVAILABLE -ErrorAction SilentlyContinue }
    $env:VITE_API_PROXY_TARGET = "http://127.0.0.1:$ApiPort"
    $env:PYTHONPATH = (
        (Join-Path $repositoryRoot "backend\src") + [IO.Path]::PathSeparator + $repositoryRoot
    )
    $openDHistoryValidationExitCode = 1
    try {
        & $pythonCommand $sourceValidationCommand $repositoryRoot 2>$null | Out-Null
        $openDHistoryValidationExitCode = $LASTEXITCODE
    }
    catch { }
    if ($openDHistoryValidationExitCode -ne 0) {
        throw "opend_history_runtime_invalid"
    }

    if ($UiMode -eq "preview") {
        $buildProcess = Start-Process -FilePath $nodeCommand `
            -ArgumentList @($viteCommand, "build", "--config", "frontend/vite.config.ts") `
            -WorkingDirectory $repositoryRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logRoot "ui-build.stdout.log") `
            -RedirectStandardError (Join-Path $logRoot "ui-build.stderr.log") `
            -Wait -PassThru
        if ($buildProcess.ExitCode -ne 0) { throw "frontend_build_failed" }
    }

    $apiProcess = Start-Process -FilePath $pythonCommand `
        -ArgumentList @(
            "-m", "uvicorn", "stock_prediction.api.runtime:app",
            "--host", "127.0.0.1", "--port", "$ApiPort"
        ) `
        -WorkingDirectory $repositoryRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logRoot "api.stdout.log") `
        -RedirectStandardError (Join-Path $logRoot "api.stderr.log") `
        -PassThru
    Write-LocalProcessState -Path $apiPidPath -Component "api" -ProcessId $apiProcess.Id | Out-Null

    $uiArguments = @($viteCommand)
    if ($UiMode -eq "preview") { $uiArguments += "preview" }
    $uiArguments += @(
        "--config", "frontend/vite.config.ts",
        "--host", "127.0.0.1", "--port", "$UiPort", "--strictPort"
    )
    $uiProcess = Start-Process -FilePath $nodeCommand `
        -ArgumentList $uiArguments `
        -WorkingDirectory $repositoryRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logRoot "ui.stdout.log") `
        -RedirectStandardError (Join-Path $logRoot "ui.stderr.log") `
        -PassThru
    Write-LocalProcessState -Path $uiPidPath -Component "ui" -ProcessId $uiProcess.Id | Out-Null

    if ($startScheduler) {
        $schedulerProcess = Start-Process -FilePath $liveCommand `
            -ArgumentList @("live-scheduler", "--workspace-root", $schedulerWorkspaceRoot) `
            -WorkingDirectory $repositoryRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logRoot "scheduler.stdout.log") `
            -RedirectStandardError (Join-Path $logRoot "scheduler.stderr.log") `
            -PassThru
        Write-LocalProcessState `
            -Path $schedulerPidPath `
            -Component "scheduler" `
            -ProcessId $schedulerProcess.Id | Out-Null
    }

    if (-not (Wait-LocalUrl "http://127.0.0.1:$ApiPort/api/v1/health" 30)) {
        throw "api_start_timeout"
    }
    if (
        $startScheduler -and
        -not (Wait-EodWorker "http://127.0.0.1:$ApiPort/api/v1/operations/eod-update/worker" 30)
    ) {
        throw "eod_worker_start_timeout"
    }
    if (-not (Wait-LocalUrl "http://127.0.0.1:$UiPort/" 30)) {
        throw "ui_start_timeout"
    }

    [ordered]@{
        status = "started"
        api_url = "http://127.0.0.1:$ApiPort"
        ui_url = "http://127.0.0.1:$UiPort"
        ui_mode = $UiMode
        api_pid = $apiProcess.Id
        ui_pid = $uiProcess.Id
        scheduler_pid = if ($null -eq $schedulerProcess) { $null } else { $schedulerProcess.Id }
        scheduler_enabled = $startScheduler
        research_root = $resolvedResearchRoot
        live_root = $resolvedLiveRoot
        prediction_snapshot_available = $validManifestFound
    } | ConvertTo-Json -Compress
}
catch {
    $failureMessage = $_.Exception.Message
    if ($null -ne $schedulerProcess) { Stop-Process -Id $schedulerProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($null -ne $uiProcess) { Stop-Process -Id $uiProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($null -ne $apiProcess) { Stop-Process -Id $apiProcess.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $apiPidPath, $uiPidPath, $schedulerPidPath -Force -ErrorAction SilentlyContinue
    if ($failureMessage -in @("opend_history_runtime_invalid", "frontend_build_failed")) {
        [Console]::Error.WriteLine($failureMessage)
    }
    else {
        [Console]::Error.WriteLine("local_start_failed")
    }
    exit 2
}
finally {
    if ($null -eq $previousResearchRoot) {
        Remove-Item Env:PREDICTION_STOCK_RESEARCH_ROOT -ErrorAction SilentlyContinue
    }
    else { $env:PREDICTION_STOCK_RESEARCH_ROOT = $previousResearchRoot }
    if ($null -eq $previousLiveRoot) {
        Remove-Item Env:PREDICTION_STOCK_LIVE_ROOT -ErrorAction SilentlyContinue
    }
    else { $env:PREDICTION_STOCK_LIVE_ROOT = $previousLiveRoot }
    if ($null -eq $previousStrictStartup) {
        Remove-Item Env:PREDICTION_STOCK_STRICT_STARTUP -ErrorAction SilentlyContinue
    }
    else { $env:PREDICTION_STOCK_STRICT_STARTUP = $previousStrictStartup }
    if ($null -eq $previousAllowUnavailable) {
        Remove-Item Env:PREDICTION_STOCK_ALLOW_UNAVAILABLE -ErrorAction SilentlyContinue
    }
    else { $env:PREDICTION_STOCK_ALLOW_UNAVAILABLE = $previousAllowUnavailable }
    if ($null -eq $previousProxyTarget) {
        Remove-Item Env:VITE_API_PROXY_TARGET -ErrorAction SilentlyContinue
    }
    else { $env:VITE_API_PROXY_TARGET = $previousProxyTarget }
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else { $env:PYTHONPATH = $previousPythonPath }
}
