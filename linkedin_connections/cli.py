import argparse
import csv
import http.cookiejar
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, NoReturn
from urllib.parse import unquote, urlparse

from requests.cookies import RequestsCookieJar

DEFAULT_COOKIE_FILE = Path("cookies/linkedin.cookies")


PROFILE_URL_RE = re.compile(r"/in/([^/?#]+)/?")


def fail(message: str, exit_code: int = 1) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(exit_code)


def profile_slug(value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        match = PROFILE_URL_RE.search(urlparse(value).path)
        if not match:
            fail(f"Expected LinkedIn /in/ profile URL, got: {value}", 2)
        return match.group(1)
    return value.strip().strip("/")


def load_netscape_cookies(path: Path) -> RequestsCookieJar:
    source = http.cookiejar.MozillaCookieJar()
    source.load(str(path), ignore_discard=True, ignore_expires=True)

    jar = RequestsCookieJar()
    for cookie in source:
        jar.set_cookie(cookie)
    return jar


def load_cookies(path: Path) -> RequestsCookieJar:
    if not path.exists() or path.stat().st_size == 0:
        fail(
            "\n".join(
                [
                    f"Cookie file empty: {path}",
                    "Paste LinkedIn cookies.txt there, then rerun.",
                    "Accepted format: Netscape cookies.txt from Get cookies.txt LOCALLY.",
                    "Required cookies: li_at and JSESSIONID.",
                ]
            ),
            2,
        )

    try:
        jar = load_netscape_cookies(path)
    except http.cookiejar.LoadError as exc:
        fail(f"Could not load Netscape cookies.txt: {exc}", 2)

    names = {cookie.name for cookie in jar}
    missing = {"li_at", "JSESSIONID"} - names
    if missing:
        fail(f"Missing required cookie(s): {', '.join(sorted(missing))}", 2)
    return jar


def no_delay() -> None:
    return None


def use_fast_fetches(api: Any) -> None:
    original_fetch: Callable[..., Any] = api._fetch
    original_post: Callable[..., Any] = api._post

    def fast_fetch(uri: str, *args: Any, **kwargs: Any) -> Any:
        kwargs["evade"] = no_delay
        return original_fetch(uri, *args, **kwargs)

    def fast_post(uri: str, *args: Any, **kwargs: Any) -> Any:
        kwargs["evade"] = no_delay
        return original_post(uri, *args, **kwargs)

    api._fetch = fast_fetch
    api._post = fast_post


def profile_urn_id(api: Any, public_id: str) -> str:
    urn_id = profile_urn_id_from_html(api, public_id)
    if urn_id:
        return urn_id

    try:
        profile = api.get_profile(public_id=public_id)
    except KeyError:
        urn_id = profile_urn_id_from_html(api, public_id)
        if urn_id:
            return urn_id
        fail(f"Could not find fsd_profile URN in profile page HTML for {public_id}")
    if not profile:
        fail(f"Could not read profile: https://www.linkedin.com/in/{public_id}/")

    urn_id = profile.get("urn_id")
    if not urn_id and profile.get("profile_urn"):
        urn_id = profile["profile_urn"].split(":")[-1]
    if not urn_id:
        fail(f"Could not find URN for profile: {public_id}")
    return urn_id


def profile_urn_id_from_html(api: Any, public_id: str) -> str | None:
    patterns = [
        re.compile(r"urn:li:fsd_profile:([A-Za-z0-9_-]+)"),
        re.compile(r"ref([A-Za-z0-9_-]+)Topcard"),
    ]

    with api.client.session.get(
        f"https://www.linkedin.com/in/{public_id}/", stream=True
    ) as res:
        if res.status_code != 200:
            return None

        text = ""
        for chunk in res.iter_content(chunk_size=8192, decode_unicode=True):
            if not chunk:
                continue

            if isinstance(chunk, bytes):
                chunk = chunk.decode(res.encoding or "utf-8", errors="ignore")

            text = (text + chunk)[-40000:]
            decoded = unquote(text)
            for pattern in patterns:
                match = pattern.search(decoded)
                if match:
                    return match.group(1)

    return None


def canonical_profile_url(value: str) -> str | None:
    decoded = unquote(value)
    parsed = urlparse(decoded)
    match = PROFILE_URL_RE.search(parsed.path)
    if not match:
        match = PROFILE_URL_RE.search(decoded)
    if not match:
        return None
    return f"https://www.linkedin.com/in/{match.group(1)}/"


def profile_url_from_result(result: dict[str, Any]) -> str | None:
    preferred_keys = ("navigationUrl", "profileUrl", "targetUrl", "url")
    for key in preferred_keys:
        value = result.get(key)
        if isinstance(value, str):
            url = canonical_profile_url(value)
            if url:
                return url

    stack: list[Any] = [result]

    while stack:
        value = stack.pop()
        if isinstance(value, str):
            url = canonical_profile_url(value)
            if url:
                return url
        elif isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)

    return None


