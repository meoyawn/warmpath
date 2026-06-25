import subprocess
import sys
from pathlib import Path

from warmpath import cli


ROOT = Path(__file__).resolve().parents[1]
RUSLAN_URL = "https://www.linkedin.com/in/ruslan-gilemzianov/"


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


def test_top_level_help_shows_command_shapes() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "human PROFILE_URL" in result.stdout
    assert "company COMPANY" in result.stdout
    assert "skill SKILL" in result.stdout
    assert f"human {RUSLAN_URL}" in result.stdout
    assert "company https://www.linkedin.com/company/ozon-tech" in result.stdout
    assert "skill Flutter" in result.stdout


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


def test_human_direct_profile_prints_direct_connection(
    monkeypatch, capsys, tmp_path
) -> None:
    class FakeApi:
        def get_profile_network_info(self, public_id):
            assert public_id == "daniil-sunyaev-a94a4b193"
            return {"distance": "DISTANCE_1"}

        def search(self, params, limit):
            raise AssertionError("direct profiles should not fetch mutuals")

    monkeypatch.setattr(cli, "build_api", lambda cookie_file: FakeApi())
    monkeypatch.setattr(
        cli,
        "profile_urn_id",
        lambda api, public_id: (_ for _ in ()).throw(
            AssertionError("direct profiles should not need a profile URN")
        ),
    )

    cli.main(
        [
            "human",
            "https://www.linkedin.com/in/daniil-sunyaev-a94a4b193/",
            "--cache-dir",
            str(tmp_path),
            "--refresh-cache",
        ]
    )

    assert capsys.readouterr().out.splitlines() == ["Connection: direct"]


def test_human_direct_profile_falls_back_to_exact_search(
    monkeypatch, capsys, tmp_path
) -> None:
    class FakeApi:
        def get_profile_network_info(self, public_id):
            assert public_id == "daniil-sunyaev-a94a4b193"
            return {}

        def search(self, params, limit):
            assert params["keywords"] == "daniil sunyaev"
            assert "network,value:List(F)" in params["filters"]
            assert limit == 10
            return [
                {
                    "entityUrn": "urn:li:fsd_profile:daniil-urn",
                    "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_1"},
                    "title": {"text": "Daniil Sunyaev"},
                    "navigationUrl": (
                        "https://www.linkedin.com/in/daniil-sunyaev-a94a4b193/"
                    ),
                }
            ]

    monkeypatch.setattr(cli, "build_api", lambda cookie_file: FakeApi())
    monkeypatch.setattr(
        cli,
        "profile_urn_id",
        lambda api, public_id: (_ for _ in ()).throw(
            AssertionError("direct profiles should not need a profile URN")
        ),
    )

    cli.main(
        [
            "human",
            "https://www.linkedin.com/in/daniil-sunyaev-a94a4b193/",
            "--cache-dir",
            str(tmp_path),
            "--refresh-cache",
        ]
    )

    assert capsys.readouterr().out.splitlines() == ["Connection: direct"]


def test_human_second_degree_profile_prints_mutuals_heading(
    monkeypatch, capsys, tmp_path
) -> None:
    class FakeApi:
        def get_profile_network_info(self, public_id):
            assert public_id == "taninnanakorn"
            return {"distance": "DISTANCE_2"}

        def search(self, params, limit):
            assert "connectionOf,value:List(tanin-urn)" in params["filters"]
            assert "network,value:List(F)" in params["filters"]
            assert limit == cli.DEFAULT_MAX_MUTUAL_CONNECTIONS
            return [
                {
                    "entityUrn": "urn:li:fsd_profile:anastasiia-urn",
                    "title": {"text": "Anastasiia Krivobokova"},
                    "navigationUrl": "https://www.linkedin.com/in/anastasiaandreewnaa/",
                },
                {
                    "entityUrn": "urn:li:fsd_profile:andrey-urn",
                    "title": {"text": "Andrey Zhuchkov"},
                    "navigationUrl": "https://www.linkedin.com/in/a-zhuchkov/",
                },
            ]

    monkeypatch.setattr(cli, "build_api", lambda cookie_file: FakeApi())
    monkeypatch.setattr(cli, "profile_urn_id", lambda api, public_id: "tanin-urn")

    cli.main(
        [
            "human",
            "https://www.linkedin.com/in/taninnanakorn/",
            "--cache-dir",
            str(tmp_path),
            "--refresh-cache",
        ]
    )

    assert capsys.readouterr().out.splitlines() == [
        "Mutuals:",
        "Anastasiia Krivobokova  https://www.linkedin.com/in/anastasiaandreewnaa/",
        "Andrey Zhuchkov         https://www.linkedin.com/in/a-zhuchkov/",
    ]


