from __future__ import annotations

import json
import runpy
import sys

import pytest

from neural_mesh.cli import main


def test_cli_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.2.0"


def test_cli_without_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "usage: neural-mesh" in output
    assert "demo" in output


def test_cli_demo_runs_primary_journey(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["agreement_level"] == "moderate"
    assert result["agreement_ratio"] == pytest.approx(2 / 3, abs=0.0001)
    assert result["winning_providers"] == ["cautious", "concise"]
    assert result["providers_succeeded"] == 3
    assert result["usage_recorded"] is False


def test_python_module_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["python -m neural_mesh", "--version"])
    with pytest.raises(SystemExit) as exit_status:
        runpy.run_module("neural_mesh", run_name="__main__")
    assert exit_status.value.code == 0
