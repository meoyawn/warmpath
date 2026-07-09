# Warmpath Skill Command Architecture

This document describes the current implementation of:

```sh
uvx warmpath skill <Skill>
```

## Entry Point

The CLI entry point is `warmpath.cli:main`.

For the `skill` subcommand, the call path is:

1. `main(argv)`
2. `parse_skill_args(argv[1:])`
3. `run_skill_command(args)`
4. `find_skill_connections(...)`
5. `render_skill_connections_result(result)`

`run_skill_command` builds a LinkedIn API client from the cookie file, resolves the cache directory, calls the skill search pipeline, and prints the rendered result.

## Defaults

Current defaults:

- `--max-depth`: `2`
- `--limit`: `5`
- candidate search window: `25`
- cache directory: `~/.cache/warmpath`

`--limit` is the display limit. It caps how many profiles are printed.

The candidate search window controls how many LinkedIn search rows are fetched per network depth before filtering and ranking. It is currently `max(--limit, 25)`.

## Search Flow

`find_skill_connections` loops over network depths:

- degree `1` maps to LinkedIn network filter `F`
- degree `2` maps to LinkedIn network filter `S`

For each degree, `fetch_skill_connection_rows` calls:

```python
api.search(
    {
        "keywords": skill,
        "filters": (
            "List((key:resultType,value:List(PEOPLE)),"
            f"(key:network,value:List({network_depth})))"
        ),
    },
    limit=search_limit,
)
```

So, with default `--max-depth 2`, the command performs two LinkedIn people-search requests when the search cache is cold:

1. first-degree people search
2. second-degree people search

## Row Normalization

Each raw LinkedIn search result is normalized by `connection_result_row`.

The normalized row keeps:

- name
- network distance
- job title
- location
- profile URN
- canonical profile URL
- mutual connection metadata, when present

Profile URLs are canonicalized to `https://www.linkedin.com/in/<public-id>/`.

## Skill Verification

Search results are not accepted blindly.

For each candidate row, `skill_match_quality` tries to verify the requested skill:

1. Fetch profile skills with `api.get_profile_skills(public_id=<public-id>)`.
2. If that returns a non-empty payload, inspect the payload for an exact normalized skill match.
3. If the public-id lookup returns an empty list, retry with `api.get_profile_skills(urn_id=<urn-id>)`, when a URN is available.
4. If profile-skill lookup does not confirm the skill, fall back to visible search-row text: name, job title, and location.

Match quality is recorded as:

- `pinned_profile`
- `profile_skill`
- `visible_text`

Rows with no match quality are discarded unless they are pinned.

## Pinned Skill Profiles

Some LinkedIn skill/profile combinations can be known-good even when profile-skill endpoints return empty data.

Current pinned profiles:

- `Leadership`: `https://www.linkedin.com/in/timur-pokayonkov/`

Pinned profiles:

- bypass profile-skill verification
- are still required to appear in the LinkedIn search result window
- are ranked before non-pinned rows at the same network degree

This handles the case where LinkedIn search returns Timur as a direct connection for `Leadership`, but the profile-skill endpoint returns no usable skill payload.

## Ranking

Candidates are deduped by:

1. profile URN
2. profile URL
3. fallback tuple of name, job title, and location

Then candidates are sorted by `skill_candidate_score`:

1. network degree: first-degree before second-degree
2. pinned profile rank
3. match quality: pinned profile, profile-skill match, visible-text match
4. original LinkedIn search rank
5. name

After sorting, the list is sliced to `--limit`.

## Rendering

`render_skill_connections_result` prints:

- searched skill
- max depth
- count of first-degree and second-degree candidates in the final sliced result
- grouped first-degree profiles
- grouped second-degree profiles

Each candidate prints:

- name
- role, when available
- location, when available
- mutual connection names for second-degree candidates, when available
- profile URL, when available

## Cache Model

`cached_json` stores JSON payloads under `~/.cache/warmpath`.

Cache filenames use:

- namespace, such as `skill-search-raw` or `profile-skills`
- SHA-256 digest of the structured cache key

For skill search, cache keys include:

- skill text
- network depth
- search limit

For profile skills, cache keys include either:

- public profile ID
- profile URN ID

For mutual searches, cache keys include:

- target profile URN ID
- mutual search limit

`--refresh-cache` bypasses existing cache files and writes fresh payloads.

## HTTP Request Fanout

The current implementation can make substantially more than 1-5 HTTP requests when uncached.

With defaults:

- `--max-depth 2`
- display `--limit 5`
- candidate search window `25`

Cold-cache request shape:

- 1 first-degree people search
- 1 second-degree people search
- up to 25 first-degree profile-skill public-id requests
- up to 25 first-degree profile-skill URN fallback requests
- up to 25 second-degree profile-skill public-id requests
- up to 25 second-degree profile-skill URN fallback requests
- up to 5 displayed second-degree mutual-search requests when search rows do not expose mutual names

Worst case with default display limit: `2 + (25 * 2 * 2) + 5 = 107` LinkedIn API calls.

Typical count is lower because:

- pinned profiles skip profile-skill verification
- rows without a public ID or URN have fewer lookup keys
- public-id skill payloads can stop the URN fallback
- only displayed second-degree candidates without visible mutual names are enriched with mutual searches
- warm cache avoids repeated HTTP calls

This is why caching still matters for the current implementation. Parallelizing requests would reduce latency, but it would not reduce request volume or LinkedIn rate-limit exposure.

## Known Tradeoffs

The current approach favors recall over minimal request count:

- A wider search window prevents a useful direct connection from being excluded before ranking.
- Profile-skill verification reduces false positives from keyword search.
- Visible-text fallback keeps usable results when LinkedIn skill payloads are empty.
- Second-degree mutual enrichment keeps the printed output actionable when LinkedIn search rows omit exact mutual profiles.
- Pinned profiles handle known LinkedIn data gaps.

The main cost is HTTP fanout. If request volume becomes the priority, the likely simplification is to remove per-profile skill verification and trust LinkedIn keyword search plus visible-text ranking.
