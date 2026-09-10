import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from requests import Request
from requests.cookies import RequestsCookieJar

from warmpath import auth, cli


@pytest.fixture(autouse=True)
def auth_path(tmp_path, monkeypatch):
    path = tmp_path / "warmpath" / "auth.json"
    monkeypatch.setattr(auth, "auth_store_path", lambda: path)
    return path


@pytest.fixture
def browser_cookies():
    jar = RequestsCookieJar()
    jar.set(
        "li_at", "private-linkedin-token", domain=".linkedin.com", path="/",
        secure=True, expires=4102444800,
    )
    jar.set(
        "JSESSIONID", '"ajax:private-csrf-token"', domain="www.linkedin.com",
        path="/", secure=True,
    )
    next(cookie for cookie in jar if cookie.name == "JSESSIONID").domain_specified = False
    return jar


@pytest.fixture
def reader(browser_cookies, monkeypatch):
    reader = Mock(return_value=browser_cookies)
    monkeypatch.setattr(auth.browser_cookie3, "chrome", reader)
    return reader


@pytest.mark.parametrize("browser", auth.BROWSERS)
def test_import_reads_only_the_selected_browser(browser, browser_cookies, monkeypatch):
    reader = Mock(return_value=browser_cookies)
    monkeypatch.setattr(auth.browser_cookie3, browser.replace("-", "_"), reader)

    cli.main(["auth", "import", "--browser", browser])

    reader.assert_called_once_with(domain_name="linkedin.com")
    assert auth.load_auth().browser == browser


def test_import_filters_unrelated_expired_and_out_of_scope_cookies(reader, browser_cookies, auth_path):
    for name, domain, path, expires, value in [
        ("tracking", ".linkedin.com", "/", None, "unrelated-tracking-token"),
        ("li_at", ".notlinkedin.com", "/", None, "unrelated-domain-token"),
        ("li_at", ".linkedin.com.example.org", "/", None, "unrelated-suffix-token"),
        ("li_at", "jobs.linkedin.com", "/", None, "unrelated-host-token"),
        ("li_at", ".linkedin.com", "/jobs", None, "unrelated-path-token"),
        ("li_at", "www.linkedin.com", "/", 1, "expired-token"),
        ("JSESSIONID", ".linkedin.com", "/", None, "invalid;token"),
    ]:
        browser_cookies.set(name, value, domain=domain, path=path, expires=expires)

    auth.import_browser("chrome")

    saved = auth.load_cookies()
    assert saved.get_dict() == {
        "li_at": "private-linkedin-token",
        "JSESSIONID": '"ajax:private-csrf-token"',
    }
    assert len(saved) == 2
    assert "unrelated" not in auth_path.read_text()
    assert "expired-token" not in auth_path.read_text()
    assert "invalid;token" not in auth_path.read_text()


def test_imported_session_reaches_linkedin_with_csrf_and_cookie_attributes(reader, browser_cookies):
    # A broader JSESSIONID must not conflict with the www cookie in the API's
    # lookup by name, or replace the token associated with that scope.
    browser_cookies.set("JSESSIONID", '"ajax:broader-token"', domain=".linkedin.com")
    auth.import_browser("chrome")

    api = cli.build_api()
    session = api.client.session
    request = session.prepare_request(Request("GET", "https://www.linkedin.com/voyager/api/me"))

    assert request.headers["csrf-token"] == "ajax:private-csrf-token"
    assert "li_at=private-linkedin-token" in request.headers["Cookie"]
    assert 'JSESSIONID="ajax:private-csrf-token"' in request.headers["Cookie"]
    assert "broader-token" not in request.headers["Cookie"]
    cookies = {cookie.name: cookie for cookie in session.cookies}
    assert cookies["li_at"].expires == 4102444800
    assert cookies["li_at"].domain == ".linkedin.com"
    assert cookies["JSESSIONID"].expires is None
    assert cookies["JSESSIONID"].domain_specified is False
    assert all(cookie.secure for cookie in session.cookies)
    assert "Cookie" not in session.prepare_request(Request("GET", "https://example.org/")).headers
    assert "Cookie" not in session.prepare_request(Request("GET", "http://www.linkedin.com/")).headers


def test_import_and_status_report_metadata_without_cookie_values(reader, capsys, auth_path):
    cli.main(["auth", "import", "--browser", "Chrome"])
    before = auth_path.read_bytes()
    reader.reset_mock()
    cli.main(["auth", "status"])

    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.count("Status: ready (local expiry check)") == 2
    assert "Browser: chrome" in output.out
    assert "Imported:" in output.out
    assert str(auth_path) in output.out
    assert "Cookie: JSESSIONID; expires: session" in output.out
    assert "Cookie: li_at; expires: 2100-01-01T00:00:00+00:00" in output.out
    assert "private-linkedin-token" not in output.out
    assert "private-csrf-token" not in output.out
    assert auth_path.read_bytes() == before
    reader.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="Unix file permissions")
def test_auth_store_is_private_even_when_replacing_an_existing_file(reader, auth_path):
    auth_path.parent.mkdir()
    auth_path.write_text("previous session")
    auth_path.chmod(0o644)

    auth.import_browser("chrome")

    assert auth_path.stat().st_mode & 0o777 == 0o600
    assert list(auth_path.parent.iterdir()) == [auth_path]


