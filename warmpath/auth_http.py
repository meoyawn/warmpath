"""Keep imported browser sessions usable across read-only LinkedIn requests."""

from copy import deepcopy
from time import monotonic, sleep
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from requests import Response, Session
from requests.exceptions import TooManyRedirects

from warmpath import auth, browser_http


MIN_REQUEST_INTERVAL = 0.5


def cookie_state(cookies: Any) -> tuple:
    return tuple(sorted(
        (cookie.name, cookie.domain, cookie.path, cookie.value, cookie.expires)
        for cookie in auth.select_cookies(cookies)
    ))


class SessionRequests:
    """Wrap requests once, covering API calls and direct profile-page reads.

    Healthy requests do no additional network or browser I/O. A rejected session
    can refresh once per request, and only GET/HEAD requests are retried.
    """

    def __init__(
        self,
        http: Session,
        session: auth.AuthSession,
        *,
        refresh: bool,
        persist: bool,
    ):
        self.http = http
        self.session = session
        self.refresh = refresh
        self.persist = persist
        self.original_request = http.request
        self.saved_state = cookie_state(session.cookies)
        self.last_request_at = 0.0

    def sync_csrf(self) -> None:
        cookies = auth.select_cookies(self.http.cookies)
        # Set-Cookie can introduce the same token at a more specific domain.
        # Send one token per name, matching the CSRF header's chosen value.
        self.http.cookies = cookies
        token = cookies.get("JSESSIONID")
        if token:
            self.http.headers["csrf-token"] = token.strip('"')

    @staticmethod
    def rejected(response: Response) -> bool:
        if response.status_code == 401:
            return True
        url = response.headers.get("location", "") or response.url or ""
        path = urlparse(url).path
        return path.startswith(("/login", "/uas/login", "/checkpoint", "/authwall"))

    def refresh_cookies(self) -> None:
        # Another command may already have imported a replacement. Reuse it
        # before accessing the browser/keychain again.
        candidate = auth.load_auth()
        if candidate.missing_cookies() or cookie_state(candidate.cookies) == self.saved_state:
            candidate = auth.import_browser(self.session.browser, save=False)
        if cookie_state(candidate.cookies) == self.saved_state:
            raise auth.AuthError(
                "LinkedIn rejected the session and the browser has the same cookies. "
                f"Sign in to LinkedIn again in {self.session.browser}, then retry."
            )
        self.session = candidate
        self.http.cookies = auth.select_cookies(candidate.cookies)
        if candidate.user_agent:
            self.http.headers["user-agent"] = candidate.user_agent
        else:
            self.http.headers.pop("user-agent", None)
        adapter = self.http.get_adapter("https://www.linkedin.com/")
        if isinstance(adapter, browser_http.BrowserAdapter):
            adapter.browser = candidate.browser
        self.sync_csrf()

    def send(self, method: str, url: str, *args: Any, **kwargs: Any) -> Response:
        wait = MIN_REQUEST_INTERVAL - (monotonic() - self.last_request_at)
        if wait > 0:
            sleep(wait)
        self.last_request_at = monotonic()
        return self.original_request(method, url, *args, **kwargs)

    def save_cookies(self) -> None:
        cookies = auth.select_cookies(self.http.cookies)
        if auth.REQUIRED_COOKIES - set(cookies.keys()):
            raise auth.AuthError("LinkedIn cleared the authentication cookies. Sign in again and retry.")
        current_state = cookie_state(cookies)
        # Include Set-Cookie replacements received during import validation.
        self.session.cookies = cookies
        if self.persist and current_state != self.saved_state:
            stored = auth.load_auth()
            # Do not overwrite a newer import made by another CLI process.
            if cookie_state(stored.cookies) == self.saved_state:
                auth.save_auth(self.session)
                self.saved_state = current_state

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Response:
        is_api = urlparse(url).path.startswith("/voyager/api/")
        follow_redirects = kwargs.get("allow_redirects", True)
        kwargs["allow_redirects"] = False
        for _ in range(4):
            if "logout" in unquote(urlparse(url).path).lower().split("/"):
                raise auth.AuthError("Refusing a LinkedIn logout request to preserve the browser session.")
            self.sync_csrf()
            response = self.send(method, url, *args, **kwargs)
            if not response.is_redirect or self.rejected(response) or not follow_redirects:
                return response
            target = urljoin(url, response.headers["location"])
            parsed = urlparse(target)
            if (
                method.upper() not in {"GET", "HEAD"}
                or parsed.scheme != "https"
                or parsed.hostname not in {"www.linkedin.com", "linkedin.com"}
                or (is_api and not parsed.path.startswith("/voyager/api/"))
            ):
                response.close()
                raise auth.AuthError("LinkedIn redirected outside the expected read-only endpoint; the request was stopped.")
            # LinkedIn sometimes sets __cf_bm and redirects to the same API URL.
            # Follow these bounded redirects, retaining the returned cookies.
            response.close()
            url = target
            kwargs.pop("params", None)
        raise TooManyRedirects("LinkedIn repeatedly redirected the request.")

    def __call__(self, method: str, url: str | bytes, *args: Any, **kwargs: Any) -> Response:
        if isinstance(url, bytes):
            url = url.decode("utf-8")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.linkedin.com", "linkedin.com"}:
            return self.original_request(method, url, *args, **kwargs)

        is_api = parsed.path.startswith("/voyager/api/")
        kwargs.setdefault("timeout", 30)
        self.sync_csrf()
        before = deepcopy(self.http.cookies)
        response = self.request(method, url, *args, **kwargs)
        rejected = self.rejected(response)
        if response.status_code == 403:
            # A private profile can return 403 with a healthy session. Only
            # refresh when the identity endpoint also rejects the session.
            if parsed.path == "/voyager/api/me":
                rejected = True
            elif self.refresh and method.upper() in {"GET", "HEAD"}:
                check = self.request("GET", "https://www.linkedin.com/voyager/api/me", timeout=15)
                rejected = self.rejected(check) or check.status_code == 403
                check.close()

        if rejected:
            self.http.cookies = before
            self.sync_csrf()
            if not self.refresh or method.upper() not in {"GET", "HEAD"}:
                response.close()
                raise auth.AuthError(
                    "LinkedIn rejected the session. Sign in to LinkedIn again, "
                    f"then retry. {auth.IMPORT_HINT}"
                )
            response.close()
            self.refresh_cookies()
            response = self.request(method, url, *args, **kwargs)
            if self.rejected(response) or response.status_code == 403:
                response.close()
                raise auth.AuthError(
                    f"LinkedIn also rejected the current {self.session.browser} session. "
                    "Sign in to LinkedIn again in that browser, then retry."
                )

        if is_api:
            # The upstream search helper otherwise turns errors into empty lists.
            response.raise_for_status()
        if 200 <= response.status_code < 300:
            self.save_cookies()
        return response
