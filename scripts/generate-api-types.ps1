param(
    [switch] $Check
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$openApiPath = Join-Path $repositoryRoot "var\contracts\openapi.json"
$schemaPath = Join-Path $repositoryRoot "frontend\src\api\generated\schema.d.ts"
$generatorCommand = Join-Path $repositoryRoot "node_modules\.bin\openapi-typescript.cmd"

if (-not (Test-Path -LiteralPath $generatorCommand)) {
    [Console]::Error.WriteLine("openapi_typescript_unavailable")
    exit 2
}

& (Join-Path $PSScriptRoot "export-openapi.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $Check) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $schemaPath) -Force | Out-Null
    & $generatorCommand $openApiPath --output $schemaPath
    exit $LASTEXITCODE
}

$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) (
    "prediction-stock-openapi-" + [System.Guid]::NewGuid().ToString("N")
)
$temporarySchema = Join-Path $temporaryDirectory "schema.d.ts"
New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null

try {
    & $generatorCommand $openApiPath --output $temporarySchema
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if (-not (Test-Path -LiteralPath $schemaPath)) {
        [Console]::Error.WriteLine("generated_api_types_missing")
        exit 1
    }

    & git -c core.safecrlf=false diff --no-index --exit-code -- $schemaPath $temporarySchema
    $diffExitCode = $LASTEXITCODE
    if ($diffExitCode -eq 0) { exit 0 }
    if ($diffExitCode -eq 1) {
        [Console]::Error.WriteLine("generated_api_types_stale")
        exit 1
    }
    exit $diffExitCode
}
finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
}
