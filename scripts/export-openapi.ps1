$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pythonCommand = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$outputPath = Join-Path $repositoryRoot "var\contracts\openapi.json"

if (-not (Test-Path -LiteralPath $pythonCommand)) {
    [Console]::Error.WriteLine("python_toolchain_unavailable")
    exit 2
}

$outputDirectory = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$exporter = @'
import json
import sys
from pathlib import Path

from stock_prediction.api.app import create_app

target = Path(sys.argv[1])
document = create_app().openapi()
payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
target.write_text(payload, encoding='utf-8', newline='\n')
'@

Push-Location $repositoryRoot
try {
    & $pythonCommand -c $exporter $outputPath
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
