# LinkedIn Connections

Fetch visible connections for any LinkedIn `/in/` profile using your logged-in LinkedIn cookies.

If the target profile is your 1st-degree connection, the result is usually that person's visible 1st-degree network, which mostly appears to you as 2nd-degree profiles.

## Cookies

1. Install [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc?hl=en).
2. Open LinkedIn in Chrome while logged in.
3. Export LinkedIn cookies in Netscape `cookies.txt` format.
4. Paste the full file contents into:

```text
cookies/linkedin.cookies
```

Required cookies: `li_at`, `JSESSIONID`.

## Usage

Run from this directory:

```sh
uv run warmpath connections mitchellh --limit 50
```

Each fetched profile URL is printed to stdout, one URL per line.

### Connections

Use your exported browser cookies to fetch connections as the logged-in LinkedIn user:

```sh
uv run warmpath connections mitchellh --limit 50
```

Write CSV:

```sh
uv run warmpath connections mitchellh --limit 50 --csv-out mitchellh_connections_50.csv
```

Write both formats:

```sh
uv run warmpath connections mitchellh --limit 50 --json-out mitchellh_connections_50.json --csv-out mitchellh_connections_50.csv
```

### Company

Find people at a target company who are reachable through your network:

```sh
uv run warmpath company https://www.linkedin.com/company/ozon-tech
uv run warmpath company "Ozon Tech"
```

By default this searches up to second degree:

- direct: `you -> employee`
- second-degree candidate with visible mutuals: `you -> introducer -> employee`
- unresolved second-degree candidate: `you -> unknown introducer -> employee`

Second-degree paths print mutual introducers when LinkedIn exposes them. They stay marked unresolved when LinkedIn confirms reachability but does not return visible mutuals.

Useful options:

```sh
uv run warmpath company https://www.linkedin.com/company/ozon-tech --max-degree 2 --limit 25 --json-out ozon_paths.json
```

## Required Arguments

- `connections profile`: LinkedIn `/in/` URL or public slug, for example `mitchellh`.
- `company company`: LinkedIn `/company/` URL or company name.

## Useful Options

- `--limit 50`: number of profiles to fetch. Default: `50`.
- `--cookie-file cookies/linkedin.cookies`: override cookie file path.
- `company --max-degree 2`: maximum reachable degree to search. Default: `2`.
- `company --cache-dir .linkedin-cache`: cache LinkedIn search results.
- `company --refresh-cache`: bypass cache and fetch fresh results.
- `--help`: show examples and all flags.

## Output

For `connections`, stdout contains one canonical LinkedIn profile URL per line.

JSON and CSV rows contain:

- `url`
- `name`
- `distance`
- `jobtitle`
- `location`
- `urn_id`

For `company`, stdout is human-readable. `--json-out` writes structured company, query, summary, and candidate data.

## Notes

- LinkedIn visibility and privacy settings control what can be fetched.
- If the old `open-linkedin-api` profile endpoint returns `410`, the CLI falls back to extracting the current `fsd_profile` id from authenticated profile HTML.
- Keep `cookies/linkedin.cookies` private. It is ignored by Git.
