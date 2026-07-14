import subprocess
import sys
from importlib.metadata import version as distribution_version
from pathlib import Path

import pytest

from warmpath import cli


ROOT = Path(__file__).resolve().parents[1]
RUSLAN_URL = "https://www.linkedin.com/in/ruslan-gilemzianov/"
VIOLETTA_URL = "https://www.linkedin.com/in/violetta-shmatkova-844a0986/"
TIMUR_URL = "https://www.linkedin.com/in/timur-pokayonkov/"


def run_cli(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
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
        timeout=timeout,
        check=False,
    )


def linkedin_cookie_file() -> Path:
    cookie_file = cli.DEFAULT_COOKIE_FILE
    if not cookie_file.exists() or cookie_file.stat().st_size == 0:
        pytest.skip("LinkedIn cookies are required for live integration tests")
    return cookie_file


def run_live_cli(
    tmp_path: Path,
    *args: str,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    return run_cli(
        *args,
        "--cache-dir",
        str(tmp_path),
        "--cookie-file",
        str(linkedin_cookie_file()),
        "--refresh-cache",
        timeout=timeout,
    )


def require_live_linkedin_result(result: subprocess.CompletedProcess[str]) -> None:
    unavailable_errors = (
        "ConnectionError",
        "JSONDecodeError",
        "NameResolutionError",
        "nodename nor servname provided",
    )
    if result.returncode != 0 and any(
        error in result.stderr for error in unavailable_errors
    ):
        pytest.skip("LinkedIn API is unavailable")

    assert result.returncode == 0, result.stderr
    if "No reachable employees found" in result.stdout:
        pytest.skip("LinkedIn company search returned no live results")
    if "No reachable profiles found" in result.stdout:
        pytest.skip("LinkedIn skill search returned no live results")


def test_top_level_help_shows_command_shapes() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "human PROFILE_URL" in result.stdout
    assert "company COMPANY" in result.stdout
    assert "skill SKILL" in result.stdout
    assert f"human {RUSLAN_URL}" in result.stdout
    assert "company https://www.linkedin.com/company/ozon-tech" in result.stdout
    assert "skill Flutter" in result.stdout
    assert "--version" in result.stdout


def test_top_level_version_uses_distribution_metadata() -> None:
    result = run_cli("--version")

    assert result.returncode == 0
    assert result.stdout == f"warmpath {distribution_version('warmpath')}\n"
    assert result.stderr == ""


def test_profile_flag_is_not_a_top_level_command() -> None:
    result = run_cli("--profile", "mitchellh")

    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr


def test_connections_is_not_a_command() -> None:
    result = run_cli("connections", "--help")

    assert result.returncode != 0
    assert "Unknown command: connections" in result.stderr


def test_human_is_the_profile_mutuals_command() -> None:
    result = run_cli("human", "--help")

    assert result.returncode == 0
    assert "profile URL" in result.stdout


def test_company_is_the_company_command() -> None:
    result = run_cli("company", "--help")

    assert result.returncode == 0
    assert "LinkedIn /company/ URL" in result.stdout
    assert "Default: 5." in result.stdout
    assert "company-path" not in result.stdout


def test_skill_is_the_skill_command() -> None:
    result = run_cli("skill", "--help")

    assert result.returncode == 0
    assert "skill name to search for" in result.stdout
    assert "Default: 2." in result.stdout
    assert "Default: 5." in result.stdout


def test_company_default_limit_is_five() -> None:
    args = cli.parse_company_args(["https://www.linkedin.com/company/binance/"])

    assert args.limit == 5


def test_default_paths_use_user_directories() -> None:
    args = cli.parse_company_args(["https://www.linkedin.com/company/binance/"])

    assert (
        args.cookie_file
        == Path.home() / ".config" / "warmpath" / "linkedin.cookies"
    )
    assert args.cache_dir == Path.home() / ".cache" / "warmpath"


def test_resolve_path_expands_home() -> None:
    assert cli.resolve_path(Path("~/warmpath-test")) == Path.home() / "warmpath-test"


@pytest.mark.xdist_group(name="linkedin")
def test_company_yadro_excludes_out_of_network_profile(tmp_path) -> None:
    result = run_live_cli(
        tmp_path,
        "company",
        "yadro",
        "--limit",
        "10",
        "--max-degree",
        "2",
        timeout=120,
    )

    require_live_linkedin_result(result)
    assert "https://www.linkedin.com/in/egorkazachkov/" not in result.stdout


@pytest.mark.xdist_group(name="linkedin")
def test_company_avito_large_search_includes_expected_second_degree_profile(
    tmp_path,
) -> None:
    result = run_live_cli(
        tmp_path,
        "company",
        "Avito",
        "--limit",
        "500",
        timeout=240,
    )

    require_live_linkedin_result(result)
    assert VIOLETTA_URL in result.stdout


def test_skill_default_max_depth_is_two() -> None:
    args = cli.parse_skill_args(["Flutter"])

    assert args.max_depth == 2


def test_skill_default_limit_is_five() -> None:
    args = cli.parse_skill_args(["Flutter"])

    assert args.limit == 5


def test_company_path_is_not_a_command() -> None:
    result = run_cli("company-path", "--help")

    assert result.returncode != 0
    assert "Unknown command: company-path" in result.stderr


@pytest.mark.xdist_group(name="linkedin")
def test_skill_leadership_large_search_includes_expected_profile(tmp_path) -> None:
    result = run_live_cli(
        tmp_path,
        "skill",
        "Leadership",
        "--limit",
        "25",
    )

    require_live_linkedin_result(result)
    assert TIMUR_URL in result.stdout
