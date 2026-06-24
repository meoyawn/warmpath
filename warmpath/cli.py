import argparse
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
DEFAULT_COMPANY_PATH_LIMIT = 5
DEFAULT_CACHE_DIR = Path(".linkedin-cache")
DEFAULT_MAX_MUTUAL_CONNECTIONS = 50
MUTUAL_CONNECTION_TEXT_RE = re.compile(r"\bmutual connections?\b", re.IGNORECASE)
OTHER_MUTUALS_RE = re.compile(
    r"(?:^|[\s,])(?:&|and)\s+(\d+)\s+other mutual connections?\s*$",
    re.IGNORECASE,
)
MUTUAL_COUNT_RE = re.compile(r"\b(\d+)\s+mutual connections?\b", re.IGNORECASE)
PROFILE_NETWORK_DISTANCE_KEYS = {
    "connectiondistance",
    "degree",
    "distance",
    "memberdistance",
    "networkdistance",
}


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


def canonical_profile_public_id(value: str) -> str | None:
    url = canonical_profile_url(value)
    if not url:
        return None
    match = PROFILE_URL_RE.search(urlparse(url).path)
    if not match:
        return None
    return match.group(1)


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


def text_values_containing(value: Any, pattern: re.Pattern[str]) -> list[str]:
    matches: list[str] = []
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            if pattern.search(current):
                matches.append(current)
        elif isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return matches


def parse_mutual_connection_text(
    text: str,
) -> tuple[list[dict[str, str | None]], int | None, bool]:
    normalized = re.sub(r"\s+", " ", text).strip()
    other_match = OTHER_MUTUALS_RE.search(normalized)
    other_count = int(other_match.group(1)) if other_match else 0
    names_text = normalized[: other_match.start()].strip(" ,") if other_match else ""

    count_match = MUTUAL_COUNT_RE.search(normalized)
    if not names_text and count_match:
        return [], int(count_match.group(1)), True

    names = [
        name.strip(" ,")
        for name in re.split(r"\s*,\s*|\s+and\s+", names_text)
        if name.strip(" ,")
    ]
    mutual_connections = [
        {"name": name, "url": None, "urn_id": None}
        for name in dict.fromkeys(names)
    ]
    if not mutual_connections and not count_match:
        return [], None, False

    mutual_count = len(mutual_connections) + other_count
    if count_match and int(count_match.group(1)) > mutual_count:
        mutual_count = int(count_match.group(1))
    return mutual_connections, mutual_count, other_count > 0


def mutual_connections_from_result(
    result: dict[str, Any],
) -> tuple[list[dict[str, str | None]], int | None, bool]:
    best_connections: list[dict[str, str | None]] = []
    best_count: int | None = None
    best_truncated = False

    for text in text_values_containing(result, MUTUAL_CONNECTION_TEXT_RE):
        connections, count, truncated = parse_mutual_connection_text(text)
        best_score = (best_count or 0, len(best_connections))
        score = (count or 0, len(connections))
        if score > best_score:
            best_connections = connections
            best_count = count
            best_truncated = truncated

    return best_connections, best_count, best_truncated


def connection_result_row(result: dict[str, Any]) -> dict[str, Any]:
    tracking = result.get("entityCustomTrackingInfo")
    if not isinstance(tracking, dict):
        tracking = {}

    row: dict[str, Any] = {
        "name": text_field(result, "title"),
        "distance": tracking.get("memberDistance"),
        "jobtitle": text_field(result, "primarySubtitle"),
        "location": text_field(result, "secondarySubtitle"),
        "urn_id": urn_id_from_result(result),
        "url": profile_url_from_result(result),
    }

    mutual_connections, mutual_count, mutuals_truncated = mutual_connections_from_result(
        result
    )
    if mutual_connections or mutual_count is not None:
        row["mutual_connections"] = mutual_connections
        row["mutual_count"] = mutual_count
        row["mutuals_truncated"] = mutuals_truncated

    return row


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