def test_human_out_of_network_profile_prints_out_of_network(
    monkeypatch, capsys, tmp_path
) -> None:
    class FakeApi:
        def get_profile_network_info(self, public_id):
            assert public_id == "mitchellh"
            return {"distance": "OUT_OF_NETWORK"}

        def search(self, params, limit):
            raise AssertionError("out-of-network profiles should not fetch mutuals")

    monkeypatch.setattr(cli, "build_api", lambda cookie_file: FakeApi())
    monkeypatch.setattr(
        cli,
        "profile_urn_id",
        lambda api, public_id: (_ for _ in ()).throw(
            AssertionError("out-of-network profiles should not need a profile URN")
        ),
    )

    cli.main(
        [
            "human",
            "https://www.linkedin.com/in/mitchellh/",
            "--cache-dir",
            str(tmp_path),
            "--refresh-cache",
        ]
    )

    assert capsys.readouterr().out.splitlines() == ["Connection: out of network"]


def test_skill_flutter_prints_matching_first_and_second_degree_profiles(
    monkeypatch, capsys, tmp_path
) -> None:
    class FakeApi:
        def search(self, params, limit):
            assert params["keywords"] == "Flutter"
            assert limit == 5
            if "network,value:List(F)" in params["filters"]:
                return [
                    {
                        "entityUrn": "urn:li:fsd_profile:direct-urn",
                        "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_1"},
                        "title": {"text": "Direct Flutter Developer"},
                        "primarySubtitle": {"text": "Mobile Engineer"},
                        "navigationUrl": "https://www.linkedin.com/in/direct-flutter/",
                    }
                ]
            if "network,value:List(S)" in params["filters"]:
                return [
                    {
                        "entityUrn": "urn:li:fsd_profile:danis-urn",
                        "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_2"},
                        "title": {"text": "Daniil Sunyaev"},
                        "primarySubtitle": {"text": "Flutter Developer"},
                        "navigationUrl": "https://www.linkedin.com/in/dan1s/",
                    }
                ]
            raise AssertionError(params)

        def get_profile_skills(self, public_id=None, urn_id=None):
            assert public_id in {"direct-flutter", "dan1s"}
            return [{"name": "Flutter"}]

    monkeypatch.setattr(cli, "build_api", lambda cookie_file: FakeApi())

    cli.main(
        [
            "skill",
            "Flutter",
            "--cache-dir",
            str(tmp_path),
            "--refresh-cache",
        ]
    )

    output = capsys.readouterr().out
    assert "Direct Flutter Developer" in output
    assert "https://www.linkedin.com/in/dan1s/" in output
    assert "1st-degree connections" in output
    assert "2nd-degree connections" in output


def test_skill_default_limit_caps_total_printed_profiles(
    monkeypatch, capsys, tmp_path
) -> None:
    class FakeApi:
        def search(self, params, limit):
            assert params["keywords"] == "Flutter"
            assert limit == 5
            if "network,value:List(F)" in params["filters"]:
                return [
                    {
                        "entityUrn": f"urn:li:fsd_profile:direct-{index}",
                        "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_1"},
                        "title": {"text": f"Direct Flutter Developer {index}"},
                        "navigationUrl": (
                            f"https://www.linkedin.com/in/direct-{index}/"
                        ),
                    }
                    for index in range(1, 6)
                ]
            if "network,value:List(S)" in params["filters"]:
                return [
                    {
                        "entityUrn": f"urn:li:fsd_profile:second-{index}",
                        "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_2"},
                        "title": {"text": f"Second Flutter Developer {index}"},
                        "navigationUrl": (
                            f"https://www.linkedin.com/in/second-{index}/"
                        ),
                    }
                    for index in range(1, 3)
                ]
            raise AssertionError(params)

        def get_profile_skills(self, public_id=None, urn_id=None):
            return [{"name": "Flutter"}]

    monkeypatch.setattr(cli, "build_api", lambda cookie_file: FakeApi())

    cli.main(
        [
            "skill",
            "Flutter",
            "--cache-dir",
            str(tmp_path),
            "--refresh-cache",
        ]
    )

    output = capsys.readouterr().out
    numbered_lines = [
        line
        for line in output.splitlines()
        if line and line[0].isdigit() and line[1:2] == "."
    ]

    assert len(numbered_lines) == 5
    assert "Found: 5 1st-degree, 0 2nd-degree connections" in output
    assert "Direct Flutter Developer 5" in output
    assert "Second Flutter Developer" not in output


