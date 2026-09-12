from collections import Counter
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from requests import Response
from requests.cookies import RequestsCookieJar
from requests.exceptions import HTTPError

from warmpath import auth, cli, companies


def employer(name="Acme", company_id="1", slug="acme"):
    return {"entityUrn": f"urn:li:fsd_company:{company_id}", "name": name, "universalName": slug}


def position(company=None, *, ended=False, name=None):
    dates = {"start": {"year": 2020}}
    if ended:
        dates["end"] = {"year": 2023}
    return {"dateRange": dates, "company": company, "companyName": name}


def profile(identity, positions=None):
    result = {"entityUrn": f"urn:li:fsd_profile:{identity}", "publicIdentifier": f"public-{identity}"}
    if positions is not None:
        result["profilePositionGroups"] = {"elements": [
            {"profilePositionInPositionGroup": {"elements": positions}},
        ]}
    return result


def page(profiles, start=0, total=None):
    paging = {"start": start, "count": 2}
    if total is not None:
        paging["total"] = total
    return {"elements": [{"connectedMemberResolutionResult": p} for p in profiles], "paging": paging}


def test_visits_every_page_and_fetches_each_unique_profile_once():
    pages = {0: page([profile("a"), profile("b")]),
             2: page([profile("b"), profile("c")], 2), 4: page([], 4)}
    calls = []

    def fetch(endpoint, params):
        calls.append((endpoint, params))
        if endpoint.endswith("connections"):
            return pages[params["start"]]
        return {"elements": [profile(params["memberIdentity"], [position(employer())])]}

    result = companies.find_companies(fetch)

    assert result["connections"] == 3
    assert result["unavailable_profiles"] == []
    assert result["companies"] == [{"urn_id": "1", "name": "Acme", "url": "https://www.linkedin.com/company/acme/"}]
    assert Counter(params["memberIdentity"] for _, params in calls if "memberIdentity" in params) == {"a": 1, "b": 1, "c": 1}
    assert [params["start"] for _, params in calls if "start" in params] == [0, 2, 4]
    assert all(endpoint in {"/relationships/dash/connections", "/identity/dash/profiles"} for endpoint, _ in calls)


def test_inline_employers_and_known_total_need_only_one_request():
    fetch = Mock(return_value=page([
        profile("a", [position(employer()), position(employer("Old", "2", "old"), ended=True)]),
        profile("b", []),
    ], total=2))

    result = companies.find_companies(fetch)

    fetch.assert_called_once()
    assert [company["name"] for company in result["companies"]] == ["Acme"]


def test_current_positions_follow_only_the_requested_normalized_profile_graph():
    payload = {
        "data": {"*elements": ["urn:li:fsd_profile:a"]},
        "included": [
            {"entityUrn": "urn:li:fsd_profile:a", "*profilePositionGroups": "groups"},
            {"entityUrn": "groups", "*elements": ["group"]},
            {"entityUrn": "group", "*profilePositionInPositionGroup": "positions"},
            {"entityUrn": "positions", "*elements": ["current", "former", "second"]},
            {"entityUrn": "current", "dateRange": {"start": {"year": 2024}}, "*company": "urn:li:fsd_company:1"},
            {"entityUrn": "former", "dateRange": {"start": {"year": 2020}, "end": {"year": 2023}}, "companyName": "Old company"},
            {"entityUrn": "second", "dateRange": {"start": {"year": 2025}}, "companyName": "Independent"},
            employer(),
            {"entityUrn": "unrelated", "dateRange": {"start": {"year": 2026}}, "companyName": "Someone else's employer"},
        ],
    }

    result = companies.profile_companies(lambda *args: payload, "a")

    assert {company["name"] for company in result} == {"Acme", "Independent"}


def test_connection_without_embedded_profile_is_still_looked_up():
    fetch = Mock(side_effect=[
        {"elements": [{"connectedMember": "urn:li:fsd_profile:a"}], "paging": {"total": 1}},
        {"elements": [profile("a", [position(employer())])]},
    ])

    assert companies.find_companies(fetch)["connections"] == 1
    assert fetch.call_args.args[1]["memberIdentity"] == "a"


