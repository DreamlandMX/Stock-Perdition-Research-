$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
$expectedNodeVersion = (Get-Content -LiteralPath (Join-Path $repositoryRoot ".node-version") -Raw).Trim()
$winGetNodeRoot = Join-Path $env:LOCALAPPDATA (
    "Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\" +
    "node-v$expectedNodeVersion-win-x64"
)

if (
    (Test-Path -LiteralPath (Join-Path $winGetNodeRoot "node.exe")) -and
    (Test-Path -LiteralPath (Join-Path $winGetNodeRoot "npm.cmd"))
) {
    $nodeRoot = $winGetNodeRoot
}
else {
    $resolvedNode = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($null -eq $resolvedNode) {
        [Console]::Error.WriteLine("node_toolchain_unavailable")
        exit 2
    }
    $nodeRoot = Split-Path -Parent $resolvedNode.Source
}

$nodeCommand = Join-Path $nodeRoot "node.exe"
$npmCommand = Join-Path $nodeRoot "npm.cmd"
if (
    -not (Test-Path -LiteralPath $nodeCommand) -or
    -not (Test-Path -LiteralPath $npmCommand)
) {
    [Console]::Error.WriteLine("node_toolchain_unavailable")
    exit 2
}

$actualNodeVersion = (& $nodeCommand --version).Trim()
if ($actualNodeVersion -ne "v$expectedNodeVersion") {
    [Console]::Error.WriteLine("node_version_mismatch")
    exit 2
}

Push-Location $repositoryRoot
try {
    $requiredM5Assets = @(
        "playwright.config.ts",
        "frontend\e2e\tsconfig.json"
    )
    foreach ($asset in $requiredM5Assets) {
        if (-not (Test-Path -LiteralPath (Join-Path $repositoryRoot $asset))) {
            [Console]::Error.WriteLine("m5_e2e_toolchain_asset_missing")
            exit 2
        }
    }

    & $npmCommand ci --ignore-scripts
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $npmCommand run contracts:check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $npmCommand run typecheck
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $npmCommand run lint
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $npmCommand run format:check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $npmCommand test
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
