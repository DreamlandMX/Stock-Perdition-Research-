import json
import os
import subprocess
import time
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPOSITORY_ROOT / "scripts" / "local-process-state.ps1"


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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


def _run_stop_script(run_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "scripts" / "stop-local.ps1"),
            "-RunRoot",
            str(run_root),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _run_stop_with_stop_process_override(
    run_root: Path, implementation: str
) -> subprocess.CompletedProcess[str]:
    return _run_powershell(
        f"""
function Stop-Process {{
    param([int]$Id, [switch]$Force, [object]$ErrorAction)
    {implementation}
}}
& '{REPOSITORY_ROOT / "scripts" / "stop-local.ps1"}' -RunRoot '{run_root}'
"""
    )


def _node_command() -> Path:
    node_version = (REPOSITORY_ROOT / ".node-version").read_text(encoding="utf-8").strip()
    return (
        Path(os.environ["LOCALAPPDATA"])
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / f"node-v{node_version}-win-x64"
        / "node.exe"
    )


def _start_managed_ui_process(run_root: Path) -> subprocess.Popen[bytes]:
    node_command = _node_command()
    vite_command = REPOSITORY_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
    process = subprocess.Popen(
        [
            str(node_command),
            "-e",
            "setTimeout(() => {}, 60000)",
            str(vite_command),
            "--config",
            "frontend/vite.config.ts",
        ],
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    state_path = run_root / "ui.pid"
    write_state = f"""
$ErrorActionPreference = "Stop"
. '{MODULE_PATH}'
Write-LocalProcessState -Path '{state_path}' -Component 'ui' -ProcessId {process.pid} | Out-Null
"""
    for _ in range(10):
        result = _run_powershell(write_state)
        if result.returncode == 0:
            return process
        time.sleep(0.1)
    process.kill()
    process.wait(timeout=5)
    raise AssertionError(result.stderr)


def _status_script(state_path: Path, snapshot: dict[str, object]) -> str:
    snapshot_json = json.dumps(snapshot, separators=(",", ":"))
    return f"""
$ErrorActionPreference = "Stop"
. '{MODULE_PATH}'
$snapshot = '{snapshot_json}' | ConvertFrom-Json
Get-LocalProcessStateStatus `
    -Path '{state_path}' `
    -Component 'ui' `
    -ExpectedProcessNames @('node') `
    -ExpectedExecutablePaths @('C:\\tools\\node.exe') `
    -ExpectedCommandFragments @('vite.js', '--port', '18765') `
    -ProcessSnapshot $snapshot | ConvertTo-Json -Compress
"""


def _snapshot(**overrides: object) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "pid": 43628,
        "process_name": "node.exe",
        "executable_path": r"C:\tools\node.exe",
        "started_at_utc": "2026-08-10T17:29:47.0000000Z",
        "command_line": r'"C:\tools\node.exe" "E:\repo\node_modules\vite\bin\vite.js" --port 18765',
        "command_line_sha256": "d0b5f2d6cf2a2f006e71742f25c7c2577f31a1aa8c807d30f4845443bb4a4b98",
    }
    snapshot.update(overrides)
    return snapshot


def _write_json_state(path: Path, **overrides: object) -> None:
    state: dict[str, object] = {
        "schema_version": 1,
        "component": "ui",
        "pid": 43628,
        "process_name": "node.exe",
        "executable_path": r"C:\tools\node.exe",
        "started_at_utc": "2026-08-10T17:29:47.0000000Z",
        "command_line_sha256": "d0b5f2d6cf2a2f006e71742f25c7c2577f31a1aa8c807d30f4845443bb4a4b98",
        "instance_id": "00000000-0000-4000-8000-000000000001",
    }
    state.update(overrides)
    path.write_text(json.dumps(state), encoding="utf-8")


def test_json_state_is_active_only_for_the_exact_process_identity(tmp_path: Path) -> None:
    state_path = tmp_path / "ui.pid"
    _write_json_state(state_path)

    result = _run_powershell(_status_script(state_path, _snapshot()))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "active"


