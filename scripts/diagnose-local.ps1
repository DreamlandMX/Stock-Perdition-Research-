param(
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 18766,
    [ValidateRange(1024, 65535)]
    [int]$UiPort = 18765,
    [string]$RunRoot = ""
)

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

function Get-UrlStatus {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return [ordered]@{ reachable = $true; status_code = $response.StatusCode }
    }
    catch {
        $statusCode = $null
        if ($null -ne $_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        return [ordered]@{ reachable = $false; status_code = $statusCode }
    }
}

function Get-ManagedProcessStatus {
    param([hashtable]$Target)
    $stateStatus = Get-LocalProcessStateStatus `
        -Path $Target.path `
        -Component $Target.name `
        -ExpectedProcessNames $Target.process_names `
        -ExpectedExecutablePaths $Target.executable_paths `
        -ExpectedCommandFragments $Target.command_fragments
    return [ordered]@{
        pid_state = $stateStatus.status
        pid_format = $stateStatus.format
        running = ($stateStatus.status -eq "active")
        pid = $stateStatus.pid
    }
}

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

$drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($repositoryRoot).TrimEnd("\").TrimEnd(":"))
$pythonVersion = $null
if (Test-Path -LiteralPath $pythonCommand -PathType Leaf) {
    $pythonVersion = (& $pythonCommand --version 2>&1).ToString().Trim()
}

[ordered]@{
    status = "diagnostic"
    expected = [ordered]@{
        python = "3.11.x"
        node = $expectedNodeVersion
        host = "127.0.0.1"
    }
    detected = [ordered]@{
        python = $pythonVersion
        api = Get-ManagedProcessStatus $targets[0]
        ui = Get-ManagedProcessStatus $targets[1]
        scheduler = Get-ManagedProcessStatus $targets[2]
        api_health = Get-UrlStatus "http://127.0.0.1:$ApiPort/api/v1/health"
        ui_health = Get-UrlStatus "http://127.0.0.1:$UiPort/"
        disk_free_bytes = [int64]$drive.Free
    }
} | ConvertTo-Json -Depth 5
