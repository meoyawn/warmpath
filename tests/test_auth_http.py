from copy import deepcopy
from email.message import Message
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from requests import Response
from requests.adapters import BaseAdapter
from requests.exceptions import HTTPError, Timeout, TooManyRedirects

from warmpath import auth, auth_http, cli


def response(status=200, *, cookies=(), location=None):
    result = Response()
    result.status_code = status
    result._content = b'{"miniProfile":{"firstName":"Ada","lastName":"Lovelace"}}'
    result.headers["content-type"] = "application/json"
    if location:
        result.headers["location"] = location
    message = Message()
    for cookie in cookies:
        message.add_header("Set-Cookie", cookie)
    result.raw = Mock(_original_response=SimpleNamespace(msg=message))
    return result


class LinkedInAdapter(BaseAdapter):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        result.request = request
        result.url = request.url
        return result

    def close(self):
        pass


@pytest.fixture
def saved_session(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from requests.cookies import RequestsCookieJar

    monkeypatch.setattr(auth, "auth_store_path", lambda: tmp_path / "auth.json")
    monkeypatch.setattr(auth_http, "sleep", lambda _: None)
    cookies = RequestsCookieJar()
    for name, value in (
        ("li_at", "old-auth-token"), ("JSESSIONID", '"ajax:old-csrf"'),
        ("bcookie", "browser-device"), ("bscookie", "verified-device"),
    ):
        cookies.set(name, value, domain=".www.linkedin.com", path="/", secure=True)
    session = auth.AuthSession("chrome", datetime.now(timezone.utc), cookies)
    auth.save_auth(session)
    return session


@pytest.fixture
def reader(saved_session, monkeypatch):
    fresh = deepcopy(saved_session.cookies)
    fresh.set("li_at", "new-auth-token", domain=".www.linkedin.com", path="/", secure=True)
    fresh.set("JSESSIONID", '"ajax:new-csrf"', domain=".www.linkedin.com", path="/", secure=True)
    reader = Mock(return_value=fresh)
    monkeypatch.setattr(auth.browser_cookie3, "chrome", reader)
    return reader


def api_with_responses(responses):
    api = cli.build_api()
    adapter = LinkedInAdapter(responses)
    api.client.session.mount("https://www.linkedin.com/", adapter)
    return api, adapter


def test_healthy_requests_use_device_cookies_without_auth_probe_or_browser_io(saved_session, reader):
    before = auth.auth_store_path().read_bytes()
    api, adapter = api_with_responses([response(), response()])

    api._fetch("/example")
    api._fetch("/example?page=2")

    assert len(adapter.requests) == 2
    assert all("bcookie=browser-device" in req.headers["Cookie"] for req in adapter.requests)
    assert all("bscookie=verified-device" in req.headers["Cookie"] for req in adapter.requests)
    reader.assert_not_called()
    assert auth.auth_store_path().read_bytes() == before


def test_imported_browser_user_agent_is_sent(saved_session, reader):
    saved_session.user_agent = "Imported Chrome/152.0.0.0"
    auth.save_auth(saved_session)
    api, adapter = api_with_responses([response()])

    api._fetch("/example")

    assert adapter.requests[0].headers["user-agent"] == saved_session.user_agent


def test_old_import_uses_installed_browser_user_agent(saved_session, reader, monkeypatch):
    monkeypatch.setattr(auth, "browser_user_agent", lambda browser: "Installed Chrome/152.0.0.0")
    api, adapter = api_with_responses([response()])

    api._fetch("/example")

    assert adapter.requests[0].headers["user-agent"] == "Installed Chrome/152.0.0.0"


def test_requests_are_spaced_without_adding_http_calls(saved_session, reader, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(auth_http, "monotonic", lambda: clock[0])
    monkeypatch.setattr(auth_http, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    api, adapter = api_with_responses([response(), response()])

    api._fetch("/example")
    api._fetch("/example?page=2")

    assert clock[0] == 10.0 + auth_http.MIN_REQUEST_INTERVAL
    assert len(adapter.requests) == 2


def test_rejected_session_refreshes_once_and_retries_original_request(saved_session, reader):
    api, adapter = api_with_responses([
        response(401, cookies=["li_at=; Max-Age=0; Domain=.www.linkedin.com; Path=/; Secure"]),
        response(), response(),
    ])

    api._fetch("/example", params={"start": 100})
    api._fetch("/example", params={"start": 200})

    assert len(adapter.requests) == 3
    assert adapter.requests[0].url == adapter.requests[1].url
    assert "start=100" in adapter.requests[1].url
    assert "li_at=new-auth-token" in adapter.requests[1].headers["Cookie"]
    assert adapter.requests[1].headers["csrf-token"] == "ajax:new-csrf"
    reader.assert_called_once_with(domain_name="linkedin.com")
    assert auth.load_cookies().get("li_at") == "new-auth-token"


def test_successful_responses_persist_rotated_cookies_and_update_csrf(saved_session, reader):
    api, adapter = api_with_responses([
        response(cookies=[
            "li_at=server-token; Domain=.www.linkedin.com; Path=/; Secure",
            'JSESSIONID="ajax:server-csrf"; Domain=.www.linkedin.com; Path=/; Secure',
        ]),
        response(),
    ])

    api._fetch("/example")
    assert auth.load_cookies().get("li_at") == "server-token"
    api._fetch("/example")

    assert adapter.requests[1].headers["csrf-token"] == "ajax:server-csrf"
    assert 'JSESSIONID="ajax:server-csrf"' in adapter.requests[1].headers["Cookie"]
    reader.assert_not_called()


def test_long_running_client_can_recover_from_later_rotation(saved_session, reader):
    second_rotation = deepcopy(reader.return_value)
    second_rotation.set("li_at", "second-new-token", domain=".www.linkedin.com", path="/", secure=True)
    reader.side_effect = [reader.return_value, second_rotation]
    api, adapter = api_with_responses([response(401), response(), response(401), response()])

    api._fetch("/example?page=1")
    api._fetch("/example?page=2")

    assert reader.call_count == 2
    assert len(adapter.requests) == 4
    assert "li_at=second-new-token" in adapter.requests[-1].headers["Cookie"]
    assert auth.load_cookies().get("li_at") == "second-new-token"


def test_rotated_csrf_cookie_at_more_specific_domain_replaces_old_header(saved_session, reader):
    saved_session.cookies.clear(".www.linkedin.com", "/", "JSESSIONID")
    saved_session.cookies.set("JSESSIONID", '"ajax:broad"', domain=".linkedin.com", path="/", secure=True)
    auth.save_auth(saved_session)
    api, adapter = api_with_responses([
        response(cookies=['JSESSIONID="ajax:specific"; Domain=.www.linkedin.com; Path=/; Secure']),
        response(),
    ])

    api._fetch("/example")
    api._fetch("/example")

    assert adapter.requests[1].headers["Cookie"].count("JSESSIONID=") == 1
    assert 'JSESSIONID="ajax:specific"' in adapter.requests[1].headers["Cookie"]
    assert adapter.requests[1].headers["csrf-token"] == "ajax:specific"


def test_import_saves_cookies_returned_by_validation(saved_session, reader, monkeypatch, capsys):
    adapter = LinkedInAdapter([response(cookies=[
        "li_at=validated-token; Domain=.www.linkedin.com; Path=/; Secure",
    ])])
    original_build_api = cli.build_api

    def build(*args, **kwargs):
        api = original_build_api(*args, **kwargs)
        api.client.session.mount("https://www.linkedin.com/", adapter)
        return api

    monkeypatch.setattr(cli, "build_api", build)
    cli.main(["auth", "import", "--browser", "chrome"])

    assert capsys.readouterr().out == "Logged in as Ada Lovelace\n"
    assert auth.load_cookies().get("li_at") == "validated-token"
    assert len(adapter.requests) == 1
    reader.assert_called_once()


def test_import_then_separate_status_clients_reuse_saved_rotation(saved_session, reader, monkeypatch, capsys):
    adapters = []
    replies = [
        response(cookies=[
            "li_at=validated-token; Domain=.www.linkedin.com; Path=/; Secure",
            'JSESSIONID="ajax:validated-csrf"; Domain=.www.linkedin.com; Path=/; Secure',
            "lidc=routing; Domain=.linkedin.com; Path=/; Secure",
        ]),
        response(cookies=[
            'JSESSIONID="ajax:status-csrf"; Domain=.www.linkedin.com; Path=/; Secure',
        ]),
        response(),
    ]
    original_build_api = cli.build_api

    def build(*args, **kwargs):
        api = original_build_api(*args, **kwargs)
        adapter = LinkedInAdapter([replies[len(adapters)]])
        adapters.append(adapter)
        api.client.session.mount("https://www.linkedin.com/", adapter)
        return api

    monkeypatch.setattr(cli, "build_api", build)
    cli.main(["auth", "import", "--browser", "chrome"])
    cli.main(["auth", "status"])
    cli.main(["auth", "status"])

    assert capsys.readouterr().out == "Logged in as Ada Lovelace\n" * 3
    reader.assert_called_once()
    assert len(adapters) == 3
    for adapter, csrf in zip(adapters[1:], ("ajax:validated-csrf", "ajax:status-csrf")):
        assert len(adapter.requests) == 1
        request = adapter.requests[0]
        assert "li_at=validated-token" in request.headers["Cookie"]
        assert "bcookie=browser-device" in request.headers["Cookie"]
        assert "bscookie=verified-device" in request.headers["Cookie"]
        assert "lidc=routing" in request.headers["Cookie"]
        assert request.headers["csrf-token"] == csrf
        assert f'JSESSIONID="{csrf}"' in request.headers["Cookie"]


@pytest.mark.parametrize("failure", [response(401), response(403), response(429), Timeout("secret")])
def test_failed_recovery_preserves_saved_session(saved_session, reader, failure):
    before = auth.auth_store_path().read_bytes()
    api, adapter = api_with_responses([response(401), failure])

    with pytest.raises((auth.AuthError, HTTPError, Timeout)):
        api._fetch("/example")

    assert auth.auth_store_path().read_bytes() == before
    assert len(adapter.requests) == 2
    reader.assert_called_once()


def test_same_revoked_browser_cookie_is_not_retried(saved_session, reader):
    reader.return_value = saved_session.cookies
    api, adapter = api_with_responses([response(401)])

    with pytest.raises(auth.AuthError, match="same cookies"):
        api._fetch("/example")

    assert len(adapter.requests) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_server_errors_are_not_empty_results_or_auth_retries(saved_session, reader, status):
    api, adapter = api_with_responses([response(status)])

    with pytest.raises(HTTPError):
        api._fetch("/example")

    assert len(adapter.requests) == 1
    reader.assert_not_called()


def test_profile_permission_error_does_not_refresh_valid_auth(saved_session, reader):
    api, adapter = api_with_responses([response(403), response()])

    with pytest.raises(HTTPError):
        api._fetch("/identity/dash/profiles")

    assert adapter.requests[1].url == "https://www.linkedin.com/voyager/api/me"
    reader.assert_not_called()


def test_login_redirect_is_recovered_without_following_it(saved_session, reader):
    api, adapter = api_with_responses([response(302, location="/checkpoint/lg/login"), response()])

    api._fetch("/example")

    assert len(adapter.requests) == 2
    assert adapter.requests[0].url == adapter.requests[1].url
    reader.assert_called_once()


def test_api_cookie_redirect_is_followed_with_new_cookie(saved_session, reader):
    api, adapter = api_with_responses([
        response(302, location="https://www.linkedin.com/voyager/api/example?start=100", cookies=[
            "__cf_bm=updated; Domain=.linkedin.com; Path=/; Secure",
        ]),
        response(),
    ])

    api._fetch("/example", params={"start": 100})

    assert len(adapter.requests) == 2
    assert adapter.requests[0].url == adapter.requests[1].url
    assert "__cf_bm=updated" in adapter.requests[1].headers["Cookie"]
    reader.assert_not_called()


def test_repeated_api_redirects_stop_without_reimport(saved_session, reader):
    api, adapter = api_with_responses([
        response(302, location="/voyager/api/example") for _ in range(4)
    ])

    with pytest.raises(TooManyRedirects):
        api._fetch("/example")

    assert len(adapter.requests) == 4
    reader.assert_not_called()


@pytest.mark.parametrize("url", ["/m/logout", "/uas/logout", "/%6Cogout"])
def test_logout_is_never_requested(saved_session, reader, url):
    api, adapter = api_with_responses([])
    before = auth.auth_store_path().read_bytes()

    with pytest.raises(auth.AuthError, match="logout"):
        api.client.session.get(f"https://www.linkedin.com{url}")

    assert adapter.requests == []
    assert auth.auth_store_path().read_bytes() == before
    reader.assert_not_called()


def test_profile_redirect_cannot_log_out_the_shared_session(saved_session, reader):
    api, adapter = api_with_responses([response(302, location="/m/logout")])
    before = auth.auth_store_path().read_bytes()

    with pytest.raises(auth.AuthError, match="logout"):
        api.client.session.get("https://www.linkedin.com/in/example/")

    assert len(adapter.requests) == 1
    assert auth.auth_store_path().read_bytes() == before
    reader.assert_not_called()


def test_profile_login_redirect_is_recovered_without_following_login(saved_session, reader):
    api, adapter = api_with_responses([response(302, location="/login"), response()])

    api.client.session.get("https://www.linkedin.com/in/example/")

    assert len(adapter.requests) == 2
    assert adapter.requests[0].url == adapter.requests[1].url
    reader.assert_called_once()


def test_post_is_never_replayed(saved_session, reader):
    api, adapter = api_with_responses([response(401)])

    with pytest.raises(auth.AuthError):
        api._post("/example", json={"value": 1})

    assert len(adapter.requests) == 1
    reader.assert_not_called()


def test_newer_import_is_reused_instead_of_browser_read(saved_session, reader):
    api, adapter = api_with_responses([response(401), response()])
    newer = deepcopy(saved_session)
    newer.cookies = reader.return_value
    auth.save_auth(newer)

    api._fetch("/example")

    assert "li_at=new-auth-token" in adapter.requests[1].headers["Cookie"]
    reader.assert_not_called()


def test_cookie_rotation_does_not_overwrite_newer_import(saved_session, reader):
    api, _ = api_with_responses([response(cookies=[
        "li_at=old-session-rotated; Domain=.www.linkedin.com; Path=/; Secure",
    ])])
    newer = deepcopy(saved_session)
    newer.cookies = reader.return_value
    auth.save_auth(newer)

    api._fetch("/example")

    assert auth.load_cookies().get("li_at") == "new-auth-token"
