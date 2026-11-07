$ErrorActionPreference = "Stop"

& uv run stock-prediction probe-opend @args
exit $LASTEXITCODE
