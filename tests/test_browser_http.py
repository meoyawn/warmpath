from unittest.mock import Mock
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from curl_cffi import requests as curl_requests
from curl_cffi.requests import exceptions as curl_errors
from requests import Session
from requests.exceptions import ConnectionError, SSLError, Timeout

from warmpath import browser_http


def reply(status=200, *, headers=(), content=b'{"ok":true}'):
    result = curl_requests.Response()
    result.status_code = status
    result.url = "https://www.linkedin.com/voyager/api/me"
    result.reason = "OK"
    result.headers = curl_requests.Headers(headers)
    result.encoding = "utf-8"
    result.elapsed = timedelta(seconds=0.1)
    result.content = content
    result.iter_content = Mock(return_value=iter([content]))
    result.close = Mock()
    return result


@pytest.fixture
def transport(monkeypatch):
    backend = Mock()
    factory = Mock(return_value=backend)
    monkeypatch.setattr(browser_http.curl_requests, "Session", factory)
    session = Session()
    session.headers["user-agent"] = "Mozilla/5.0 (Macintosh) Chrome/152.0.0.0 Safari/537.36"
    session.mount("https://www.linkedin.com/", browser_http.BrowserAdapter("chrome"))
    factory.assert_called_once_with(discard_cookies=True)
    return session, backend


def test_browser_transport_uses_tls_impersonation_and_fetch_headers(transport):
    session, backend = transport
    backend.request.return_value = reply()
    session.cookies.set("li_at", "private-token", domain=".linkedin.com", secure=True)

    response = session.get("https://www.linkedin.com/voyager/api/me", timeout=15)

    assert response.json() == {"ok": True}
    backend.request.assert_called_once()
    arguments = backend.request.call_args.kwargs
    assert arguments["impersonate"].startswith("chrome")
    assert int(arguments["impersonate"].removeprefix("chrome")) <= 152
    assert arguments["headers"]["sec-fetch-mode"] == "cors"
    assert arguments["headers"]["sec-fetch-user"] is None
    assert arguments["headers"]["upgrade-insecure-requests"] is None
    assert arguments["headers"]["sec-ch-ua-platform"] == '"macOS"'
    assert '"Chromium";v="152"' in arguments["headers"]["sec-ch-ua"]
    assert "li_at=private-token" in arguments["headers"]["Cookie"]
    assert "Connection" not in arguments["headers"]
    assert "Accept-Encoding" not in arguments["headers"]
    assert arguments["allow_redirects"] is False
    assert arguments["verify"] is True
    assert arguments["timeout"] == 15


def test_set_cookie_rotation_and_deletion_use_requests_cookie_policy(transport):
    session, backend = transport
    session.cookies.set("li_at", "old-token", domain=".linkedin.com", secure=True)
    session.cookies.set("lidc", "old-route", domain=".linkedin.com", secure=True)
    backend.request.side_effect = [reply(headers=[
        ("Set-Cookie", "li_at=new-token; Domain=.linkedin.com; Path=/; Secure"),
        ("Set-Cookie", "lidc=; Domain=.linkedin.com; Path=/; Max-Age=0; Secure"),
    ]), reply()]

    first = session.get("https://www.linkedin.com/voyager/api/me")
    session.get("https://www.linkedin.com/voyager/api/me")

    assert first.cookies.get("li_at") == "new-token"
    assert session.cookies.get("li_at") == "new-token"
    assert "lidc" not in session.cookies
    cookie_header = backend.request.call_args.kwargs["headers"]["Cookie"]
    assert "li_at=new-token" in cookie_header
    assert "old-token" not in cookie_header
    assert "lidc=" not in cookie_header


def test_streamed_unicode_is_decoded_by_requests_and_closed(transport):
    session, backend = transport
    upstream = reply(content="Привет".encode())
    backend.request.return_value = upstream

    with session.get("https://www.linkedin.com/in/example/", stream=True) as response:
        assert list(response.iter_content(chunk_size=8192, decode_unicode=True)) == ["Привет"]

    upstream.close.assert_called()


@pytest.mark.parametrize("original,expected", [
    (curl_errors.Timeout, Timeout),
    (curl_errors.SSLError, SSLError),
    (curl_errors.ConnectionError, ConnectionError),
])
def test_transport_errors_keep_existing_cli_error_handling(transport, original, expected):
    session, backend = transport
    backend.request.side_effect = original("private-token")

    with pytest.raises(expected) as error:
        session.get("https://www.linkedin.com/voyager/api/me")

    assert "private-token" not in str(error.value)
    backend.request.assert_called_once()


@pytest.mark.parametrize("browser,agent,target", [
    ("chrome", "Chrome/131.0.0.0", "chrome131"),
    ("edge", "Chrome/131.0.0.0 Edg/131.0.0.0", "chrome131"),
    ("firefox", "Firefox/135.0", "firefox135"),
    ("librewolf", None, "firefox"),
    ("safari", None, "safari"),
])
def test_browser_family_and_supported_version_are_selected(browser, agent, target):
    assert browser_http.impersonation_target(browser, agent) == target


def test_real_transport_handles_compression_redirects_and_cookies():
    import gzip

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/final")
                self.send_header("Set-Cookie", "test_cookie=rotated; Path=/")
                self.end_headers()
                return
            assert self.headers.get("Cookie") == "test_cookie=rotated"
            content = gzip.compress('{"message":"Привет"}'.encode())
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    worker.start()
    try:
        with Session() as session:
            session.trust_env = False
            session.mount("http://127.0.0.1:", browser_http.BrowserAdapter("chrome"))
            response = session.get(f"http://127.0.0.1:{server.server_port}/start", timeout=5)
            assert response.json() == {"message": "Привет"}
            assert len(response.history) == 1
            assert response.elapsed >= timedelta(0)
            assert session.cookies.get("test_cookie") == "rotated"
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
