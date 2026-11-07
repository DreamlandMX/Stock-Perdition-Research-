"""Policy tests for the frontend-only TypeScript toolchain boundary."""

import json
import os
from pathlib import Path
import shutil
import subprocess


EXPECTED_SCRIPTS = {
    "contracts:export": (
        "powershell -ExecutionPolicy Bypass -File scripts/export-openapi.ps1"
    ),
    "contracts:generate": (
        "powershell -ExecutionPolicy Bypass -File scripts/generate-api-types.ps1"
    ),
    "contracts:check": (
        "powershell -ExecutionPolicy Bypass -File scripts/generate-api-types.ps1 -Check"
    ),
    "docs:check": (
        "powershell -ExecutionPolicy Bypass -File scripts/check-markdown-links.ps1"
    ),
    "dev": "vite --config frontend/vite.config.ts",
    "build": "vite build --config frontend/vite.config.ts",
    "typecheck": (
        "tsc --noEmit --project tsconfig.json && "
        "tsc --noEmit --project frontend/tsconfig.json && "
        "tsc --noEmit --project frontend/e2e/tsconfig.json"
    ),
    "lint": (
        "eslint tooling/typescript frontend vitest.config.ts "
        "playwright.config.ts --max-warnings=0"
    ),
    "format:check": "prettier --check .",
    "test": "vitest run",
    "test:e2e": "playwright test --config playwright.config.ts",
    "test:e2e:update": (
        "playwright test --config playwright.config.ts --update-snapshots"
    ),
}
EXPECTED_DEPENDENCIES = {
    "@tanstack/react-query",
    "@tanstack/react-table",
    "echarts",
    "lightweight-charts",
    "lucide-react",
    "openapi-fetch",
    "react",
    "react-dom",
    "react-router-dom",
}
EXPECTED_DEV_DEPENDENCIES = {
    "@eslint/js",
    "@playwright/test",
    "@testing-library/dom",
    "@testing-library/jest-dom",
    "@testing-library/react",
    "@testing-library/user-event",
    "@types/node",
    "@types/react",
    "@types/react-dom",
    "@vitejs/plugin-react",
    "eslint",
    "jsdom",
    "openapi-typescript",
    "prettier",
    "typescript",
    "typescript-eslint",
    "vite",
    "vitest",
}


def load_json(path: str) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_package_is_private_locked_and_has_no_server_scripts() -> None:
    package = load_json("package.json")

    assert package["private"] is True
    assert package["packageManager"] == "npm@11.6.0"
    assert package["engines"] == {"node": ">=24.18.0 <25"}
    scripts = package["scripts"]
    assert isinstance(scripts, dict)
    assert scripts == EXPECTED_SCRIPTS
    assert all(
        name.startswith("contracts:")
        or name.startswith("docs:")
        or name
        in {
            "dev",
            "build",
            "typecheck",
            "lint",
            "format:check",
            "test",
            "test:e2e",
            "test:e2e:update",
        }
        for name in scripts
    )
    assert set(scripts).isdisjoint({"start", "serve", "server", "api", "backend"})
    assert all(
        not str(command)
        .casefold()
        .lstrip()
        .startswith(("node ", "node.exe ", "tsx ", "ts-node ", "nodemon "))
        for command in scripts.values()
    )

    dependencies = package["dependencies"]
    dev_dependencies = package["devDependencies"]
    assert isinstance(dependencies, dict)
    assert isinstance(dev_dependencies, dict)
    assert set(dependencies) == EXPECTED_DEPENDENCIES
    assert set(dev_dependencies) == EXPECTED_DEV_DEPENDENCIES
    assert all(
        isinstance(version, str)
        and version[0].isdigit()
        and not version.startswith(("^", "~"))
        for dependency_group in (dependencies, dev_dependencies)
        for version in dependency_group.values()
    )
    assert str(dev_dependencies["typescript"]).startswith("5.")


