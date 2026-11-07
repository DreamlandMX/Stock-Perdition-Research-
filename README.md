# Stock Prediction Research

A local research platform for stock and option data, reproducible model experiments, and a desktop research interface. Research only; it does not place orders or guarantee investment returns.

## Requirements

- Windows and PowerShell
- Python 3.11 and uv
- Node.js 24.18.0 (see `.node-version`)
- Your own market-data access for live operations; offline unit tests use fixtures

## Setup and checks

From the repository root:

```powershell
uv sync --locked --python 3.11
uv run pytest
uv run ruff check .
uv run mypy backend/src

$nodeLts = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.18.0-win-x64'
$env:Path = "$nodeLts;$env:Path"
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'
& "$nodeLts\npm.cmd" ci --ignore-scripts
& "$nodeLts\npm.cmd" run typecheck
& "$nodeLts\npm.cmd" run lint
& "$nodeLts\npm.cmd" test
& "$nodeLts\npm.cmd" run build
```

Alternatively, use `.\scripts\check-typescript.ps1`, which checks the pinned Node version and supports Node installed on PATH.

## Local application

Configure your own data sources and research roots before starting the live application. The API binds to `127.0.0.1:18766`; the UI uses `127.0.0.1:18765`.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-local.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop-local.ps1
```

The startup script enables the scheduler by default. Data updates, training, and model promotion are controlled operations: do not trigger them during setup or routine tests. OpenD connects through its loopback service and authenticates outside this repository. Other providers use your own environment configuration. Never commit credentials or private market data.

## Public source boundary

This source release includes backend and frontend code, SQL, synthetic test fixtures, scripts, and dependency locks. It excludes private runtime data, models, logs, screenshots, browser recordings, agent workspaces, internal reports, and generated build output.

Playwright test sources and configuration are included. Screenshot baselines are intentionally absent. Generate and review baselines locally before running screenshot comparisons; they remain ignored. Ordinary unit tests do not require generating screenshots. External integration tests require explicit opt-in and your own services; historical requests can consume data quotas.

Backtests and probability estimates are research evidence, not promises of future performance. Live use requires independent validation of data access, configuration, and model behavior.
