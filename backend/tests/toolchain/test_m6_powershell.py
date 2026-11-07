import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _run_powershell_file(
    script: Path, *arguments: str, cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _write_history_module(package_root: Path, *, has_iter_pages: bool) -> None:
    (package_root / "opend").mkdir(parents=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "opend" / "__init__.py").write_text("", encoding="utf-8")
    reader_body = (
        "    def iter_pages(self):\n        return iter(())\n"
        if has_iter_pages
        else "    pass\n"
    )
    (package_root / "opend" / "history.py").write_text(
        f"class OpenDHistoryReader:\n{reader_body}",
        encoding="utf-8",
    )


def _start_harness(
    tmp_path: Path,
    *,
    history_outside_backend: bool = False,
    history_has_iter_pages: bool = False,
) -> Path:
    repository_root = tmp_path / "worktree"
    scripts_root = repository_root / "scripts"
    scripts_root.mkdir(parents=True)
    for script_name in (
        "start-local.ps1",
        "local-process-state.ps1",
        "validate-local-source.py",
    ):
        shutil.copy2(
            REPOSITORY_ROOT / "scripts" / script_name, scripts_root / script_name
        )

    package_root = (
        repository_root / "stock_prediction"
        if history_outside_backend
        else repository_root / "backend" / "src" / "stock_prediction"
    )
    _write_history_module(
        package_root,
        has_iter_pages=history_outside_backend or history_has_iter_pages,
    )
    shutil.copy2(REPOSITORY_ROOT / ".node-version", repository_root / ".node-version")
    (repository_root / "node_modules" / "vite" / "bin").mkdir(parents=True)
    (repository_root / "node_modules" / "vite" / "bin" / "vite.js").write_text(
        "// test placeholder\n", encoding="utf-8"
    )
    junction = subprocess.run(
        [
            "cmd.exe",
            "/c",
            "mklink",
            "/J",
            str(repository_root / ".venv"),
            str(REPOSITORY_ROOT / ".venv"),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert junction.returncode == 0, junction.stderr
    return repository_root


def test_source_validator_accepts_current_backend_with_iter_pages(
    tmp_path: Path,
) -> None:
    repository_root = _start_harness(tmp_path, history_has_iter_pages=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(repository_root / "backend" / "src"), str(repository_root))
    )

    result = subprocess.run(
        [
            str(repository_root / ".venv" / "Scripts" / "python.exe"),
            str(repository_root / "scripts" / "validate-local-source.py"),
            str(repository_root),
        ],
        cwd=repository_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert result.stderr.strip() == ""


def test_start_script_rejects_stale_opend_runtime_before_process_launch(
    tmp_path: Path,
) -> None:
    repository_root = _start_harness(tmp_path)

    result = _run_powershell_file(
        repository_root / "scripts" / "start-local.ps1",
        "-AllowUnavailable",
        "-DisableScheduler",
        "-ApiPort",
        "40111",
        "-UiPort",
        "40112",
        cwd=repository_root,
    )

    assert result.returncode == 2
    assert result.stdout.strip() == ""
    assert result.stderr.strip() == "opend_history_runtime_invalid"
    assert not any((repository_root / "var" / "run").glob("*.pid"))


def test_start_script_rejects_opend_module_outside_worktree_backend_src(
    tmp_path: Path,
) -> None:
    repository_root = _start_harness(tmp_path, history_outside_backend=True)

    result = _run_powershell_file(
        repository_root / "scripts" / "start-local.ps1",
        "-AllowUnavailable",
        "-DisableScheduler",
        "-ApiPort",
        "40113",
        "-UiPort",
        "40114",
        cwd=repository_root,
    )

    assert result.returncode == 2
    assert result.stdout.strip() == ""
    assert result.stderr.strip() == "opend_history_runtime_invalid"
    assert not any((repository_root / "var" / "run").glob("*.pid"))


def test_preview_build_failure_does_not_launch_services(tmp_path: Path) -> None:
    repository_root = _start_harness(tmp_path, history_has_iter_pages=True)
    (repository_root / "node_modules" / "vite" / "bin" / "vite.js").write_text(
        "process.stdout.write(JSON.stringify(process.argv.slice(2))); process.exit(7);\n",
        encoding="utf-8",
    )

    result = _run_powershell_file(
        repository_root / "scripts" / "start-local.ps1",
        "-AllowUnavailable",
        "-DisableScheduler",
        "-UiMode",
        "preview",
        "-ApiPort",
        "40115",
        "-UiPort",
        "40116",
        cwd=repository_root,
    )

    assert result.returncode == 2
    assert result.stdout.strip() == ""
    assert result.stderr.strip() == "frontend_build_failed"
    assert json.loads(
        (repository_root / "var" / "log" / "ui-build.stdout.log").read_text()
    ) == [
        "build",
        "--config",
        "frontend/vite.config.ts",
    ]
    assert not any((repository_root / "var" / "run").glob("*.pid"))
    assert not (repository_root / "var" / "log" / "api.stdout.log").exists()


@pytest.mark.parametrize("ui_mode", ["dev", "preview"])
def test_ui_mode_uses_managed_vite_arguments_before_any_service_launch(
    tmp_path: Path,
    ui_mode: str,
) -> None:
    repository_root = _start_harness(tmp_path, history_has_iter_pages=True)
    (repository_root / "scripts" / "local-process-state.ps1").write_text(
        'function Get-LocalProcessStateStatus { return @{ status = "missing" } }\n'
        "function Write-LocalProcessState {}\n",
        encoding="utf-8",
    )
    script = f"""
$global:launches = @()
function Start-Process {{
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle,
          $RedirectStandardOutput, $RedirectStandardError, [switch]$Wait, [switch]$PassThru)
    $global:launches += [ordered]@{{ executable = $FilePath; arguments = $ArgumentList; wait = [bool]$Wait }}
    return [pscustomobject]@{{ Id = 10000 + $global:launches.Count; ExitCode = 0 }}
}}
function Invoke-WebRequest {{ return [pscustomobject]@{{ StatusCode = 200 }} }}
$startup = & '{repository_root / "scripts" / "start-local.ps1"}' `
    -AllowUnavailable -DisableScheduler -UiMode '{ui_mode}' -ApiPort 40117 -UiPort 40118 | ConvertFrom-Json
[ordered]@{{ startup = $startup; launches = $global:launches }} | ConvertTo-Json -Depth 5 -Compress
"""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["startup"]["ui_mode"] == ui_mode
    assert payload["startup"]["scheduler_enabled"] is False
    launches = payload["launches"]
    assert len(launches) == (3 if ui_mode == "preview" else 2)
    if ui_mode == "preview":
        assert launches[0]["wait"] is True
        assert launches[0]["arguments"][1:] == [
            "build",
            "--config",
            "frontend/vite.config.ts",
        ]
    assert launches[-2]["arguments"][:3] == [
        "-m",
        "uvicorn",
        "stock_prediction.api.runtime:app",
    ]
    ui_arguments = launches[-1]["arguments"]
    assert ui_arguments[0].endswith(r"node_modules\vite\bin\vite.js")
    assert ui_arguments[1:] == (
        (["preview"] if ui_mode == "preview" else [])
        + [
            "--config",
            "frontend/vite.config.ts",
            "--host",
            "127.0.0.1",
            "--port",
            "40118",
            "--strictPort",
        ]
    )


def _write_json_state(path: Path, component: str, pid: int) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "component": component,
                "pid": pid,
                "process_name": "python.exe",
                "executable_path": r"C:\\not-a-real-python.exe",
                "started_at_utc": "2026-08-10T17:29:47.0000000Z",
                "command_line_sha256": "d0b5f2d6cf2a2f006e71742f25c7c2577f31a1aa8c807d30f4845443bb4a4b98",
                "instance_id": "00000000-0000-4000-8000-000000000001",
            }
        ),
        encoding="utf-8",
    )