def test_json_state_treats_a_reused_pid_as_stale(tmp_path: Path) -> None:
    state_path = tmp_path / "ui.pid"
    _write_json_state(state_path)
    notion = _snapshot(
        process_name="Notion.exe",
        executable_path=r"C:\Users\example\AppData\Local\Programs\Notion\Notion.exe",
        started_at_utc="2026-08-11T13:29:47.0000000Z",
        command_line='"Notion.exe" --type=renderer',
        command_line_sha256="7f3db135f8d16f48963c2dc8761cc301949701949c4e59a03bc0f5e33925e886",
    )

    result = _run_powershell(_status_script(state_path, notion))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "stale"
    assert payload["reason"] == "process_identity_mismatch"


def test_json_state_with_blank_live_metadata_uses_verified_recorded_identity(tmp_path: Path) -> None:
    state_path = tmp_path / "ui.pid"
    _write_json_state(state_path)
    snapshot = _snapshot(executable_path="", command_line="")

    result = _run_powershell(_status_script(state_path, snapshot))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "active"
    assert payload["format"] == "json"
    assert payload["reason"] == "identity_match_metadata_unavailable"


def test_json_blank_metadata_rejects_mismatched_recorded_start_time(tmp_path: Path) -> None:
    state_path = tmp_path / "ui.pid"
    _write_json_state(state_path)
    snapshot = _snapshot(
        executable_path="",
        command_line="",
        started_at_utc="2026-08-10T17:29:49.0000000Z",
    )

    result = _run_powershell(_status_script(state_path, snapshot))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "stale"
    assert payload["reason"] == "process_identity_mismatch"


def test_json_blank_metadata_rejects_mismatched_recorded_name(tmp_path: Path) -> None:
    state_path = tmp_path / "ui.pid"
    _write_json_state(state_path)
    snapshot = _snapshot(executable_path="", command_line="", process_name="python.exe")

    result = _run_powershell(_status_script(state_path, snapshot))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "stale"
    assert payload["reason"] == "process_identity_mismatch"


def test_json_blank_metadata_rejects_recorded_path_outside_target(tmp_path: Path) -> None:
    state_path = tmp_path / "ui.pid"
    _write_json_state(state_path, executable_path=r"C:\other\node.exe")
    snapshot = _snapshot(executable_path="", command_line="")

    result = _run_powershell(_status_script(state_path, snapshot))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "stale"
    assert payload["reason"] == "process_not_managed"


def test_legacy_pid_requires_the_expected_executable_and_command(tmp_path: Path) -> None:
    state_path = tmp_path / "ui.pid"
    state_path.write_text("43628\n", encoding="ascii")
    unrelated_node = _snapshot(
        executable_path=r"C:\other\node.exe",
        command_line='"C:\\other\\node.exe" unrelated-server.js',
    )

    result = _run_powershell(_status_script(state_path, unrelated_node))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "stale"
    assert payload["format"] == "legacy"
    assert payload["reason"] == "legacy_process_not_managed"


def test_invalid_state_is_not_treated_as_stale(tmp_path: Path) -> None:
    state_path = tmp_path / "ui.pid"
    state_path.write_text('{"schema_version":99,"pid":43628}', encoding="utf-8")

    result = _run_powershell(_status_script(state_path, _snapshot()))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "invalid"
    assert payload["reason"] == "unsupported_or_malformed_state"


def test_writer_creates_complete_json_state_atomically(tmp_path: Path) -> None:
    state_path = tmp_path / "api.pid"
    script = f"""
$ErrorActionPreference = "Stop"
. '{MODULE_PATH}'
Write-LocalProcessState -Path '{state_path}' -Component 'api' -ProcessId $PID | Out-Null
Get-Content -LiteralPath '{state_path}' -Raw
"""

    result = _run_powershell(script)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["component"] == "api"
    assert payload["pid"] > 0
    assert payload["process_name"].casefold() in {"powershell", "powershell.exe"}
    assert payload["executable_path"].casefold().endswith("powershell.exe")
    assert payload["started_at_utc"].endswith("Z")
    assert len(payload["command_line_sha256"]) == 64
    assert len(payload["instance_id"]) == 36
    assert list(tmp_path.glob("api.pid.*.tmp")) == []


