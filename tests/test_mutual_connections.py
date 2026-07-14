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