def row_mutual_details(
    row: dict[str, Any],
) -> tuple[list[dict[str, str | None]], int | None, bool]:
    raw_mutual_connections = row.get("mutual_connections")
    if isinstance(raw_mutual_connections, list):
        mutual_connections = [
            item
            for item in raw_mutual_connections
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]
    else:
        mutual_connections = []

    raw_mutual_count = row.get("mutual_count")
    mutual_count = raw_mutual_count if isinstance(raw_mutual_count, int) else None
    raw_mutuals_truncated = row.get("mutuals_truncated")
    mutuals_truncated = (
        raw_mutuals_truncated if isinstance(raw_mutuals_truncated, bool) else False
    )
    return mutual_connections, mutual_count, mutuals_truncated


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

    mutual_connections, mutual_count, mutuals_truncated = row_mutual_details(row)
    if mutual_connections:
        if mutual_count is None:
            mutual_count = len(mutual_connections)
        return {
            "degree": degree,
            "path_status": "partially_resolved",
            "target": target,
            "mutual_connections": mutual_connections,
            "mutual_count": mutual_count,
            "mutuals_truncated": mutuals_truncated,
            "path": [
                {"role": "me"},
                *[
                    {"role": "introducer_candidate", **mutual}
                    for mutual in mutual_connections
                ],
                {"role": "target", **target},
            ],
            "evidence": {
                "source": search_source,
                "network_depth": NETWORK_DEPTH_BY_DEGREE[degree],
                "note": "LinkedIn search returned visible mutual connection candidates.",
            },
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
        "people-search-raw",
        {
            "mode": "current_company",
            "company_urn_id": company_urn_id,
            "network_depth": network_depth,
            "max_targets": max_targets,
        },
        refresh_cache,
        lambda: api.search(
            {
                "filters": (
                    "List((key:resultType,value:List(PEOPLE)),"
                    f"(key:currentCompany,value:List({company_urn_id})),"
                    f"(key:network,value:List({network_depth})))"
                )
            },
            limit=max_targets,
        ),
    )
    if not isinstance(rows, list):
        rows = []

    people = [connection_result_row(row) for row in rows if isinstance(row, dict)]
    if people:
        for row in people:
            row["_search_source"] = "search.current_company"
        return people

    if not company_name:
        return []

    fallback_rows = cached_json(
        cache_dir,
        "people-search-raw",
        {
            "mode": "keyword_company",
            "company_name": company_name,
            "network_depth": network_depth,
            "max_targets": max_targets,
        },
        refresh_cache,
        lambda: api.search(
            {
                "filters": (
                    "List((key:resultType,value:List(PEOPLE)),"
                    f"(key:company,value:List({company_name})),"
                    f"(key:network,value:List({network_depth})))"
                )
            },
            limit=max_targets,
        ),
    )
    if not isinstance(fallback_rows, list):
        return []

    fallback_people = [
        connection_result_row(row) for row in fallback_rows if isinstance(row, dict)
    ]
    for row in fallback_people:
        row["_search_source"] = "search.keyword_company"
    return fallback_people


def fetch_mutual_connection_rows(
    api: Any,
    target_urn_id: str,
    limit: int,
    cache_dir: Path,
    refresh_cache: bool,
) -> list[dict[str, Any]]:
    rows = cached_json(
        cache_dir,
        "mutual-search",
        {"target_urn_id": target_urn_id, "limit": limit},
        refresh_cache,
        lambda: api.search(
            {
                "filters": (
                    "List((key:resultType,value:List(PEOPLE)),"
                    f"(key:connectionOf,value:List({target_urn_id})),"
                    "(key:network,value:List(F)))"
                )
            },
            limit=limit,
        ),
    )
    if not isinstance(rows, list):
        return []

    return [connection_result_row(row) for row in rows if isinstance(row, dict)]