def test_diagnostic_script_reports_json_legacy_and_missing_process_states(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    _write_json_state(run_root / "api.pid", "api", os.getpid())
    (run_root / "ui.pid").write_text(f"{os.getpid()}\n", encoding="ascii")

    result = _run_powershell_file(
        REPOSITORY_ROOT / "scripts" / "diagnose-local.ps1",
        "-RunRoot",
        str(run_root),
        cwd=REPOSITORY_ROOT,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "diagnostic"
    assert set(payload["detected"]) == {
        "python",
        "api",
        "ui",
        "scheduler",
        "api_health",
        "ui_health",
        "disk_free_bytes",
    }
    assert payload["detected"]["api"] == {
        "pid_state": "stale",
        "pid_format": "json",
        "running": False,
        "pid": os.getpid(),
    }
    assert payload["detected"]["ui"] == {
        "pid_state": "stale",
        "pid_format": "legacy",
        "running": False,
        "pid": os.getpid(),
    }
    assert payload["detected"]["scheduler"] == {
        "pid_state": "missing",
        "pid_format": None,
        "running": False,
        "pid": None,
    }
    assert "not-a-real-python" not in result.stdout


def test_diagnostic_default_ports_match_local_startup_contract() -> None:
    script = f"""
$global:requestedUrls = @()
function Invoke-WebRequest {{
    param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec)
    $global:requestedUrls += $Uri
    return [pscustomobject]@{{ StatusCode = 200 }}
}}
$diagnostic = & '{REPOSITORY_ROOT / "scripts" / "diagnose-local.ps1"}' | ConvertFrom-Json
[ordered]@{{ requested_urls = $global:requestedUrls; diagnostic = $diagnostic }} | ConvertTo-Json -Depth 5 -Compress
"""

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["requested_urls"] == [
        "http://127.0.0.1:18766/api/v1/health",
        "http://127.0.0.1:18765/",
    ]
    assert payload["diagnostic"]["detected"]["api_health"] == {
        "reachable": True,
        "status_code": 200,
    }
    assert payload["diagnostic"]["detected"]["ui_health"] == {
        "reachable": True,
        "status_code": 200,
    }