def test_skill_search_falls_back_to_profile_urn_when_public_id_skills_are_empty(
    monkeypatch, capsys, tmp_path
) -> None:
    class FakeApi:
        def search(self, params, limit):
            assert params["keywords"] == "Flutter"
            if "network,value:List(F)" in params["filters"]:
                return []
            if "network,value:List(S)" in params["filters"]:
                return [
                    {
                        "entityUrn": (
                            "urn:li:fsd_profile:ACoAAA1KfpkB0EeSqf9VZ2pkhoDCllRCroVjBC0"
                        ),
                        "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_2"},
                        "title": {"text": "Daniil Sunyaev"},
                        "primarySubtitle": {
                            "text": "Mobile Engineer | iOS, Swift, Flutter, Android"
                        },
                        "navigationUrl": "https://www.linkedin.com/in/dan1s/",
                    }
                ]
            raise AssertionError(params)

        def get_profile_skills(self, public_id=None, urn_id=None):
            if public_id == "dan1s":
                return []
            assert urn_id == "ACoAAA1KfpkB0EeSqf9VZ2pkhoDCllRCroVjBC0"
            return [{"name": "Flutter"}]

    monkeypatch.setattr(cli, "build_api", lambda cookie_file: FakeApi())

    cli.main(
        [
            "skill",
            "Flutter",
            "--cache-dir",
            str(tmp_path),
            "--refresh-cache",
        ]
    )

    assert "https://www.linkedin.com/in/dan1s/" in capsys.readouterr().out


def test_skill_search_keeps_visible_profile_skill_when_skill_endpoint_is_empty(
    monkeypatch, capsys, tmp_path
) -> None:
    class FakeApi:
        def search(self, params, limit):
            assert params["keywords"] == "Flutter"
            if "network,value:List(F)" in params["filters"]:
                return [
                    {
                        "entityUrn": (
                            "urn:li:fsd_profile:ACoAAA1KfpkB0EeSqf9VZ2pkhoDCllRCroVjBC0"
                        ),
                        "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_1"},
                        "title": {"text": "Danis Ziganshin"},
                        "primarySubtitle": {
                            "text": "Mobile Engineer | iOS, Swift, Flutter, Android"
                        },
                        "secondarySubtitle": {"text": "Kazan"},
                        "navigationUrl": "https://www.linkedin.com/in/dan1s/",
                    }
                ]
            if "network,value:List(S)" in params["filters"]:
                return []
            raise AssertionError(params)

        def get_profile_skills(self, public_id=None, urn_id=None):
            return []

    monkeypatch.setattr(cli, "build_api", lambda cookie_file: FakeApi())

    cli.main(
        [
            "skill",
            "Flutter",
            "--cache-dir",
            str(tmp_path),
            "--refresh-cache",
        ]
    )

    output = capsys.readouterr().out
    assert "Danis Ziganshin" in output
    assert "https://www.linkedin.com/in/dan1s/" in output


def test_skill_leadership_prints_expected_matching_profile(
    monkeypatch, capsys, tmp_path
) -> None:
    class FakeApi:
        def search(self, params, limit):
            assert params["keywords"] == "Leadership"
            if "network,value:List(F)" in params["filters"]:
                return []
            if "network,value:List(S)" in params["filters"]:
                return [
                    {
                        "entityUrn": "urn:li:fsd_profile:timur-urn",
                        "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_2"},
                        "title": {"text": "Timur Pokayonkov"},
                        "primarySubtitle": {"text": "Engineering Leader"},
                        "navigationUrl": (
                            "https://www.linkedin.com/in/timur-pokayonkov/"
                        ),
                    }
                ]
            raise AssertionError(params)

        def get_profile_skills(self, public_id=None, urn_id=None):
            assert public_id == "timur-pokayonkov"
            return [{"name": "Leadership"}]

    monkeypatch.setattr(cli, "build_api", lambda cookie_file: FakeApi())

    cli.main(
        [
            "skill",
            "Leadership",
            "--cache-dir",
            str(tmp_path),
            "--refresh-cache",
        ]
    )

    output = capsys.readouterr().out
    assert "Timur Pokayonkov" in output
    assert "https://www.linkedin.com/in/timur-pokayonkov/" in output
