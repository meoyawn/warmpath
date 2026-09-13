from unittest.mock import Mock

import pytest

from warmpath import cli
from warmpath.cli import (
    candidate_matches_filter,
    company_path_candidate,
    connection_result_row,
    render_company_path_result,
)


def test_candidate_filter_matches_visible_profile_fields_case_insensitively() -> None:
    candidate = {
        "target": {
            "name": "Ada Lovelace",
            "jobtitle": "Senior Android Developer",
            "location": "London, United Kingdom",
            "url": "https://www.linkedin.com/in/ada-lovelace/",
        }
    }

    assert candidate_matches_filter(candidate, "android developer")
    assert candidate_matches_filter(candidate, "ADA")
    assert candidate_matches_filter(candidate, "united kingdom")
    assert not candidate_matches_filter(candidate, "iOS")


def test_candidate_filter_normalizes_whitespace() -> None:
    candidate = {"target": {"jobtitle": "Senior Android   Developer"}}

    assert candidate_matches_filter(candidate, "android developer")


def test_connection_result_row_extracts_visible_mutual_connection_names() -> None:
    row = connection_result_row(
        {
            "entityUrn": "urn:li:fsd_profile:ACoAABaR-MoBLHrbfL3jmaglgQoONjPGWrfmTSE",
            "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_2"},
            "title": {"text": "Ruslan Gilemzianov"},
            "primarySubtitle": {
                "text": "Lead Software Engineer | Low-Latency Systems @ Binance"
            },
            "secondarySubtitle": {"text": "Dubai, United Arab Emirates"},
            "insights": [
                {
                    "simpleInsight": {
                        "title": {
                            "text": (
                                "Maksim Kuzmin, Nikita Feshchun "
                                "& 8 other mutual connections"
                            )
                        }
                    }
                }
            ],
        }
    )

    assert row["mutual_connections"] == [
        {"name": "Maksim Kuzmin", "url": None, "urn_id": None},
        {"name": "Nikita Feshchun", "url": None, "urn_id": None},
    ]
    assert row["mutual_count"] == 10
    assert row["mutuals_truncated"] is True


def test_second_degree_candidate_renders_visible_mutual_connections() -> None:
    row = {
        "name": "Ruslan Gilemzianov",
        "distance": "DISTANCE_2",
        "jobtitle": "Lead Software Engineer @ Binance",
        "location": "Dubai, United Arab Emirates",
        "urn_id": "ACoAABaR-MoBLHrbfL3jmaglgQoONjPGWrfmTSE",
        "url": None,
        "mutual_connections": [
            {
                "name": "Anastasiia Krivobokova",
                "url": "https://www.linkedin.com/in/anastasiaandreewnaa/",
                "urn_id": "ACoAACeBStkBoUxCGfrnXFevZJkQ-UX6eHu4deU",
            },
            {
                "name": "Andrey Zhuchkov",
                "url": "https://www.linkedin.com/in/a-zhuchkov/",
                "urn_id": "ACoAACJd_TgBcn1VPQemkT4e3qsSPkR9WjEYhy8",
            },
        ],
        "mutual_count": 2,
        "mutuals_truncated": False,
    }

    result = {
        "company": {
            "name": "Binance",
            "url": "https://www.linkedin.com/company/binance/",
            "urn_id": "100531715",
        },
        "query": {"company": "binance", "max_degree": 2},
        "summary": {"direct_count": 0, "second_degree_count": 1},
        "candidates": [company_path_candidate(row, 2)],
    }

    rendered = render_company_path_result(result)

    assert "Mutuals (2): Anastasiia Krivobokova, Andrey Zhuchkov" in rendered
    assert "Path:" not in rendered
    assert "Status:" not in rendered
    assert "Company URN:" not in rendered
    assert "URN:" not in rendered
    assert "unknown introducer" not in rendered


