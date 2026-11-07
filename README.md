# Stock Prediction Research

A desktop research platform for stock and options analysis, model experiments, and backtest review.

[English](#english) | [中文](#中文)

## English

### Overview

Stock Prediction Research connects market-data ingestion, point-in-time features, model evaluation, and a desktop research interface. It helps inspect market conditions, compare research signals, and trace predictions back to their data and model evidence.

This is a source release. It does not include private market data, trained model weights, or real training logs. Cloning the repository does not reproduce an existing live workspace.

### Main capabilities

| Area             | Capabilities                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------- |
| Market overview  | Market and sector views, price history, research signals, and data freshness indicators.                      |
| Stock research   | Instrument screening, forecast intervals, probability estimates, and supporting evidence.                     |
| Options research | Option-chain analytics, implied moves, volatility and skew views, structure maps, and pulse sessions.         |
| Data pipeline    | Provider adapters, immutable snapshots, DuckDB catalogs, Parquet artifacts, and quality checks.               |
| Model research   | Point-in-time features and labels, baseline comparisons, walk-forward evaluation, calibration, and backtests. |
| Operations       | Controlled data updates, training and model-promotion workflows, worker status, and health checks.            |

The interface includes English and Chinese text. Development targets desktop use, mainly at 1280 px and 1920 px widths.

### Technology and structure

- **Backend:** Python, FastAPI, Pydantic, DuckDB, pandas, and PyArrow.
- **Models:** scikit-learn, LightGBM, CatBoost, and PyTorch.
- **Frontend:** React, TypeScript, Vite, TanStack Query, ECharts, and Lightweight Charts.
- **Checks:** pytest, Vitest, TypeScript, and Playwright test sources.

```text
backend/src/stock_prediction/  API, data pipelines, models, and operations
backend/sql/                  SQL definitions
backend/tests/                Backend tests and synthetic fixtures
frontend/src/                 Desktop research interface
frontend/e2e/                 Browser test sources
scripts/                      Setup checks and runtime tools
tooling/                      TypeScript contract checks
```

### Requirements

- Windows and PowerShell for the managed startup scripts.
- Python 3.11 and `uv`.
- Node.js 24.18.0, as pinned in `.node-version`.
- Your own provider access and valid datasets for live research.

The Windows dependency configuration uses the CUDA 12.8 build of PyTorch 2.8. Some training workflows have additional GPU checks. Installing dependencies does not establish that a particular machine meets those checks. Other operating systems have not been verified for this release.

### Setup

```powershell
git clone https://github.com/DreamlandMX/Stock-Perdition-Research-.git
Set-Location Stock-Perdition-Research-
uv sync --locked --python 3.11
```

Use the pinned Node version. The following example resolves its standard WinGet installation relative to the current user's environment:

```powershell
$nodeLts = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.18.0-win-x64'
$env:Path = "$nodeLts;$env:Path"
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'
& "$nodeLts\npm.cmd" ci --ignore-scripts
& "$nodeLts\npm.cmd" run typecheck
```

The managed application launcher also expects this WinGet Node layout. The separate toolchain wrapper, `.\scripts\check-typescript.ps1`, can fall back to Node on `PATH` and checks its version.

### Run the desktop application

First prepare your own data-provider configuration and a valid published research snapshot. The managed launcher checks these prerequisites and fails if the required data is missing. Its `-ResearchRoot` and `-LiveRoot` parameters select your research and live-data directories.

Run these commands from the repository root:

```powershell
# Start the API, frontend, and scheduler.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-local.ps1

# Inspect service status.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-local.ps1

# Stop the managed services.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop-local.ps1
```

The UI uses `127.0.0.1:18765`; the API uses `127.0.0.1:18766`. Open `http://127.0.0.1:18765/overview` after startup succeeds.

The scheduler is enabled by default. Manual data updates, training, and model promotion use explicit controls. The scheduler may run configured scheduled jobs. OpenD authenticates through its own application; other provider credentials belong in your environment, outside version control. Historical data requests may consume provider quotas.

### Verification

After setup, run the unit tests and build:

```powershell
uv run pytest
npm run typecheck
npm test
npm run build
```

Additional static checks are available through `uv run ruff check .`, `uv run mypy backend/src`, and `npm run lint`. Use `npm run contracts:check` to check generated API types.

External integration tests require explicit opt-in and your own services. Playwright test sources are included, but screenshot baselines are not. Generate and review baselines locally before using screenshot comparisons; generated images remain ignored.

### What is included

| Included                                           | Excluded                                                                |
| -------------------------------------------------- | ----------------------------------------------------------------------- |
| Backend and frontend source code                   | Private provider or account data                                        |
| Training, evaluation, and backtest implementations | Real training logs, run histories, and evaluation artifacts             |
| SQL, synthetic fixtures, and tests                 | Trained model weights and runtime snapshots                             |
| Dependency locks and runtime scripts               | Credentials, private keys, and local configuration                      |
| General setup instructions                         | Screenshots, browser recordings, internal reports, and agent workspaces |

Keep generated data and model artifacts in the ignored runtime directories. Do not force-add ignored files to a commit.

### Research limitations

The project does not place orders or manage positions. Forecasts, probabilities, and backtests are research outputs, not investment advice or evidence of future returns. Synthetic tests check implementation behavior; they do not establish predictive performance on live markets.

---

## 中文

### 项目简介

Stock Prediction Research 是一个面向桌面端的股票与期权研究平台。它连接行情采集、点时特征、模型评估和研究界面，用于查看市场状态、比较研究信号，以及追溯预测所依赖的数据和模型证据。

本仓库发布的是源代码，**不包含真实行情、已训练模型权重或真实训练日志**。克隆仓库并不等于恢复一个已经运行的研究工作区。

### 主要功能

| 模块     | 功能                                                           |
| -------- | -------------------------------------------------------------- |
| 市场总览 | 市场与行业视图、价格历史、研究信号和数据时效状态。             |
| 股票研究 | 标的筛选、预测区间、概率估计和相关证据。                       |
| 期权研究 | 期权链分析、隐含波动范围、波动率与偏斜、结构图和脉冲会话。     |
| 数据管道 | 数据源适配、不可变快照、DuckDB 目录、Parquet 产物和质量检查。  |
| 模型研究 | 点时特征与标签、基线比较、滚动验证、概率校准和回测。           |
| 运行管理 | 受控的数据更新、训练与模型晋级流程，以及执行器状态和健康检查。 |

界面提供中英文文本。开发以桌面端为目标，主要检查 1280 px 和 1920 px 宽度。

### 技术结构与环境

后端使用 Python、FastAPI、DuckDB、pandas 和 PyArrow。模型研究使用 scikit-learn、LightGBM、CatBoost 和 PyTorch。前端使用 React、TypeScript、Vite、TanStack Query、ECharts 和 Lightweight Charts。

环境要求：

- Windows 与 PowerShell，用于托管启动脚本。
- Python 3.11 和 `uv`。
- `.node-version` 中固定的 Node.js 24.18.0。
- 真实研究所需的自有数据源权限及有效数据集。

Windows 依赖配置使用 PyTorch 2.8 的 CUDA 12.8 版本。部分训练流程另有 GPU 检查，安装成功不代表机器满足全部训练要求。本次发布未验证其他操作系统。

### 安装与运行

按上方英文 **Setup** 部分克隆项目、安装 Python 依赖，并使用固定版本的 Node 安装前端依赖。示例通过环境变量定位通用安装目录，不包含个人电脑路径。

托管启动脚本还依赖该 WinGet Node 安装布局。独立的 `.\scripts\check-typescript.ps1` 工具链检查脚本可以回退到 `PATH` 中的 Node，并核对版本。

启动前，需要配置自己的数据源并准备有效的研究快照。没有这些数据时，启动前检查会报错。可以通过 `-ResearchRoot` 和 `-LiveRoot` 参数指定自己的数据目录。

在仓库根目录运行：

```powershell
# 启动 API、前端和调度器。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-local.ps1

# 检查服务状态。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-local.ps1

# 停止托管服务。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop-local.ps1
```

启动成功后，访问 `http://127.0.0.1:18765/overview`。API 使用 `127.0.0.1:18766`。

调度器默认启用。手动数据更新、训练和模型晋级需要通过明确的控制入口执行；调度器可能执行已配置的定时任务。OpenD 在其自身应用中完成登录；其他数据源凭据应通过环境配置提供，不得提交到仓库。历史行情请求可能消耗数据源额度。

### 测试与构建

安装完成后，可以运行：

```powershell
uv run pytest
npm run typecheck
npm test
npm run build
```

其他静态检查包括 `uv run ruff check .`、`uv run mypy backend/src` 和 `npm run lint`。`npm run contracts:check` 用于检查生成的 API 类型。

外部集成测试需要显式启用，并使用自己的服务。仓库保留 Playwright 测试源码，但不提供截图基线。运行截图比较前，需要在自己的环境中生成并检查基线；生成的图片仍被 Git 忽略。

### 公开内容边界

**包含：** 前后端源码、训练与评估实现、回测实现、SQL、合成测试夹具、测试代码、依赖锁文件和通用运行脚本。

**不包含：** 真实训练日志与历史记录、模型权重、真实行情、运行快照、账号数据、凭据、私钥、个人配置、截图、浏览器录制、内部报告或 Agent 工作目录。

生成的数据与模型产物应保存在已忽略的运行目录中，不要强制加入 Git 提交。

### 研究限制

本项目不下单、不管理仓位。预测、概率与回测结果仅用于研究，不构成投资建议，也不能证明未来收益。合成测试用于检查实现行为，不能证明模型在真实市场上具备预测优势。