def test_stop_script_removes_reused_legacy_pid_without_stopping_that_process(tmp_path: Path) -> None:
    state_path = tmp_path / "ui.pid"
    state_path.write_text(f"{os.getpid()}\n", encoding="ascii")

    result = _run_stop_script(tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "stopped"
    assert payload["components"] == []
    assert payload["stale_components"] == ["ui"]
    assert not state_path.exists()
    assert os.getpid() > 0


def test_stop_script_preserves_invalid_state_and_fails_loudly(tmp_path: Path) -> None:
    state_path = tmp_path / "scheduler.pid"
    state_path.write_text("not-json-or-a-pid", encoding="utf-8")

    result = _run_stop_script(tmp_path)

    assert result.returncode == 2
    assert "invalid_local_pid_state" in result.stderr
    assert state_path.exists()


def test_stop_script_keeps_state_when_termination_does_not_remove_managed_process(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    process = _start_managed_ui_process(run_root)
    state_path = run_root / "ui.pid"
    try:
        result = _run_stop_with_stop_process_override(run_root, "return")

        assert result.returncode != 0
        assert result.stderr.strip() == "local_process_stop_failed"
        assert "stopped" not in result.stdout
        assert state_path.exists()
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_stop_script_sanitizes_termination_errors_and_preserves_state(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    process = _start_managed_ui_process(run_root)
    state_path = run_root / "ui.pid"
    try:
        result = _run_stop_with_stop_process_override(run_root, 'throw "termination_denied"')

        assert result.returncode != 0
        assert result.stderr.strip() == "local_process_stop_failed"
        assert "termination_denied" not in result.stderr
        assert "stopped" not in result.stdout
        assert state_path.exists()
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_stop_script_verifies_conhost_disappears_without_direct_termination(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "ui.pid").write_text("100\n", encoding="ascii")
    node_command = _node_command()
    vite_command = REPOSITORY_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
    script = f"""
$global:active = @{{ 100 = $true; 101 = $true; 102 = $true }}
$global:stopCalls = @()
function Get-CimInstance {{
    param([string]$ClassName, [string]$Filter, [object]$ErrorAction)
    if ($Filter -eq 'ProcessId = 100') {{
        return [pscustomobject]@{{
            ProcessId = 100
            Name = 'node.exe'
            ExecutablePath = '{node_command}'
            CommandLine = '"{node_command}" "{vite_command}" --config frontend/vite.config.ts'
            CreationDate = [DateTime]::UtcNow
        }}
    }}
    if ($Filter -eq 'ParentProcessId = 100') {{
        return @(
            [pscustomobject]@{{ ProcessId = 101; Name = 'conhost.exe' }},
            [pscustomobject]@{{ ProcessId = 102; Name = 'worker.exe' }}
        )
    }}
    return @()
}}
function Get-Process {{
    param([int]$Id, [object]$ErrorAction)
    if ($global:active[$Id]) {{ return [pscustomobject]@{{ Id = $Id }} }}
    return $null
}}
function Stop-Process {{
    param([int]$Id, [switch]$Force, [object]$ErrorAction)
    $global:stopCalls += $Id
    if ($Id -eq 102) {{ $global:active[102] = $false; return }}
    if ($Id -eq 100) {{
        $global:active[100] = $false
        $global:active[101] = $false
        return
    }}
}}
$payload = & '{REPOSITORY_ROOT / "scripts" / "stop-local.ps1"}' -RunRoot '{run_root}' | ConvertFrom-Json
[ordered]@{{ payload = $payload; stop_calls = $global:stopCalls }} | ConvertTo-Json -Compress
"""

    result = _run_powershell(script)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["payload"] == {
        "status": "stopped",
        "components": ["ui"],
        "stale_components": [],
    }
    assert payload["stop_calls"] == [102, 100]
    assert not (run_root / "ui.pid").exists()