@pytest.mark.parametrize("condition", ["empty", "missing", "expired", "empty_value"])
def test_unusable_import_preserves_previous_session(condition, reader, browser_cookies, auth_path):
    auth.import_browser("chrome")
    before = auth_path.read_bytes()
    if condition == "empty":
        browser_cookies.clear()
    elif condition == "missing":
        browser_cookies.clear(".linkedin.com", "/", "li_at")
    else:
        cookie = next(cookie for cookie in browser_cookies if cookie.name == "li_at")
        if condition == "expired":
            cookie.expires = 1
        else:
            cookie.value = ""

    with pytest.raises(auth.AuthError, match="No usable LinkedIn session"):
        auth.import_browser("chrome")

    assert auth_path.read_bytes() == before


def test_browser_error_preserves_session_and_does_not_echo_secrets(reader, auth_path, capsys):
    auth.import_browser("chrome")
    before = auth_path.read_bytes()
    reader.side_effect = RuntimeError("private-linkedin-token")

    with pytest.raises(SystemExit) as error:
        cli.main(["auth", "import", "--browser", "chrome"])

    assert error.value.code == 2
    output = capsys.readouterr()
    assert "Could not read chrome cookies" in output.err
    assert "private-linkedin-token" not in output.out + output.err
    assert auth_path.read_bytes() == before


def test_failed_save_preserves_previous_session_and_cleans_up(reader, auth_path, monkeypatch):
    auth.import_browser("chrome")
    before = auth_path.read_bytes()
    monkeypatch.setattr(Path, "replace", Mock(side_effect=PermissionError("cannot replace")))

    with pytest.raises(auth.AuthError, match="Could not save"):
        auth.import_browser("chrome")

    assert auth_path.read_bytes() == before
    assert list(auth_path.parent.iterdir()) == [auth_path]


@pytest.mark.parametrize("command", [
    ["auth", "status"], ["company", "Acme"], ["skill", "Python"],
    ["human", "https://www.linkedin.com/in/example/"],
])
def test_missing_auth_has_import_guidance_without_browser_or_api_access(command, reader, monkeypatch, capsys, auth_path):
    linkedin = Mock()
    monkeypatch.setattr(cli, "Linkedin", linkedin)

    with pytest.raises(SystemExit) as error:
        cli.main(command)

    assert error.value.code == 2
    assert "warmpath auth import --browser" in capsys.readouterr().err
    assert not auth_path.exists()
    reader.assert_not_called()
    linkedin.assert_not_called()


@pytest.mark.parametrize("content", [b"", b"[]", b"{}", b"private-token", b"\xff"])
def test_corrupt_store_has_reimport_guidance(content, auth_path, capsys):
    auth_path.parent.mkdir()
    auth_path.write_bytes(content)

    with pytest.raises(SystemExit) as error:
        cli.main(["auth", "status"])

    assert error.value.code == 2
    output = capsys.readouterr()
    assert "saved LinkedIn session is invalid" in output.err
    assert "warmpath auth import --browser" in output.err
    assert "private-token" not in output.out + output.err
    assert auth_path.read_bytes() == content


@pytest.mark.parametrize("field,value", [
    ("domain", "example.org"), ("path", "/jobs"), ("secure", "true"),
    ("expires", "tomorrow"), ("expires", 10**20),
    ("domain_specified", False), ("value", "token\r\nInjected: header"),
])
def test_invalid_stored_cookie_is_rejected(field, value, reader, auth_path):
    auth.import_browser("chrome")
    payload = json.loads(auth_path.read_text())
    payload["cookies"][0][field] = value
    auth_path.write_text(json.dumps(payload))

    with pytest.raises(auth.AuthError, match="saved LinkedIn session is invalid"):
        auth.load_cookies()


def test_expired_session_is_reported_and_rejected_without_reimport(reader, capsys):
    session = auth.import_browser("chrome")
    next(cookie for cookie in session.cookies if cookie.name == "li_at").expires = 1
    auth.save_auth(session)
    reader.reset_mock()

    with pytest.raises(SystemExit) as status_error:
        cli.main(["auth", "status"])

    assert status_error.value.code == 1
    output = capsys.readouterr()
    assert "Status: expired or incomplete" in output.out
    assert "Missing or expired: li_at" in output.out
    assert "warmpath auth import --browser chrome" in output.out

    with pytest.raises(SystemExit) as api_error:
        cli.build_api()

    assert api_error.value.code == 2
    assert "warmpath auth import --browser chrome" in capsys.readouterr().err
    reader.assert_not_called()


@pytest.mark.parametrize("arguments", [[], ["import"], ["import", "--browser", "unknown"], ["login"]])
def test_auth_rejects_missing_and_unknown_arguments(arguments, reader):
    with pytest.raises(SystemExit) as error:
        cli.main(["auth", *arguments])

    assert error.value.code == 2
    reader.assert_not_called()


@pytest.mark.parametrize("command,target", [("company", "Acme"), ("skill", "Python"), ("human", "example")])
def test_manual_cookie_option_is_removed(command, target, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main([command, target, "--cookie-file", "unused"])

    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
