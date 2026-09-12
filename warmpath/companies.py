"""Enumerate employers through the connections list, without people search caps."""

import re
from typing import Any, Callable
from urllib.parse import quote, unquote, urlparse

from requests.exceptions import HTTPError


Fetch = Callable[[str, dict[str, Any]], dict[str, Any]]
CONNECTIONS_PAGE_SIZE = 1000
CONNECTIONS_DECORATION = (
    "com.linkedin.voyager.dash.deco.web.mynetwork.ConnectionListWithProfile-16"
)
PROFILE_DECORATION = (
    "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-101"
)
COMPANY_URN = re.compile(r"^urn:li:(?:fsd_company|fs_normalized_company|company):([^,)]+)$")


class CompaniesError(Exception):
    pass


class IncompleteEmployment(CompaniesError):
    def __init__(self, employers: list[dict[str, str | None]]):
        super().__init__("LinkedIn returned only part of a profile's employment history.")
        self.employers = employers


class ResponseGraph:
    """Resolve normalized Voyager references, also accepting inline responses."""

    def __init__(self, payload: dict[str, Any]):
        self.root = payload.get("data", payload)
        if not isinstance(self.root, dict):
            raise CompaniesError("LinkedIn returned an invalid collection.")
        self.entities = {
            item["entityUrn"]: item
            for item in payload.get("included", [])
            if isinstance(item, dict) and isinstance(item.get("entityUrn"), str)
        }

    def resolve(self, value: Any) -> Any:
        return self.entities.get(value, value) if isinstance(value, str) else value

    def field(self, record: dict[str, Any], key: str) -> Any:
        return self.resolve(record.get(f"*{key}", record.get(key)))

    def elements(self, collection: Any) -> list[dict[str, Any]]:
        collection = self.resolve(collection)
        if isinstance(collection, dict):
            collection = collection.get("*elements", collection.get("elements"))
        if not isinstance(collection, list):
            raise CompaniesError("LinkedIn omitted a requested collection; results would be incomplete.")
        elements = [self.resolve(item) for item in collection]
        if any(not isinstance(item, dict) for item in elements):
            raise CompaniesError("LinkedIn omitted a referenced record; results would be incomplete.")
        return elements


