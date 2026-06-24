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
uv run linkedin-connections --profile mitchellh --limit 50
```

Each fetched profile URL is printed to stdout, one URL per line.

### Human Mode

Use your exported browser cookies to fetch connections as the logged-in LinkedIn user:

```sh
uv run linkedin-connections --profile mitchellh --limit 50
```

Write CSV:

```sh
uv run linkedin-connections --profile mitchellh --limit 50 --csv-out mitchellh_connections_50.csv
```

Write both formats:

```sh
uv run linkedin-connections --profile mitchellh --limit 50 --json-out mitchellh_connections_50.json --csv-out mitchellh_connections_50.csv
```

## Required Arguments

- `--profile`: LinkedIn `/in/` URL or public slug, for example `mitchellh`.

## Useful Options

- `--limit 50`: number of profiles to fetch. Default: `50`.
- `--cookie-file cookies/linkedin.cookies`: override cookie file path.
- `--help`: show examples and all flags.

## Output

Stdout contains one canonical LinkedIn profile URL per line.

JSON and CSV rows contain:

- `url`
- `name`
- `distance`
- `jobtitle`
- `location`
- `urn_id`

## Notes

- LinkedIn visibility and privacy settings control what can be fetched.
- If the old `open-linkedin-api` profile endpoint returns `410`, the CLI falls back to extracting the current `fsd_profile` id from authenticated profile HTML.
- Keep `cookies/linkedin.cookies` private. It is ignored by Git.
