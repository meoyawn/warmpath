import argparse
import csv
import hashlib
import http.cookiejar
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, NoReturn
from urllib.parse import unquote, urlparse

from open_linkedin_api import Linkedin
from requests.cookies import RequestsCookieJar

DEFAULT_COOKIE_FILE = Path("cookies/linkedin.cookies")


PROFILE_URL_RE = re.compile(r"/in/([^/?#]+)/?")
COMPANY_URL_RE = re.compile(r"/company/([^/?#]+)/?")
COMPANY_URN_RE = re.compile(
    r"urn:li:(?:fsd_company|fs_normalized_company|company):([^,)]+)"
)

NETWORK_DEPTH_BY_DEGREE = {1: "F", 2: "S"}
DEFAULT_COMPANY_PATH_LIMIT = 25
DEFAULT_CACHE_DIR = Path(".linkedin-cache")


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


def company_slug(value: str) -> str | None:
    if not value.startswith(("http://", "https://")):
        return None

    match = COMPANY_URL_RE.search(urlparse(value).path)
    if not match:
        fail(f"Expected LinkedIn /company/ URL, got: {value}", 2)
    return match.group(1).strip().strip("/")


def company_search_keywords(value: str) -> str:
    slug = company_slug(value)
    if slug:
        return slug.replace("-", " ")
    return value.strip()


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


def resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path


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


def build_api(cookie_file: Path) -> Any:
    cookies = load_cookies(resolve_path(cookie_file))
    api = Linkedin("", "", cookies=cookies)
    use_fast_fetches(api)
    return api


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


