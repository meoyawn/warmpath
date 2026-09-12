import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
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
# Keep the browser's existing device/security context as well as the two required
# tokens. Dropping these made imports differ from the previously working exports.
SESSION_COOKIES = REQUIRED_COOKIES | {
    "bcookie", "bscookie", "lidc", "liap", "li_gc", "li_mc", "li_rm", "lang",
    "dfpfpt", "fptctx2", "li_ep_auth_context", "li_ep_auth-cookie", "chp_token",
    "li_cu", "fid", "fcookie", "ccookie", "__cf_bm",
}
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
        cookie.name in SESSION_COOKIES
        and cookie.domain.lstrip(".").lower() in {"linkedin.com", "www.linkedin.com"}
        and (cookie.domain_specified or cookie.domain.lower() in {"linkedin.com", "www.linkedin.com"})
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
    user_agent: str | None = None

    def missing_cookies(self) -> list[str]:
        return sorted(REQUIRED_COOKIES - set(select_cookies(self.cookies).keys()))


def save_auth(session: AuthSession) -> None:
    path = auth_store_path()
    payload = {
        "version": 1,
        "browser": session.browser,
        "imported_at": session.imported_at.isoformat(),
        "user_agent": session.user_agent,
        "cookies": [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "domain_specified": cookie.domain_specified,
                "path": cookie.path,
                "secure": bool(cookie.secure),
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


def browser_user_agent(browser: str) -> str | None:
    """Use the installed Chromium version, not the API library's Chrome 83 UA."""
    apps = {"chrome": "Google Chrome", "chromium": "Chromium", "edge": "Microsoft Edge"}
    if browser not in apps:
        return None
    version = None
    if sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            try:
                info = plistlib.loads((root / f"{apps[browser]}.app/Contents/Info.plist").read_bytes())
                version = info.get("CFBundleShortVersionString")
                break
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
        platform = "Macintosh; Intel Mac OS X 10_15_7"
    elif sys.platform.startswith("linux"):
        commands = {"chrome": ("google-chrome", "google-chrome-stable"), "chromium": ("chromium", "chromium-browser"), "edge": ("microsoft-edge",)}
        for command in commands[browser]:
            executable = shutil.which(command)
            if executable:
                try:
                    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, check=True)
                    match = re.search(r"\b(\d+)(?:\.\d+){2,3}\b", result.stdout)
                    version = match.group(0) if match else None
                    break
                except (OSError, subprocess.SubprocessError):
                    continue
        platform = "X11; Linux x86_64"
    elif sys.platform == "win32":
        # Chromium records the current version alongside its profile data.
        roots = {"chrome": "Google/Chrome", "chromium": "Chromium", "edge": "Microsoft/Edge"}
        try:
            version = (Path(os.environ["LOCALAPPDATA"]) / roots[browser] / "User Data/Last Version").read_text().strip()
        except (KeyError, OSError):
            return None
        platform = "Windows NT 10.0; Win64; x64"
    else:
        return None
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)*", version):
        return None
    major = version.split(".")[0]
    agent = f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    if browser == "edge":
        agent += f" Edg/{major}.0.0.0"
    return agent


def import_browser(browser: str, *, save: bool = True) -> AuthSession:
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

    session = AuthSession(browser, datetime.now(timezone.utc), select_cookies(source), browser_user_agent(browser))
    missing = session.missing_cookies()
    if missing:
        raise AuthError(
            f"No usable LinkedIn session in {browser}: missing or expired "
            f"{', '.join(missing)}. Sign in to LinkedIn in a regular browser window, "
            f"then run warmpath auth import --browser {browser} again."
        )
    if save:
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
            or (
                payload.get("user_agent") is not None
                and (
                    not isinstance(payload["user_agent"], str)
                    or not payload["user_agent"]
                    or not payload["user_agent"].isascii()
                    or any(char in payload["user_agent"] for char in "\r\n")
                )
            )
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
                # Older imports saved browser-cookie3's integer 0/1 Secure flag.
                or type(record.get("secure")) not in (bool, int)
                or record["secure"] not in (0, 1)
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
                secure=bool(record["secure"]),
                expires=record.get("expires"),
            )
            cookie.domain_specified = record["domain_specified"]
            if not is_auth_cookie(cookie) or cookie.name in jar:
                raise ValueError("Invalid stored cookie scope")
            jar.set_cookie(cookie)
        return AuthSession(payload["browser"], imported_at, jar, payload.get("user_agent"))
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
