"""Browser TLS/HTTP transport with the Requests interface used by the API client."""

import re
from email.message import Message
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests
from curl_cffi.requests import exceptions as curl_errors
from curl_cffi.requests.impersonate import BrowserType
from requests import PreparedRequest, Response
from requests.adapters import BaseAdapter
from requests.cookies import extract_cookies_to_jar
from requests.exceptions import ConnectionError, RequestException, SSLError, Timeout
from requests.structures import CaseInsensitiveDict


def impersonation_target(browser: str, user_agent: str | None) -> str:
    family = "firefox" if browser in {"firefox", "librewolf"} else "safari" if browser == "safari" else "chrome"
    match = re.search(r"(?:Chrome|Firefox)/(\d+)", user_agent or "")
    if match and family in {"chrome", "firefox"}:
        major = int(match.group(1))
        targets = [
            (int(found.group(1)), item.value) for item in BrowserType
            if (found := re.fullmatch(rf"{family}(\d+)[a-z]?", item.value))
            and int(found.group(1)) <= major
        ]
        if targets:
            return max(targets)[1]
    return family


def translate_error(error: curl_errors.RequestException, request: PreparedRequest) -> RequestException:
    error_type = (
        Timeout if isinstance(error, curl_errors.Timeout) else
        SSLError if isinstance(error, curl_errors.SSLError) else ConnectionError
    )
    return error_type("The browser HTTP transport could not complete the request.", request=request)


class BrowserBody:
    def __init__(self, response: Any, request: PreparedRequest):
        self.response = response
        self.request = request
        headers = Message()
        for key, value in response.headers.multi_items():
            if value is not None:
                headers.add_header(key, value)
        # Requests' cookie policy handles Set-Cookie, including deletion, scope,
        # and redirects. Keep one cookie engine instead of a second curl jar.
        self._original_response = SimpleNamespace(msg=headers)

    def stream(self, amount: int, decode_content: bool = True):
        try:
            yield from self.response.iter_content()
        except curl_errors.RequestException as exc:
            raise translate_error(exc, self.request) from exc

    def close(self) -> None:
        self.response.close()

    def release_conn(self) -> None:
        self.close()


class BrowserAdapter(BaseAdapter):
    def __init__(self, browser: str):
        self.browser = browser
        self.backend: Any = curl_requests.Session(discard_cookies=True)

    def send(self, request: PreparedRequest, stream=False, timeout=None, verify=True, cert=None, proxies=None) -> Response:
        headers: dict[str, Any] = dict(request.headers)
        # Let the impersonation preset supply browser connection/compression
        # defaults; Requests defaults would override them with Python's values.
        for key in list(headers):
            if key.lower() in {"connection", "accept-encoding"}:
                del headers[key]
        agent = request.headers.get("user-agent") or ""
        if isinstance(agent, bytes):
            agent = agent.decode("ascii")
        major = re.search(r"Chrome/(\d+)", agent)
        if major:
            brand = "Microsoft Edge" if self.browser == "edge" else "Google Chrome"
            headers["sec-ch-ua"] = f'"Chromium";v="{major[1]}", "{brand}";v="{major[1]}", "Not_A Brand";v="99"'
            headers["sec-ch-ua-mobile"] = "?0"
            platform = "Windows" if "Windows" in agent else "Linux" if "Linux" in agent else "macOS"
            headers["sec-ch-ua-platform"] = f'"{platform}"'
        if urlparse(request.url or "").path.startswith("/voyager/api/"):
            headers.update({
                "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin", "sec-fetch-user": None,
                "upgrade-insecure-requests": None, "priority": "u=1, i",
                "referer": "https://www.linkedin.com/feed/",
            })
        try:
            received = self.backend.request(
                request.method, request.url, headers=headers, data=request.body,
                impersonate=impersonation_target(self.browser, agent),
                accept_encoding="gzip, deflate, br, zstd", allow_redirects=False,
                stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies,
            )
        except curl_errors.RequestException as exc:
            raise translate_error(exc, request) from exc
        response = Response()
        response.status_code = received.status_code
        response.url = received.url
        response.reason = received.reason
        response.headers = CaseInsensitiveDict(received.headers.items())
        response.encoding = received.encoding
        response.elapsed = received.elapsed
        response.request = request
        response.connection = cast(Any, self)
        response.raw = BrowserBody(received, request)
        extract_cookies_to_jar(response.cookies, request, response.raw)
        if not stream:
            response._content = received.content
            response._content_consumed = True
        return response

    def close(self) -> None:
        self.backend.close()