def plain_text(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("text")
    if isinstance(value, str):
        return " ".join(value.split()) or None
    return None


def position_company(
    position: dict[str, Any], group: dict[str, Any], graph: ResponseGraph
) -> dict[str, str | None] | None:
    company = graph.field(position, "company") or graph.field(group, "company")
    record = company if isinstance(company, dict) else {}
    urn = record.get("entityUrn") or position.get("companyUrn") or group.get("companyUrn") or company
    match = COMPANY_URN.fullmatch(urn) if isinstance(urn, str) else None
    company_id = match.group(1) if match else None
    name = plain_text(record.get("name")) or plain_text(position.get("companyName"))
    name = name or plain_text(group.get("companyName"))
    slug = plain_text(record.get("universalName"))
    if not slug:
        # Company.url is normally the external website, not a LinkedIn URL.
        url = record.get("url")
        if isinstance(url, str):
            parsed = urlparse(url)
            if (parsed.hostname or "").lower() in ("linkedin.com", "www.linkedin.com"):
                url_match = re.match(r"/company/([^/]+)", parsed.path)
                if url_match:
                    slug = unquote(url_match.group(1))
    url_id = slug or company_id
    if not name and not url_id:
        return None
    return {
        "urn_id": company_id,
        "name": name,
        "url": f"https://www.linkedin.com/company/{quote(url_id, safe='')}/" if url_id else None,
    }


def current_companies(
    profile: dict[str, Any], graph: ResponseGraph
) -> list[dict[str, str | None]] | None:
    groups = graph.field(profile, "profilePositionGroups")
    if groups is None:
        return None
    companies = []
    groups_list = graph.elements(groups)
    incomplete = collection_is_truncated(groups, groups_list, graph) and not reached_past_employment(groups_list)
    # Follow this profile's graph: unrelated profiles/positions can also be included.
    for group in groups_list:
        # A group's date range spans its positions. This identifies a current
        # employer even when LinkedIn paginates the roles within that company.
        dates = group.get("dateRange")
        if isinstance(dates, dict) and dates.get("start"):
            if dates.get("end"):
                continue
            company = position_company({}, group, graph)
            if company is not None:
                companies.append(company)
                continue
        collection = graph.field(group, "profilePositionInPositionGroup")
        positions = graph.elements(collection)
        incomplete |= collection_is_truncated(collection, positions, graph)
        for position in positions:
            dates = position.get("dateRange")
            if not isinstance(dates, dict) or not dates.get("start") or dates.get("end"):
                continue
            company = position_company(position, group, graph)
            if company is not None:
                companies.append(company)
    if incomplete:
        raise IncompleteEmployment(companies)
    return companies


def collection_is_truncated(collection: Any, elements: list, graph: ResponseGraph) -> bool:
    collection = graph.resolve(collection)
    paging = collection.get("paging", {}) if isinstance(collection, dict) else {}
    total = paging.get("total")
    return isinstance(total, int) and total > len(elements)


def reached_past_employment(groups: list[dict[str, Any]]) -> bool:
    # Profile experience places the reorderable current roles before past
    # employment. Once that boundary is present, omitted historical groups
    # cannot add current employers. Don't assume the boundary for missing dates
    # or a response that violates this ordering.
    # https://www.linkedin.com/help/linkedin/answer/a786867
    past = False
    for group in groups:
        dates = group.get("dateRange")
        if not isinstance(dates, dict) or not dates.get("start"):
            return False
        if dates.get("end"):
            past = True
        elif past:
            return False
    return past


def profile_identity(profile: dict[str, Any]) -> str:
    urn = profile.get("entityUrn")
    if isinstance(urn, str) and urn.startswith("urn:li:fsd_profile:"):
        return urn.removeprefix("urn:li:fsd_profile:")
    public_id = profile.get("publicIdentifier")
    if isinstance(public_id, str) and public_id:
        return public_id
    raise CompaniesError("LinkedIn omitted a connection's profile identity.")


def deduplicate_companies(
    companies: list[dict[str, str | None]],
) -> list[dict[str, str | None]]:
    # Stable IDs distinguish different companies with identical names. Name-only
    # positions are merged into an identified company only when unambiguous.
    identified: dict[str, dict[str, str | None]] = {}
    unnamed_ids: dict[str, dict[str, str | None]] = {}
    for company in companies:
        name = company.get("name")
        key = company.get("urn_id") or company.get("url")
        if key:
            previous = identified.setdefault(key, company.copy())
            if not previous.get("name") and name:
                previous["name"] = name
        elif name:
            unnamed_ids.setdefault(name.casefold(), company.copy())
    for name, company in unnamed_ids.items():
        matches = [item for item in identified.values() if (item.get("name") or "").casefold() == name]
        if len(matches) != 1:
            identified[f"name:{name}"] = company
    return sorted(
        identified.values(),
        key=lambda item: ((item.get("name") or item.get("url") or "").casefold(), item.get("url") or ""),
    )


def profile_companies(fetch: Fetch, identity: str) -> list[dict[str, str | None]]:
    graph = ResponseGraph(fetch("/identity/dash/profiles", {
        "q": "memberIdentity", "memberIdentity": identity,
        "decorationId": PROFILE_DECORATION,
    }))
    profiles = graph.elements(graph.root)
    if len(profiles) != 1 or (
        profile_identity(profiles[0]) != identity and profiles[0].get("publicIdentifier") != identity
    ):
        raise CompaniesError(f"LinkedIn did not return the requested profile: {identity}.")
    employers = current_companies(profiles[0], graph)
    if employers is None:
        raise CompaniesError(f"LinkedIn omitted employment data for profile: {identity}.")
    return employers


def find_companies(
    fetch: Fetch,
    get_employers: Callable[[str], list[dict[str, str | None]]] | None = None,
    progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    companies: list[dict[str, str | None]] = []
    seen_profiles: set[str] = set()
    seen_pages: set[tuple[str, ...]] = set()
    unavailable: list[str] = []
    start = 0
    while True:
        graph = ResponseGraph(fetch("/relationships/dash/connections", {
            "q": "search",
            "sortType": "RECENTLY_ADDED",
            "start": start,
            "count": CONNECTIONS_PAGE_SIZE,
            "decorationId": CONNECTIONS_DECORATION,
        }))
        connections = graph.elements(graph.root)
        paging = graph.root.get("paging", {})
        total = paging.get("total")
        if not isinstance(total, int) or total < 0:
            total = None
        if paging.get("start", start) != start:
            raise CompaniesError("LinkedIn did not advance the connections page.")
        if not connections:
            if total is not None and start < total:
                raise CompaniesError("LinkedIn stopped returning connections before the reported total.")
            break

        profiles = []
        for connection in connections:
            profile = graph.field(connection, "connectedMemberResolutionResult")
            if not isinstance(profile, dict):
                member = graph.field(connection, "connectedMember")
                if isinstance(member, dict):
                    profile = member
                elif isinstance(member, str) and member.startswith("urn:li:fsd_profile:"):
                    profile = {"entityUrn": member}
                else:
                    raise CompaniesError("LinkedIn omitted a connection's profile identity.")
            profiles.append((profile_identity(profile), profile))
        signature = tuple(identity for identity, _ in profiles)
        if signature in seen_pages:
            raise CompaniesError("LinkedIn repeated a connections page; results would be incomplete.")
        seen_pages.add(signature)

        for identity, profile in profiles:
            if identity in seen_profiles:
                continue
            seen_profiles.add(identity)
            try:
                employers = current_companies(profile, graph)
                if employers is None:
                    employers = get_employers(identity) if get_employers else profile_companies(fetch, identity)
            except IncompleteEmployment as exc:
                unavailable.append(identity)
                employers = exc.employers
            except HTTPError as exc:
                if exc.response is None or exc.response.status_code not in (403, 404):
                    raise
                # Still visit the remaining connections and report the gap.
                unavailable.append(identity)
                employers = []
            companies.extend(employers)
            if progress:
                progress(len(seen_profiles))

        # Advance by raw rows, not deduplicated rows or the requested page size:
        # LinkedIn can clamp count. A short page is not necessarily the last one.
        start += len(connections)
        if total is not None and start >= total:
            break

    return {
        "companies": deduplicate_companies(companies),
        "connections": len(seen_profiles),
        "unavailable_profiles": unavailable,
    }


def render_companies(companies: list[dict[str, str | None]], urls_only: bool) -> str:
    lines = []
    for company in companies:
        name, url = company.get("name"), company.get("url")
        if urls_only:
            if url:
                lines.append(url)
        elif name and url:
            lines.append(f"{name}  {url}")
        elif name or url:
            lines.append(name or url)
    return "\n".join(lines)