def normalize_profile_network_distance(value: str) -> str | None:
    token = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    if token in {"DISTANCE_1", "DEGREE_1", "FIRST", "FIRST_DEGREE", "F"}:
        return "direct"
    if token in {"DISTANCE_2", "DEGREE_2", "SECOND", "SECOND_DEGREE", "S"}:
        return "second"
    if token in {
        "DISTANCE_3",
        "DEGREE_3",
        "OUT",
        "OUT_OF_NETWORK",
        "THIRD",
        "THIRD_DEGREE",
        "O",
    }:
        return "out"
    if "OUT_OF_NETWORK" in token:
        return "out"
    return None


def profile_network_distance_from_value(value: Any) -> str | None:
    if isinstance(value, str):
        return normalize_profile_network_distance(value)

    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = re.sub(r"[^A-Za-z0-9]+", "", str(key)).casefold()
            if normalized_key in PROFILE_NETWORK_DISTANCE_KEYS:
                distance = profile_network_distance_from_value(item)
                if distance:
                    return distance

        for item in value.values():
            distance = profile_network_distance_from_value(item)
            if distance:
                return distance

    if isinstance(value, list):
        for item in value:
            distance = profile_network_distance_from_value(item)
            if distance:
                return distance

    return None


def fetch_profile_network_distance(
    api: Any,
    public_id: str,
    cache_dir: Path,
    refresh_cache: bool,
) -> str | None:
    get_network_info = getattr(api, "get_profile_network_info", None)
    if not callable(get_network_info):
        return None

    try:
        network_info = cached_json(
            cache_dir,
            "profile-network-info",
            {"public_id": public_id},
            refresh_cache,
            lambda: get_network_info(public_id),
        )
    except Exception:
        return None

    return profile_network_distance_from_value(network_info)


def profile_search_keywords(public_id: str) -> list[str]:
    parts = [part for part in public_id.split("-") if part]
    name_parts = [*parts]
    while len(name_parts) > 1 and any(char.isdigit() for char in name_parts[-1]):
        name_parts.pop()

    keywords = [
        " ".join(name_parts).strip(),
        " ".join(parts).strip(),
        public_id.strip(),
    ]
    return [keyword for keyword in dict.fromkeys(keywords) if keyword]


def profile_search_result_matches(
    row: dict[str, Any], public_id: str, target_urn_id: str | None
) -> bool:
    url = row.get("url")
    if isinstance(url, str) and canonical_profile_public_id(url) == public_id:
        return True

    urn_id = row.get("urn_id")
    return (
        isinstance(target_urn_id, str)
        and bool(target_urn_id)
        and isinstance(urn_id, str)
        and urn_id == target_urn_id
    )


def fetch_profile_search_distance(
    api: Any,
    public_id: str,
    cache_dir: Path,
    refresh_cache: bool,
    target_urn_id: str | None = None,
) -> str | None:
    for network_depth in ("F", "S", "O"):
        for keywords in profile_search_keywords(public_id):
            rows = cached_json(
                cache_dir,
                "profile-search",
                {
                    "public_id": public_id,
                    "keywords": keywords,
                    "network_depth": network_depth,
                },
                refresh_cache,
                lambda keywords=keywords, network_depth=network_depth: api.search(
                    {
                        "keywords": keywords,
                        "filters": (
                            "List((key:resultType,value:List(PEOPLE)),"
                            f"(key:network,value:List({network_depth})))"
                        ),
                    },
                    limit=10,
                ),
            )
            if not isinstance(rows, list):
                continue

            for result in rows:
                if not isinstance(result, dict):
                    continue
                row = connection_result_row(result)
                if not profile_search_result_matches(row, public_id, target_urn_id):
                    continue

                distance = row.get("distance")
                if isinstance(distance, str):
                    normalized = normalize_profile_network_distance(distance)
                    if normalized:
                        return normalized
                return normalize_profile_network_distance(network_depth)

    return None


def mutual_connection_fetch_limit(row: dict[str, Any]) -> int:
    mutual_count = row.get("mutual_count")
    if isinstance(mutual_count, int) and mutual_count > 0:
        return min(mutual_count, DEFAULT_MAX_MUTUAL_CONNECTIONS)
    return DEFAULT_MAX_MUTUAL_CONNECTIONS


