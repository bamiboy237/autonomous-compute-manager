import pytest

from acm_cli.main import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "acmctl 0.1.0" in capsys.readouterr().out


def test_no_command() -> None:
    assert main([]) == 2