def test_deduplication_preserves_distinct_company_ids_and_name_only_employers():
    result = companies.deduplicate_companies([
        {"urn_id": "1", "name": "Acme", "url": "https://www.linkedin.com/company/acme/"},
        {"urn_id": "1", "name": "Acme", "url": "https://www.linkedin.com/company/1/"},
        {"urn_id": "2", "name": "Acme", "url": "https://www.linkedin.com/company/2/"},
        {"urn_id": None, "name": "Independent", "url": None},
        {"urn_id": None, "name": "INDEPENDENT", "url": None},
    ])

    assert len(result) == 3
    assert {company["urn_id"] for company in result} == {"1", "2", None}


def test_company_urn_gives_url_without_a_company_lookup():
    graph = companies.ResponseGraph({})
    result = companies.position_company({"companyUrn": "urn:li:fsd_company:123", "companyName": " Acme  Inc "}, {}, graph)

    assert result == {"urn_id": "123", "name": "Acme Inc", "url": "https://www.linkedin.com/company/123/"}


def test_external_website_is_not_treated_as_a_linkedin_company_url():
    graph = companies.ResponseGraph({})
    result = companies.position_company({"company": {"name": "Acme", "url": "https://acme.example/"}}, {}, graph)

    assert result == {"urn_id": None, "name": "Acme", "url": None}


@pytest.mark.parametrize("status", [403, 404])
def test_unavailable_profile_does_not_prevent_visiting_remaining_connections(status):
    response = Response()
    response.status_code = status
    fetch = Mock(return_value=page([profile("a"), profile("b")], total=2))
    get_employers = Mock(side_effect=[HTTPError(response=response), [{"name": "Acme", "urn_id": "1", "url": None}]])

    result = companies.find_companies(fetch, get_employers)

    assert result["connections"] == 2
    assert result["unavailable_profiles"] == ["a"]
    assert len(result["companies"]) == 1
    assert get_employers.call_count == 2


def test_rate_limit_is_not_treated_as_an_unavailable_profile():
    response = Response()
    response.status_code = 429
    fetch = Mock(return_value=page([profile("a")], total=1))

    with pytest.raises(HTTPError):
        companies.find_companies(fetch, Mock(side_effect=HTTPError(response=response)))


def test_repeated_pages_and_early_empty_pages_are_reported_as_incomplete():
    with pytest.raises(companies.CompaniesError, match="repeated"):
        companies.find_companies(Mock(side_effect=[page([profile("a", [])]), page([profile("a", [])], 1)]))
    with pytest.raises(companies.CompaniesError, match="reported total"):
        companies.find_companies(Mock(return_value=page([], total=1)))


def test_missing_normalized_reference_is_reported():
    graph = companies.ResponseGraph({"data": {"*elements": ["missing"]}})
    with pytest.raises(companies.CompaniesError, match="referenced record"):
        graph.elements(graph.root)


def test_truncated_employment_history_is_reported_while_keeping_known_companies():
    member = profile("a", [position(employer())])
    member["profilePositionGroups"]["paging"] = {"total": 15}

    result = companies.find_companies(Mock(return_value=page([member], total=1)))

    assert result["connections"] == 1
    assert result["unavailable_profiles"] == ["a"]
    assert result["companies"][0]["name"] == "Acme"


def test_current_group_identifies_employer_without_loading_more_roles():
    member = profile("a", [])
    member["profilePositionGroups"]["elements"] = [{
        "dateRange": {"start": {"year": 2020}}, "companyName": "Acme",
        "companyUrn": "urn:li:fsd_company:1",
        "profilePositionInPositionGroup": {"elements": [], "paging": {"total": 25}},
    }]

    result = companies.find_companies(Mock(return_value=page([member], total=1)))

    assert result["unavailable_profiles"] == []
    assert result["companies"][0]["urn_id"] == "1"


def test_historical_tail_does_not_require_more_current_employer_lookups():
    member = profile("a", [])
    member["profilePositionGroups"] = {"paging": {"total": 30}, "elements": [
        {"dateRange": {"start": {"year": 2010}}, "companyName": "Acme"},
        {"dateRange": {"start": {"year": 2020}, "end": {"year": 2021}}, "companyName": "Former"},
    ]}
    fetch = Mock(return_value=page([member], total=1))

    result = companies.find_companies(fetch)

    assert result["unavailable_profiles"] == []
    assert [company["name"] for company in result["companies"]] == ["Acme"]
    fetch.assert_called_once()


