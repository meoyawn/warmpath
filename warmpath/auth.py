import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

import browser_cookie3
from platformdirs import user_config_path
from requests.cookies import RequestsCookieJar, create_cookie


BROWSERS = (
    "chrome",
    "chromium",
    "firefox",
    "edge",
    "brave",
    "safari",
    "arc",
    "vivaldi",
    "opera",
    "opera-gx",
    "librewolf",
)
REQUIRED_COOKIES = {"li_at", "JSESSIONID"}
IMPORT_HINT = "Run warmpath auth import --browser chrome (or your browser)."


class AuthError(Exception):
    pass


def auth_store_path() -> Path:
    return user_config_path("warmpath", appauthor=False) / "auth.json"


def is_auth_cookie(cookie: Cookie) -> bool:
    if cookie.expires is not None:
        try:
            datetime.fromtimestamp(cookie.expires, timezone.utc)
        except (ValueError, OverflowError, OSError):
            return False
    return (
        cookie.name in REQUIRED_COOKIES
        and cookie.domain.lstrip(".").lower() in {"linkedin.com", "www.linkedin.com"}
        and (cookie.domain_specified or cookie.domain.lower() == "www.linkedin.com")
        and cookie.path == "/"
        and bool(cookie.value)
        and not any(char in (cookie.value or "") for char in "\r\n;")
    )


def select_cookies(source: CookieJar) -> RequestsCookieJar:
    # The LinkedIn client looks up JSESSIONID by name, so keep one cookie per
    # name, preferring the more specific www.linkedin.com scope.
    selected: dict[str, Cookie] = {}
    for cookie in source:
        if not is_auth_cookie(cookie) or cookie.is_expired():
            continue
        if (
            cookie.name not in selected
            or cookie.domain.lstrip(".").lower() == "www.linkedin.com"
        ):
            selected[cookie.name] = cookie
    jar = RequestsCookieJar()
    for cookie in selected.values():
        jar.set_cookie(cookie)
    return jar


@dataclass
class AuthSession:
    browser: str
    imported_at: datetime
    cookies: CookieJar = field(repr=False)

    def missing_cookies(self) -> list[str]:
        return sorted(REQUIRED_COOKIES - set(select_cookies(self.cookies).keys()))


def save_auth(session: AuthSession) -> None:
    path = auth_store_path()
    payload = {
        "version": 1,
        "browser": session.browser,
        "imported_at": session.imported_at.isoformat(),
        "cookies": [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "domain_specified": cookie.domain_specified,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
            }
            for cookie in session.cookies
        ],
    }
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # NamedTemporaryFile creates an owner-only file. Replace only after the
        # complete session is written, preserving the previous import on failure.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".auth-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except OSError as exc:
        raise AuthError(f"Could not save the imported session to {path}.") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_browser(browser: str) -> AuthSession:
    if browser not in BROWSERS:
        raise AuthError(f"Unsupported browser. Choose from: {', '.join(BROWSERS)}.")
    try:
        reader = getattr(browser_cookie3, browser.replace("-", "_"))
        source = reader(domain_name="linkedin.com")
    except Exception as exc:
        # Browser/keyring errors can include sensitive data; do not echo them.
        raise AuthError(
            f"Could not read {browser} cookies ({type(exc).__name__}). "
            "Allow access to the browser's cookie store and keychain/keyring; "
            "close the browser if its cookie store is locked, then retry."
        ) from exc

    session = AuthSession(browser, datetime.now(timezone.utc), select_cookies(source))
    missing = session.missing_cookies()
    if missing:
        raise AuthError(
            f"No usable LinkedIn session in {browser}: missing or expired "
            f"{', '.join(missing)}. Sign in to LinkedIn in a regular browser window, "
            f"then run warmpath auth import --browser {browser} again."
        )
    save_auth(session)
    return session


def load_auth() -> AuthSession:
    path = auth_store_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("browser") not in BROWSERS
            or not isinstance(payload.get("cookies"), list)
        ):
            raise ValueError("Invalid auth store")
        imported_at = datetime.fromisoformat(payload["imported_at"])
        if imported_at.tzinfo is None:
            raise ValueError("Missing import timezone")
        jar = RequestsCookieJar()
        for record in payload["cookies"]:
            if (
                not isinstance(record, dict)
                or any(
                    not isinstance(record.get(key), str)
                    for key in ("name", "value", "domain", "path")
                )
                or type(record.get("secure")) is not bool
                or type(record.get("domain_specified")) is not bool
                or (
                    record.get("expires") is not None
                    and type(record["expires"]) is not int
                )
            ):
                raise ValueError("Invalid stored cookie")
            cookie = create_cookie(
                name=record["name"],
                value=record["value"],
                domain=record["domain"],
                path=record["path"],
                secure=record["secure"],
                expires=record.get("expires"),
            )
            cookie.domain_specified = record["domain_specified"]
            if not is_auth_cookie(cookie) or cookie.name in jar:
                raise ValueError("Invalid stored cookie scope")
            jar.set_cookie(cookie)
        return AuthSession(payload["browser"], imported_at, jar)
    except FileNotFoundError as exc:
        raise AuthError(f"No imported LinkedIn session. {IMPORT_HINT}") from exc
    except OSError as exc:
        raise AuthError(f"Could not read the saved session at {path}. {IMPORT_HINT}") from exc
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise AuthError(f"The saved LinkedIn session is invalid. {IMPORT_HINT}") from exc


def load_cookies() -> RequestsCookieJar:
    session = load_auth()
    missing = session.missing_cookies()
    if missing:
        raise AuthError(
            f"The imported LinkedIn session is missing or expired ({', '.join(missing)}). "
            f"Sign in to LinkedIn, then run warmpath auth import --browser {session.browser}."
        )
    return select_cookies(session.cookies)


def render_status(session: AuthSession) -> str:
    missing = session.missing_cookies()
    lines = [
        f"Status: {'expired or incomplete' if missing else 'ready'} (local expiry check)",
        f"Browser: {session.browser}",
        f"Imported: {session.imported_at.isoformat()}",
        f"Store: {auth_store_path()}",
    ]
    for cookie in sorted(session.cookies, key=lambda cookie: cookie.name):
        expiry = "session"
        if cookie.expires is not None:
            expiry = datetime.fromtimestamp(cookie.expires, timezone.utc).isoformat()
        lines.append(f"Cookie: {cookie.name}; expires: {expiry}")
    if missing:
        lines.append(f"Missing or expired: {', '.join(missing)}")
        lines.append(f"Run warmpath auth import --browser {session.browser} again.")
    return "\n".join(lines)
