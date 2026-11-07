param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("Start", "Status", "Resume", "Stop", "PublishResearch")]
    [string]$Action,

    [string]$RunId,

    [string]$WorkspaceRoot
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
    $WorkspaceRoot = Split-Path -Parent $PSScriptRoot
}
$WorkspaceRoot = (Resolve-Path -LiteralPath $WorkspaceRoot).Path
$pythonCommand = Join-Path $WorkspaceRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonCommand -PathType Leaf)) {
    [Console]::Error.WriteLine("python_toolchain_unavailable")
    exit 2
}

$pythonArguments = @(
    "-m",
    "stock_prediction.live.training_job",
    $Action,
    "--workspace-root",
    $WorkspaceRoot
)
if (-not [string]::IsNullOrWhiteSpace($RunId)) {
    $pythonArguments += @("--run-id", $RunId)
}

& $pythonCommand @pythonArguments
exit $LASTEXITCODE