def mutual_connection_payload(row: dict[str, Any]) -> dict[str, str | None] | None:
    name = row.get("name")
    if not isinstance(name, str) or not name:
        return None
    return {
        "name": name,
        "url": row.get("url") if isinstance(row.get("url"), str) else None,
        "urn_id": row.get("urn_id") if isinstance(row.get("urn_id"), str) else None,
    }


def enrich_row_with_mutual_connections(
    api: Any,
    row: dict[str, Any],
    cache_dir: Path,
    refresh_cache: bool,
) -> dict[str, Any]:
    existing_mutual_connections = row.get("mutual_connections")
    if isinstance(existing_mutual_connections, list) and existing_mutual_connections:
        return row

    target_urn_id = row.get("urn_id")
    if not isinstance(target_urn_id, str) or not target_urn_id:
        return row

    limit = mutual_connection_fetch_limit(row)
    if limit < 1:
        return row

    mutual_rows = fetch_mutual_connection_rows(
        api,
        target_urn_id,
        limit,
        cache_dir,
        refresh_cache,
    )
    mutual_connections = [
        payload
        for payload in (mutual_connection_payload(mutual) for mutual in mutual_rows)
        if payload is not None
    ]
    if not mutual_connections:
        return row

    enriched = {**row}
    mutual_count = enriched.get("mutual_count")
    if not isinstance(mutual_count, int) or mutual_count < len(mutual_connections):
        mutual_count = len(mutual_connections)

    enriched["mutual_connections"] = mutual_connections
    enriched["mutual_count"] = mutual_count
    enriched["mutuals_truncated"] = mutual_count > len(mutual_connections)
    return enriched


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
        fail(
            "--max-degree 3 is planned but not implemented yet. Current version supports 1 or 2.",
            2,
        )
    if max_degree not in (1, 2):
        fail("--max-degree must be 1, 2, or 3.", 2)

    company, company_candidates = resolve_company(
        api, company_input, cache_dir, refresh_cache
    )
    company_urn_id = company.get("urn_id")
    if not company_urn_id:
        fail(f"Could not resolve LinkedIn company: {company_input}")
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
            if degree == 2:
                row = enrich_row_with_mutual_connections(
                    api,
                    row,
                    cache_dir,
                    refresh_cache,
                )
            candidate = company_path_candidate(row, degree)
            target = candidate["target"]
            dedupe_key = target.get("urn_id") or "|".join(
                str(target.get(key) or "") for key in ("name", "jobtitle", "location")
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


def target_display_name(target: dict[str, Any]) -> str:
    name = target.get("name")
    if isinstance(name, str) and name:
        return name

    urn_id = target.get("urn_id")
    if isinstance(urn_id, str) and urn_id:
        return f"LinkedIn member {urn_id}"
    return "LinkedIn member"


def candidate_mutual_names(candidate: dict[str, Any]) -> list[str]:
    raw_mutual_connections = candidate.get("mutual_connections")
    if not isinstance(raw_mutual_connections, list):
        return []

    names: list[str] = []
    for mutual in raw_mutual_connections:
        if not isinstance(mutual, dict):
            continue
        name = mutual.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def render_mutuals_summary(candidate: dict[str, Any]) -> str | None:
    names = candidate_mutual_names(candidate)
    if not names:
        return None

    raw_total = candidate.get("mutual_count")
    total = raw_total if isinstance(raw_total, int) else len(names)
    if total < len(names):
        total = len(names)

    parts = [*names]
    remaining = total - len(names)
    if remaining:
        parts.append(f"+{remaining} more")
    return f"Mutuals ({total}): {', '.join(parts)}"


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
            if degree == 2:
                mutual_names = candidate_mutual_names(candidate)
                mutuals_summary = render_mutuals_summary(candidate)
                if mutual_names and mutuals_summary:
                    lines.append(f"   {mutuals_summary}")
            if target.get("url"):
                lines.append(f"   Profile: {target['url']}")
            lines.append("")
            index += 1

    return "\n".join(lines).rstrip()


def render_human_mutual(row: dict[str, Any]) -> str | None:
    name = row.get("name")
    if not isinstance(name, str) or not name:
        return None

    url = row.get("url")
    if isinstance(url, str) and url:
        return f"{name}\t{url}"
    return name


def render_human_mutuals(rows: list[dict[str, Any]]) -> list[str]:
    rendered_rows = []
    for row in rows:
        rendered = render_human_mutual(row)
        if rendered:
            rendered_rows.append(rendered)
    return rendered_rows


def run_human_command(args: argparse.Namespace) -> None:
    api = build_api(args.cookie_file)
    cache_dir = resolve_path(args.cache_dir)
    public_id = profile_slug(args.profile_url)
    distance = fetch_profile_network_distance(
        api,
        public_id,
        cache_dir,
        args.refresh_cache,
    )

    if distance == "direct":
        print("Connection: direct")
        return
    if distance == "out":
        print("Connection: out of network")
        return

    if distance is None:
        distance = fetch_profile_search_distance(
            api,
            public_id,
            cache_dir,
            args.refresh_cache,
        )
        if distance == "direct":
            print("Connection: direct")
            return
        if distance == "out":
            print("Connection: out of network")
            return

    urn_id = profile_urn_id(api, public_id)
    if distance is None:
        distance = fetch_profile_search_distance(
            api,
            public_id,
            cache_dir,
            args.refresh_cache,
            urn_id,
        )
        if distance == "direct":
            print("Connection: direct")
            return
        if distance == "out":
            print("Connection: out of network")
            return

    rows = fetch_mutual_connection_rows(
        api=api,
        target_urn_id=urn_id,
        limit=DEFAULT_MAX_MUTUAL_CONNECTIONS,
        cache_dir=cache_dir,
        refresh_cache=args.refresh_cache,
    )
    rendered_rows = render_human_mutuals(rows)
    if not rendered_rows:
        print("Connection: out of network")
        return

    print("Mutuals:")
    for rendered in rendered_rows:
        print(rendered)


def run_company_command(args: argparse.Namespace) -> None:
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

    print(render_company_path_result(result))


def parse_company_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="warmpath company",
        description="Find reachable referral candidates at a LinkedIn company.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  uv run warmpath company https://www.linkedin.com/company/ozon-tech
  uv run warmpath company "Ozon Tech" --max-degree 2 --limit 5

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
        help="Maximum candidates to print. Default: 5.",
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
    return parser.parse_args(argv)


def parse_human_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="warmpath human",
        description="Print mutual LinkedIn connections for a profile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  uv run warmpath human https://www.linkedin.com/in/ruslan-gilemzianov/

cookies:
  Paste Netscape cookies.txt from Get cookies.txt LOCALLY into cookies/linkedin.cookies,
  or pass another path with --cookie-file.
""",
    )
    parser.add_argument("profile_url", help="LinkedIn /in/ profile URL")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--cookie-file", type=Path, default=DEFAULT_COOKIE_FILE)
    return parser.parse_args(argv)


def parse_main_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="warmpath",
        description="Local LinkedIn connection and referral-path tools.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""commands:
  human PROFILE_URL
    Print mutual LinkedIn connections for a profile URL.

  company COMPANY
    Find people at a company reachable through your LinkedIn network.
    COMPANY can be a LinkedIn /company/ URL or a company name.

examples:
  uv run warmpath human https://www.linkedin.com/in/ruslan-gilemzianov/
  uv run warmpath company https://www.linkedin.com/company/ozon-tech
  uv run warmpath company "Ozon Tech" --max-degree 2 --limit 5

more help:
  uv run warmpath human --help
  uv run warmpath company --help
""",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        parse_main_args(["--help"])
        return

    if argv and argv[0] == "human":
        run_human_command(parse_human_args(argv[1:]))
        return

    if argv and argv[0] == "company":
        run_company_command(parse_company_args(argv[1:]))
        return

    if argv[0].startswith("-"):
        parse_main_args(argv)
        return

    fail(f"Unknown command: {argv[0]}", 2)

    parse_main_args(argv)


if __name__ == "__main__":
    main()