def cache_file_path(cache_dir: Path, namespace: str, key: dict[str, Any]) -> Path:
    digest = hashlib.sha256(
        json.dumps(key, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:20]
    return cache_dir / f"{namespace}-{digest}.json"


def cached_json(
    cache_dir: Path,
    namespace: str,
    key: dict[str, Any],
    refresh_cache: bool,
    fetch: Callable[[], Any],
) -> Any:
    path = cache_file_path(cache_dir, namespace, key)
    if not refresh_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    data = fetch()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def company_urn_id_from_value(value: Any) -> str | None:
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            match = COMPANY_URN_RE.search(current)
            if match:
                return match.group(1)
        elif isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return None


def compact_company_payload(
    payload: dict[str, Any], source: str, public_id: str | None = None
) -> dict[str, str | None]:
    name = payload.get("name")
    if not isinstance(name, str):
        name = payload.get("localizedName")
    if not isinstance(name, str):
        name = None

    company_public_id = payload.get("universalName")
    if not isinstance(company_public_id, str):
        company_public_id = public_id

    url = None
    if company_public_id:
        url = f"https://www.linkedin.com/company/{company_public_id}/"

    return {
        "urn_id": company_urn_id_from_value(payload),
        "name": name,
        "headline": None,
        "subline": None,
        "url": url,
        "public_id": company_public_id,
        "source": source,
    }


def compact_company_search_result(
    result: dict[str, Any], source: str
) -> dict[str, str | None]:
    return {
        "urn_id": result.get("urn_id"),
        "name": result.get("name"),
        "headline": result.get("headline"),
        "subline": result.get("subline"),
        "url": None,
        "public_id": None,
        "source": source,
    }


def resolve_company(
    api: Any, company: str, cache_dir: Path, refresh_cache: bool
) -> tuple[dict[str, str | None], list[dict[str, str | None]]]:
    slug = company_slug(company)
    candidates: list[dict[str, str | None]] = []

    if slug:
        try:
            payload = cached_json(
                cache_dir,
                "company-get",
                {"public_id": slug},
                refresh_cache,
                lambda: api.get_company(slug),
            )
        except Exception:
            payload = {}

        if isinstance(payload, dict):
            resolved = compact_company_payload(payload, "get_company", slug)
            if resolved["urn_id"]:
                return resolved, [resolved]

    keywords = company_search_keywords(company)
    raw_results = cached_json(
        cache_dir,
        "company-search",
        {"keywords": keywords},
        refresh_cache,
        lambda: api.search_companies(keywords=[keywords], limit=10),
    )
    if not isinstance(raw_results, list):
        raw_results = []

    for result in raw_results:
        if isinstance(result, dict):
            candidate = compact_company_search_result(result, "search_companies")
            if candidate["urn_id"]:
                candidates.append(candidate)

    if not candidates:
        fail(f"Could not resolve LinkedIn company: {company}")

    normalized_keywords = keywords.casefold()
    selected = candidates[0]
    for candidate in candidates:
        name = candidate.get("name")
        if isinstance(name, str) and name.casefold() == normalized_keywords:
            selected = candidate
            break

    if slug and not selected.get("url"):
        selected["url"] = f"https://www.linkedin.com/company/{slug}/"
        selected["public_id"] = slug

    return selected, candidates


def candidate_score(candidate: dict[str, Any]) -> tuple[int, int]:
    target = candidate.get("target")
    if not isinstance(target, dict):
        target = {}

    title = str(target.get("jobtitle") or "").casefold()
    boost = 0
    if any(word in title for word in ("recruit", "talent", "hiring")):
        boost += 2
    if any(word in title for word in ("engineer", "developer", "manager", "lead")):
        boost += 1

    degree = candidate.get("degree")
    if not isinstance(degree, int):
        degree = 99
    return degree, -boost


def person_target(row: dict[str, Any]) -> dict[str, str | None]:
    return {
        "urn_id": row.get("urn_id"),
        "name": row.get("name"),
        "jobtitle": row.get("jobtitle"),
        "location": row.get("location"),
        "distance": row.get("distance"),
        "url": row.get("url"),
    }


def company_path_candidate(row: dict[str, Any], degree: int) -> dict[str, Any]:
    target = person_target(row)
    search_source = row.get("_search_source")
    if not isinstance(search_source, str):
        search_source = "search_people"
    if degree == 1:
        return {
            "degree": 1,
            "path_status": "resolved",
            "target": target,
            "path": [{"role": "me"}, {"role": "target", **target}],
            "evidence": {"source": search_source, "network_depth": "F"},
        }

    return {
        "degree": degree,
        "path_status": "unresolved",
        "target": target,
        "path": [
            {"role": "me"},
            {"role": "unknown_introducer"},
            {"role": "target", **target},
        ],
        "evidence": {
            "source": search_source,
            "network_depth": NETWORK_DEPTH_BY_DEGREE[degree],
            "note": "LinkedIn search confirmed reachability, but exact introducer was not returned.",
        },
    }


def fetch_company_people(
    api: Any,
    company_urn_id: str,
    company_name: str | None,
    degree: int,
    max_targets: int,
    cache_dir: Path,
    refresh_cache: bool,
) -> list[dict[str, Any]]:
    network_depth = NETWORK_DEPTH_BY_DEGREE[degree]
    rows = cached_json(
        cache_dir,
        "people-search",
        {
            "mode": "current_company",
            "company_urn_id": company_urn_id,
            "network_depth": network_depth,
            "max_targets": max_targets,
        },
        refresh_cache,
        lambda: api.search_people(
            current_company=[company_urn_id],
            network_depths=[network_depth],
            limit=max_targets,
        ),
    )
    if not isinstance(rows, list):
        rows = []

    people = [row for row in rows if isinstance(row, dict)]
    if people:
        for row in people:
            row["_search_source"] = "search_people.current_company"
        return people

    if not company_name:
        return []

    fallback_rows = cached_json(
        cache_dir,
        "people-search",
        {
            "mode": "keyword_company",
            "company_name": company_name,
            "network_depth": network_depth,
            "max_targets": max_targets,
        },
        refresh_cache,
        lambda: api.search_people(
            keyword_company=company_name,
            network_depths=[network_depth],
            limit=max_targets,
        ),
    )
    if not isinstance(fallback_rows, list):
        return []

    fallback_people = [row for row in fallback_rows if isinstance(row, dict)]
    for row in fallback_people:
        row["_search_source"] = "search_people.keyword_company"
    return fallback_people


def find_company_path_candidates(
    api: Any,
    company_input: str,
    max_degree: int,
    limit: int,
    max_targets: int,
    cache_dir: Path,
    refresh_cache: bool,
) -> dict[str, Any]:
    if max_degree == 3:
        fail("--max-degree 3 is planned but not implemented yet. Current version supports 1 or 2.", 2)
    if max_degree not in (1, 2):
        fail("--max-degree must be 1, 2, or 3.", 2)

    company, company_candidates = resolve_company(
        api, company_input, cache_dir, refresh_cache
    )
    company_urn_id = company.get("urn_id")
    if not company_urn_id:
        fail(f"Could not resolve LinkedIn company URN: {company_input}")
    company_name = company.get("name")

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for degree in range(1, max_degree + 1):
        rows = fetch_company_people(
            api,
            company_urn_id,
            company_name,
            degree,
            max_targets,
            cache_dir,
            refresh_cache,
        )
        for row in rows:
            candidate = company_path_candidate(row, degree)
            target = candidate["target"]
            dedupe_key = (
                target.get("urn_id")
                or "|".join(
                    str(target.get(key) or "")
                    for key in ("name", "jobtitle", "location")
                )
            )
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            candidates.append(candidate)

    candidates.sort(key=candidate_score)
    candidates = candidates[:limit]
    direct_count = sum(1 for candidate in candidates if candidate["degree"] == 1)
    second_count = sum(1 for candidate in candidates if candidate["degree"] == 2)
    resolved_count = sum(
        1 for candidate in candidates if candidate["path_status"] == "resolved"
    )

    return {
        "schema_version": 1,
        "query": {
            "company": company_input,
            "max_degree": max_degree,
            "limit": limit,
            "max_targets": max_targets,
        },
        "company": company,
        "company_candidates": company_candidates,
        "summary": {
            "candidate_count": len(candidates),
            "direct_count": direct_count,
            "second_degree_count": second_count,
            "resolved_path_count": resolved_count,
        },
        "candidates": candidates,
    }


def write_csv(path: Path, rows: list[dict[str, str | None]]) -> None:
    fieldnames = ["url", "name", "distance", "jobtitle", "location", "urn_id"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def write_company_path_csv(path: Path, candidates: list[dict[str, Any]]) -> None:
    fieldnames = [
        "degree",
        "path_status",
        "name",
        "jobtitle",
        "location",
        "distance",
        "urn_id",
        "url",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in candidates:
            target = candidate.get("target")
            if not isinstance(target, dict):
                target = {}
            writer.writerow(
                {
                    "degree": candidate.get("degree"),
                    "path_status": candidate.get("path_status"),
                    "name": target.get("name"),
                    "jobtitle": target.get("jobtitle"),
                    "location": target.get("location"),
                    "distance": target.get("distance"),
                    "urn_id": target.get("urn_id"),
                    "url": target.get("url"),
                }
            )


def target_display_name(target: dict[str, Any]) -> str:
    name = target.get("name")
    if isinstance(name, str) and name:
        return name

    urn_id = target.get("urn_id")
    if isinstance(urn_id, str) and urn_id:
        return f"LinkedIn member {urn_id}"
    return "LinkedIn member"


def render_company_path_result(result: dict[str, Any]) -> str:
    company = result.get("company")
    if not isinstance(company, dict):
        company = {}
    summary = result.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    query = result.get("query")
    if not isinstance(query, dict):
        query = {}

    company_name = company.get("name") or query.get("company") or "Unknown company"
    lines = [f"Company: {company_name}"]

    if company.get("url"):
        lines.append(f"LinkedIn: {company['url']}")
    if company.get("urn_id"):
        lines.append(f"Company URN: {company['urn_id']}")

    max_degree = query.get("max_degree")
    lines.extend(
        [
            "",
            f"Search: reachable people up to degree {max_degree}",
            (
                "Found: "
                f"{summary.get('direct_count', 0)} direct, "
                f"{summary.get('second_degree_count', 0)} second-degree candidates"
            ),
        ]
    )

    candidates = result.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        lines.extend(
            [
                "",
                "No reachable employees found with LinkedIn search filters.",
                "Try increasing --max-targets or refreshing cache.",
            ]
        )
        return "\n".join(lines)

    index = 1
    for degree, title in ((1, "Direct connections"), (2, "Second-degree candidates")):
        group = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("degree") == degree
        ]
        if not group:
            continue

        lines.extend(["", title, ""])
        for candidate in group:
            target = candidate.get("target")
            if not isinstance(target, dict):
                target = {}

            name = target_display_name(target)
            lines.append(f"{index}. {name}")
            if target.get("jobtitle"):
                lines.append(f"   Role: {target['jobtitle']}")
            if target.get("location"):
                lines.append(f"   Location: {target['location']}")
            if degree == 1:
                lines.append(f"   Path: you -> {name}")
            else:
                lines.append(f"   Path: you -> unknown introducer -> {name}")
                lines.append(
                    "   Status: candidate confirmed as second-degree; exact introducer unresolved"
                )
            if target.get("url"):
                lines.append(f"   Profile: {target['url']}")
            if target.get("urn_id"):
                lines.append(f"   URN: {target['urn_id']}")
            lines.append("")
            index += 1

    return "\n".join(lines).rstrip()


def run_profile_command(args: argparse.Namespace) -> None:
    api = build_api(args.cookie_file)
    urn_id = profile_urn_id(api, profile_slug(args.profile))
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


def run_company_path_command(args: argparse.Namespace) -> None:
    api = build_api(args.cookie_file)
    max_targets = args.max_targets or args.limit
    result = find_company_path_candidates(
        api=api,
        company_input=args.company,
        max_degree=args.max_degree,
        limit=args.limit,
        max_targets=max_targets,
        cache_dir=resolve_path(args.cache_dir),
        refresh_cache=args.refresh_cache,
    )

    if args.json_out:
        args.json_out.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    if args.csv_out:
        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        write_company_path_csv(args.csv_out, candidates)

    print(render_company_path_result(result))

    wrote = []
    if args.json_out:
        wrote.append(str(args.json_out))
    if args.csv_out:
        wrote.append(str(args.csv_out))
    if wrote:
        print(f"Wrote company path output to {', '.join(wrote)}", file=sys.stderr)


def parse_company_path_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="warmpath company-path",
        description="Find reachable referral candidates at a LinkedIn company.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  uv run warmpath company-path https://www.linkedin.com/company/ozon-tech
  uv run warmpath company-path "Ozon Tech" --max-degree 2 --limit 25

cookies:
  Paste Netscape cookies.txt from Get cookies.txt LOCALLY into cookies/linkedin.cookies,
  or pass another path with --cookie-file.
""",
    )
    parser.add_argument("company", help="LinkedIn /company/ URL or company name")
    parser.add_argument(
        "--max-degree",
        type=int,
        choices=(1, 2, 3),
        default=2,
        help="Maximum network degree to search. Default: 2.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_COMPANY_PATH_LIMIT,
        help="Maximum candidates to print. Default: 25.",
    )
    parser.add_argument(
        "--max-targets",
        type=int,
        help="Maximum LinkedIn search results to fetch per degree. Default: --limit.",
    )
    parser.add_argument("--max-bridges", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    parser.add_argument(
        "--json-out", type=Path, help="Write structured result to this JSON file"
    )
    parser.add_argument(
        "--csv-out", type=Path, help="Write candidates to this CSV file"
    )
    return parser.parse_args(argv)


def parse_connections_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="warmpath connections",
        description="Scrape visible LinkedIn connections for an arbitrary profile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  uv run warmpath connections https://www.linkedin.com/in/mitchellh/ --limit 50
  uv run warmpath connections mitchellh --limit 50 --csv-out mitchellh_connections.csv

cookies:
  Paste Netscape cookies.txt from Get cookies.txt LOCALLY into cookies/linkedin.cookies,
  or pass another path with --cookie-file.
""",
    )
    parser.add_argument("profile", help="LinkedIn /in/ URL or public slug")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    parser.add_argument(
        "--json-out", type=Path, help="Write fetched profiles to this JSON file"
    )
    parser.add_argument(
        "--csv-out", type=Path, help="Write fetched profiles to this CSV file"
    )
    return parser.parse_args(argv)


def parse_main_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="warmpath",
        description="Local LinkedIn connection and referral-path tools.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""commands:
  connections PROFILE
    Fetch visible connections for a LinkedIn /in/ profile URL or public slug.

  company-path COMPANY
    Find people at a company reachable through your LinkedIn network.
    COMPANY can be a LinkedIn /company/ URL or a company name.

examples:
  uv run warmpath connections mitchellh --limit 50
  uv run warmpath connections https://www.linkedin.com/in/mitchellh/
  uv run warmpath company-path https://www.linkedin.com/company/ozon-tech
  uv run warmpath company-path "Ozon Tech" --max-degree 2 --limit 25

more help:
  uv run warmpath connections --help
  uv run warmpath company-path --help
""",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        parse_main_args(["--help"])
        return

    if argv and argv[0] == "connections":
        run_profile_command(parse_connections_args(argv[1:]))
        return

    if argv and argv[0] == "company-path":
        run_company_path_command(parse_company_path_args(argv[1:]))
        return

    parse_main_args(argv)


if __name__ == "__main__":
    main()
