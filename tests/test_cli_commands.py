import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "warmpath.cli",
            *args,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_top_level_help_shows_both_command_shapes() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "connections PROFILE" in result.stdout
    assert "company-path COMPANY" in result.stdout
    assert "connections mitchellh" in result.stdout
    assert "company-path https://www.linkedin.com/company/ozon-tech" in result.stdout


def test_profile_flag_is_not_a_top_level_command() -> None:
    result = run_cli("--profile", "mitchellh")

    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr


def test_connections_is_the_profile_command() -> None:
    result = run_cli("connections", "--help")

    assert result.returncode == 0
    assert "profile" in result.stdout
    assert "--profile" not in result.stdout