def test_package_lock_matches_the_exact_root_toolchain() -> None:
    package = load_json("package.json")
    lock = load_json("package-lock.json")

    assert lock["lockfileVersion"] == 3
    packages = lock["packages"]
    assert isinstance(packages, dict)
    root = packages[""]
    assert isinstance(root, dict)
    assert root["dependencies"] == package["dependencies"]
    assert root["devDependencies"] == package["devDependencies"]


def test_tsconfig_enforces_required_strict_no_emit_boundary() -> None:
    config = load_json("tsconfig.json")
    compiler = config["compilerOptions"]

    assert compiler["strict"] is True
    assert compiler["noUncheckedIndexedAccess"] is True
    assert compiler["exactOptionalPropertyTypes"] is True
    assert compiler["noEmit"] is True
    assert config["include"] == [
        "tooling/typescript/**/*.ts",
        "vitest.config.ts",
        "playwright.config.ts",
    ]

    e2e = load_json("frontend/e2e/tsconfig.json")
    e2e_compiler = e2e["compilerOptions"]
    assert e2e_compiler["strict"] is True
    assert e2e_compiler["noUncheckedIndexedAccess"] is True
    assert e2e_compiler["exactOptionalPropertyTypes"] is True
    assert e2e_compiler["noEmit"] is True


def test_typescript_contract_smoke_is_generic_and_openapi_ready() -> None:
    source = Path("tooling/typescript/contract-smoke.ts").read_text(encoding="utf-8")

    assert "OpenAPI-generated" in source
    assert "ForecastBundle" not in source
    assert "HealthResponse" not in source
    assert "CapabilitySnapshot" not in source
    assert "react" not in source.casefold()


def test_repository_has_no_javascript_or_react_application_source() -> None:
    application_roots = [Path("tooling"), Path("backend")]
    javascript = [
        path
        for root in application_roots
        if root.exists()
        for pattern in ("*.js", "*.jsx")
        for path in root.rglob(pattern)
    ]
    typescript = [
        path
        for root in application_roots
        if root.exists()
        for path in root.rglob("*.ts")
    ]

    assert javascript == []
    assert all("react" not in path.read_text(encoding="utf-8").casefold() for path in typescript)


def test_powershell_wrapper_only_orchestrates_npm_commands() -> None:
    wrapper = Path("scripts/check-typescript.ps1").read_text(encoding="utf-8")

    assert ".node-version" in wrapper
    assert "$env:LOCALAPPDATA" in wrapper
    assert "node.exe" in wrapper
    assert "npm.cmd" in wrapper
    assert "node_version_mismatch" in wrapper
    assert "node_toolchain_unavailable" in wrapper
    assert "$npmCommand" in wrapper
    assert "npm " not in wrapper
    assert "OpenD" not in wrapper
    assert "request_history_kline" not in wrapper
    assert "exit $LASTEXITCODE" in wrapper


def test_readme_uses_installed_node_lts_for_toolchain_commands() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "$env:LOCALAPPDATA" in readme
    assert ".\\scripts\\check-typescript.ps1" in readme
    assert '& "$nodeLts\\npm.cmd" run typecheck' in readme


def _isolated_wrapper(tmp_path: Path, expected_version: str) -> Path:
    root = tmp_path / "repository"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "check-typescript.ps1"
    shutil.copyfile("scripts/check-typescript.ps1", wrapper)
    (root / ".node-version").write_text(expected_version, encoding="utf-8")
    return wrapper


def _powershell() -> str:
    executable = shutil.which("powershell.exe")
    assert executable is not None
    return executable


def test_wrapper_reports_fixed_unavailable_error_and_exit_code(
    tmp_path: Path,
) -> None:
    wrapper = _isolated_wrapper(tmp_path, "24.18.0")
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(tmp_path / "empty-local-app-data")
    env["PATH"] = r"C:\Windows\System32;C:\Windows"

    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "node_toolchain_unavailable"


def test_wrapper_reports_fixed_version_mismatch_and_exit_code(
    tmp_path: Path,
) -> None:
    wrapper = _isolated_wrapper(tmp_path, "0.0.0")
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(tmp_path / "empty-local-app-data")

    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "node_version_mismatch"