@pytest.mark.parametrize("mutual_count", [None, 3])
@pytest.mark.parametrize("found_mutual", [True, False])
def test_company_looks_up_missing_mutual_contacts(
    tmp_path, monkeypatch, mutual_count, found_mutual
) -> None:
    monkeypatch.setattr(cli, "resolve_company", Mock(return_value=(
        {"name": "Acme", "urn_id": "company-id"}, []
    )))
    monkeypatch.setattr(cli, "fetch_company_people", Mock(side_effect=lambda *args: (
        [] if args[3] == 1 else [{
            "name": "Employee",
            "urn_id": "employee-id",
            "distance": "DISTANCE_2",
            "mutual_count": mutual_count,
            "mutuals_truncated": True,
            "_search_source": "search.current_company",
        }]
    )))
    api = Mock()
    api.search.return_value = [{
        "entityUrn": "urn:li:fsd_profile:contact-id",
        "title": {"text": "Ada Lovelace"},
        "navigationUrl": "https://www.linkedin.com/in/ada-lovelace/",
        "entityCustomTrackingInfo": {"memberDistance": "DISTANCE_1"},
    }] if found_mutual else []

    result = cli.find_company_path_candidates(
        api, "Acme", 2, 5, None, 5, tmp_path, False
    )

    api.search.assert_called_once_with({
        "filters": (
            "List((key:resultType,value:List(PEOPLE)),"
            "(key:connectionOf,value:List(employee-id)),"
            "(key:network,value:List(F)))"
        )
    }, limit=mutual_count or cli.DEFAULT_MAX_MUTUAL_CONNECTIONS)
    candidate = result["candidates"][0]
    rendered = render_company_path_result(result)
    assert "Employee" in rendered
    assert candidate["evidence"]["source"] == "search.current_company"
    if found_mutual:
        expected = "Mutuals (3): Ada Lovelace, +2 more" if mutual_count else "Mutuals (1): Ada Lovelace"
        assert expected in rendered
        assert candidate["path_status"] == "partially_resolved"
        assert candidate["path"][1]["url"] == "https://www.linkedin.com/in/ada-lovelace/"
    else:
        assert "Mutuals" not in rendered
        assert candidate["path_status"] == "unresolved"
        assert candidate["mutual_count"] == mutual_count
        assert candidate["mutuals_truncated"] is True

    # Repeating a company search reuses its mutual-contact lookup.
    assert cli.find_company_path_candidates(
        api, "Acme", 2, 5, None, 5, tmp_path, False
    ) == result
    api.search.assert_called_once()


def test_company_skips_mutual_lookups_for_direct_known_and_unprinted_candidates(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(cli, "resolve_company", Mock(return_value=(
        {"name": "Acme", "urn_id": "company-id"}, []
    )))
    direct = {"name": "Direct", "urn_id": "direct-id", "jobtitle": "Engineer"}
    second_degree = [
        {"name": "Filtered out", "urn_id": "filtered-id", "jobtitle": "Recruiter"},
        {
            "name": "Known mutual",
            "urn_id": "known-id",
            "jobtitle": "Engineer",
            "mutual_connections": [{"name": "Ada Lovelace"}],
        },
        {"name": "Over limit", "urn_id": "limited-id", "jobtitle": "Engineer"},
    ]
    monkeypatch.setattr(cli, "fetch_company_people", Mock(
        side_effect=lambda *args: [direct] if args[3] == 1 else second_degree
    ))
    api = Mock()

    result = cli.find_company_path_candidates(
        api, "Acme", 2, 2, "Engineer", 5, tmp_path, False
    )

    assert [candidate["target"]["name"] for candidate in result["candidates"]] == [
        "Direct", "Known mutual"
    ]
    assert "Mutuals (1): Ada Lovelace" in render_company_path_result(result)
    api.search.assert_not_called()


def test_second_degree_candidate_without_mutuals_keeps_unresolved_status() -> None:
    row = {
        "name": "Ruslan Gilemzianov",
        "distance": "DISTANCE_2",
        "jobtitle": "Lead Software Engineer @ Binance",
        "location": None,
        "urn_id": "ACoAABaR-MoBLHrbfL3jmaglgQoONjPGWrfmTSE",
        "url": None,
    }

    result = {
        "company": {"name": "Binance"},
        "query": {"company": "binance", "max_degree": 2},
        "summary": {"direct_count": 0, "second_degree_count": 1},
        "candidates": [company_path_candidate(row, 2)],
    }

    rendered = render_company_path_result(result)

    assert "Ruslan Gilemzianov" in rendered
    assert "Path:" not in rendered
    assert "Status:" not in rendered
    assert "URN:" not in rendered
    assert "unknown introducer" not in rendered