def text_field(result: dict[str, Any], key: str) -> str | None:
    value = result.get(key)
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
    return None


def urn_id_from_result(result: dict[str, Any]) -> str | None:
    urn = result.get("entityUrn")
    if not isinstance(urn, str):
        return None

    match = re.search(r"(?:fsd_profile|fs_miniProfile):([^,)]+)", urn)
    if match:
        return match.group(1)
    return urn.rsplit(":", maxsplit=1)[-1]


def connection_result_row(result: dict[str, Any]) -> dict[str, str | None]:
    tracking = result.get("entityCustomTrackingInfo")
    if not isinstance(tracking, dict):
        tracking = {}

    return {
        "name": text_field(result, "title"),
        "distance": tracking.get("memberDistance"),
        "jobtitle": text_field(result, "primarySubtitle"),
        "location": text_field(result, "secondarySubtitle"),
        "urn_id": urn_id_from_result(result),
        "url": profile_url_from_result(result),
    }


def fetch_connection_rows(api, urn_id: str, limit: int) -> list[dict[str, str | None]]:
    params = {
        "filters": (
            "List((key:resultType,value:List(PEOPLE)),"
            f"(key:connectionOf,value:List({urn_id})))"
        )
    }
    rows: list[dict[str, str | None]] = []
    seen_urls: set[str] = set()
    offset = 0

    while len(rows) < limit:
        remaining = limit - len(rows)
        batch_limit = min(49, max(2, remaining + 10))
        results = api.search(params, limit=batch_limit, offset=offset)
        if not results:
            break

        offset += len(results)
        for result in results:
            row = connection_result_row(result)
            url = row["url"]
            if not url or url in seen_urls:
                continue

            seen_urls.add(url)
            rows.append(row)
            if len(rows) == limit:
                break

        if len(results) < batch_limit:
            break

    if len(rows) < limit:
        fail(f"Only found {len(rows)} profile URL(s), expected {limit}.")

    return rows


def write_csv(path: Path, rows: list[dict[str, str | None]]) -> None:
    fieldnames = ["url", "name", "distance", "jobtitle", "location", "urn_id"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape visible LinkedIn connections for an arbitrary profile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  uv run python linkedin_connections/cli.py --profile https://www.linkedin.com/in/mitchellh/ --limit 50
  uv run python linkedin_connections/cli.py --profile mitchellh --limit 50 --csv-out mitchellh_connections.csv
  uv run linkedin-connections --profile mitchellh --json-out mitchellh_connections.json --csv-out mitchellh_connections.csv

cookies:
  Paste Netscape cookies.txt from Get cookies.txt LOCALLY into cookies/linkedin.cookies,
  or pass another path with --cookie-file.
""",
    )
    parser.add_argument(
        "--profile", required=True, help="LinkedIn /in/ URL or public slug"
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    parser.add_argument(
        "--json-out", type=Path, help="Write fetched profiles to this JSON file"
    )
    parser.add_argument(
        "--csv-out", type=Path, help="Write fetched profiles to this CSV file"
    )
    args = parser.parse_args()

    cookie_file = args.cookie_file
    if not cookie_file.is_absolute():
        cookie_file = Path.cwd() / cookie_file

    public_id = profile_slug(args.profile)

    cookies = load_cookies(cookie_file)

    from open_linkedin_api import Linkedin

    api = Linkedin("", "", cookies=cookies)
    use_fast_fetches(api)
    urn_id = profile_urn_id(api, public_id)
    rows = fetch_connection_rows(api, urn_id, args.limit)

    wrote = []
    if args.json_out:
        args.json_out.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        wrote.append(str(args.json_out))
    if args.csv_out:
        write_csv(args.csv_out, rows)
        wrote.append(str(args.csv_out))

    for row in rows:
        print(row["url"])

    if wrote:
        print(f"Wrote {len(rows)} rows to {', '.join(wrote)}", file=sys.stderr)


if __name__ == "__main__":
    main()