def test_unexpected_group_order_does_not_hide_truncation():
    assert not companies.reached_past_employment([
        {"dateRange": {"start": {"year": 2020}, "end": {"year": 2021}}},
        {"dateRange": {"start": {"year": 2010}}},
    ])


@pytest.fixture
def cli_context(tmp_path, monkeypatch):
    cookies = RequestsCookieJar()
    cookies.set("li_at", "private-test-token", domain=".linkedin.com")
    session = auth.AuthSession("chrome", datetime.now(timezone.utc), cookies)
    monkeypatch.setattr(auth, "load_auth", lambda: session)
    api = Mock()
    build = Mock(return_value=api)
    monkeypatch.setattr(cli, "build_api", build)

    def fetch(endpoint, *, params):
        payload = page([profile("a"), profile("b")], total=2) if endpoint.endswith("connections") else {
            "elements": [profile(params["memberIdentity"], [position(employer())])],
        }
        response = Mock()
        response.json.return_value = payload
        return response

    api._fetch.side_effect = fetch
    return tmp_path, session, api, build


def test_cli_cache_and_url_output_make_zero_requests_on_repeat(cli_context, capsys):
    cache_dir, _, api, build = cli_context
    arguments = ["companies", "--cache-dir", str(cache_dir)]
    cli.main(arguments)
    assert capsys.readouterr().out == "Acme  https://www.linkedin.com/company/acme/\n"
    assert api._fetch.call_count == 3
    build.reset_mock()
    api._fetch.reset_mock()

    cli.main([*arguments, "--urls"])

    assert capsys.readouterr().out == "https://www.linkedin.com/company/acme/\n"
    build.assert_not_called()
    api._fetch.assert_not_called()
    assert "private-test-token" not in "".join(path.read_text() for path in cache_dir.glob("*.json"))


def test_cli_refresh_and_account_change_do_not_reuse_stale_results(cli_context):
    cache_dir, session, api, _ = cli_context
    arguments = ["companies", "--cache-dir", str(cache_dir)]
    cli.main(arguments)
    cli.main([*arguments, "--refresh-cache"])
    assert api._fetch.call_count == 6
    session.cookies.set("li_at", "different-account", domain=".linkedin.com")
    cli.main(arguments)
    assert api._fetch.call_count == 9


def test_failed_run_reuses_completed_profile_lookups_on_retry(cli_context, capsys):
    cache_dir, _, api, _ = cli_context
    normal_fetch = api._fetch.side_effect
    failed = False

    def fetch(endpoint, *, params):
        nonlocal failed
        if params.get("memberIdentity") == "b" and not failed:
            failed = True
            raise auth.AuthError("Session expired")
        return normal_fetch(endpoint, params=params)

    api._fetch.side_effect = fetch
    arguments = ["companies", "--cache-dir", str(cache_dir)]
    with pytest.raises(SystemExit):
        cli.main(arguments)
    capsys.readouterr()
    api._fetch.reset_mock()

    cli.main(arguments)

    assert api._fetch.call_count == 1
    assert api._fetch.call_args.kwargs["params"]["memberIdentity"] == "b"
    assert "Acme" in capsys.readouterr().out


def test_urls_omit_employers_without_linkedin_pages():
    assert companies.render_companies([
        {"name": "Independent", "url": None}, {"name": "Acme", "url": "https://www.linkedin.com/company/1/"},
    ], True) == "https://www.linkedin.com/company/1/"


def test_incomplete_cached_snapshot_keeps_warning_without_more_requests(cli_context, capsys):
    cache_dir, _, api, build = cli_context
    normal = api._fetch.side_effect
    response = Response()
    response.status_code = 404

    def fetch(endpoint, *, params):
        if params.get("memberIdentity") == "b":
            raise HTTPError(response=response)
        return normal(endpoint, params=params)

    api._fetch.side_effect = fetch
    arguments = ["companies", "--cache-dir", str(cache_dir)]
    with pytest.raises(SystemExit) as first:
        cli.main(arguments)
    assert first.value.code == 1
    assert "incomplete" in capsys.readouterr().err
    build.reset_mock()
    api._fetch.reset_mock()

    with pytest.raises(SystemExit) as second:
        cli.main(arguments)

    assert second.value.code == 1
    assert "incomplete" in capsys.readouterr().err
    build.assert_not_called()
    api._fetch.assert_not_called()
