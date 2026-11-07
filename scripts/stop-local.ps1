param([string]$RunRoot = "")

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$runRoot = if ($RunRoot.Trim() -eq "") { Join-Path $repositoryRoot "var\run" } else { $RunRoot }
$pythonCommand = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$liveCommand = Join-Path $repositoryRoot ".venv\Scripts\stock-prediction.exe"
$expectedNodeVersion = (Get-Content -LiteralPath (Join-Path $repositoryRoot ".node-version") -Raw).Trim()
$winGetNodeRoot = Join-Path $env:LOCALAPPDATA (
    "Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\" +
    "node-v$expectedNodeVersion-win-x64"
)
$nodeCommand = Join-Path $winGetNodeRoot "node.exe"
$viteCommand = Join-Path $repositoryRoot "node_modules\vite\bin\vite.js"
. (Join-Path $PSScriptRoot "local-process-state.ps1")

$targets = @(
    @{
        name = "api"
        path = (Join-Path $runRoot "api.pid")
        process_names = @("python")
        executable_paths = @($pythonCommand)
        command_fragments = @("-m", "uvicorn", "stock_prediction.api.runtime:app")
    },
    @{
        name = "ui"
        path = (Join-Path $runRoot "ui.pid")
        process_names = @("node")
        executable_paths = @($nodeCommand)
        command_fragments = @($viteCommand, "--config", "frontend/vite.config.ts")
    },
    @{
        name = "scheduler"
        path = (Join-Path $runRoot "scheduler.pid")
        process_names = @("stock-prediction", "python")
        executable_paths = @($liveCommand, $pythonCommand)
        command_fragments = @("live-scheduler", "--workspace-root", $repositoryRoot)
    }
)
$stopped = @()
$staleComponents = @()

function Get-ManagedProcessTree {
    param([int]$ProcessId, [string]$ProcessName = "")

    $entries = @()
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId")
    foreach ($child in $children) {
        $entries += @(Get-ManagedProcessTree `
            -ProcessId ([int]$child.ProcessId) `
            -ProcessName ([string]$child.Name)
        )
    }
    $entries += [pscustomobject]@{
        process_id = $ProcessId
        process_name = $ProcessName
    }
    return $entries
}

function Wait-ManagedProcessExit {
    param([int]$ProcessId, [int]$TimeoutSeconds = 5)

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 100
    }
    if ($null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        throw "managed_process_exit_timeout"
    }
}

function Stop-ManagedProcessTree {
    param([int]$ProcessId, [int]$TimeoutSeconds = 5)

    $processTree = @(Get-ManagedProcessTree -ProcessId $ProcessId)
    foreach ($entry in $processTree) {
        if ([string]::Equals(
            [IO.Path]::GetFileNameWithoutExtension([string]$entry.process_name),
            "conhost",
            [StringComparison]::OrdinalIgnoreCase
        )) {
            continue
        }
        if ($null -eq (Get-Process -Id ([int]$entry.process_id) -ErrorAction SilentlyContinue)) {
            continue
        }
        Stop-Process -Id ([int]$entry.process_id) -Force -ErrorAction Stop
        Wait-ManagedProcessExit -ProcessId ([int]$entry.process_id) -TimeoutSeconds $TimeoutSeconds
    }
    foreach ($entry in $processTree) {
        Wait-ManagedProcessExit -ProcessId ([int]$entry.process_id) -TimeoutSeconds $TimeoutSeconds
    }
}

foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target.path -PathType Leaf)) { continue }
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
    if ($stateStatus.status -eq "stale") {
        Remove-Item -LiteralPath $target.path -Force
        $staleComponents += $target.name
        continue
    }
    try {
        Stop-ManagedProcessTree -ProcessId ([int]$stateStatus.pid)
        Remove-Item -LiteralPath $target.path -Force
        $stopped += $target.name
    }
    catch {
        [Console]::Error.WriteLine("local_process_stop_failed")
        exit 2
    }
}

[ordered]@{
    status = "stopped"
    components = $stopped
    stale_components = $staleComponents
} | ConvertTo-Json -Compress
