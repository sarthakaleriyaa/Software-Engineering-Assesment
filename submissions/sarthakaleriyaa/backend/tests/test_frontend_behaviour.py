"""Runs tests/js/frontend_unit.mjs: the real frontend modules (api.js and
ui-logic.js) in Node, against scripted API responses."""

import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_frontend_modules():
    result = subprocess.run(
        [NODE, str(BACKEND / "tests" / "js" / "frontend_unit.mjs"), str(BACKEND / "app" / "static" / "js")],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
    assert result.stdout.count("PASS") >= 19, result.stdout


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
@pytest.mark.parametrize("module", ["api.js", "app.js", "ui-logic.js"])
def test_frontend_module_syntax(module):
    result = subprocess.run(
        [NODE, "--check", str(BACKEND / "app" / "static" / "js" / module)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
