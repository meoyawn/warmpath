![Warmpath banner](warmpath.jpg)

# Warmpath

Find LinkedIn mutuals and warm paths using your logged-in browser session.

## Usage

Log in to LinkedIn in a regular browser window, then import that session. Replace `chrome` with your browser's name if needed.

```sh
# Import and check your browser session
uvx warmpath auth import --browser chrome
uvx warmpath auth status

# Find people who can introduce you to a company
uvx warmpath company HashiCorp
uvx warmpath company https://www.linkedin.com/company/hashicorp/ --max-degree 2 --limit 5

# List the current employers of all your direct connections
uvx warmpath companies
uvx warmpath companies --urls
uvx warmpath companies --refresh-cache

# Find reachable people with a skill
uvx warmpath skill Flutter
uvx warmpath skill Leadership --max-depth 2

# Find mutual connections with a person
uvx warmpath human https://www.linkedin.com/in/mitchellh/

# Refresh cached results
uvx warmpath human https://www.linkedin.com/in/mitchellh/ --refresh-cache

# Show options for a command; also works with companies, auth, skill, and human
uvx warmpath company --help
```

`companies` walks the complete 1st-degree connections list, looks up current
employers, and deduplicates by company ID. It includes multiple current employers
and keeps unlinked employer names; `--urls` prints only known LinkedIn company
URLs. It does not infer employment from profile headlines.

Connections are requested in pages of 1,000. Profiles are looked up once per
unique connection when employer data is absent from the connection row. Company
names and URLs come from those responses, with no separate company lookups.
Completed lookups are cached so interrupted runs can resume, and repeating a
completed snapshot makes no HTTP calls. Use `--refresh-cache` for updated
connections and employment. Caches are scoped to the imported session.

If LinkedIn restricts a profile or truncates its employment data, the command
prints the known companies, reports the incomplete result on stderr, and exits
with status 1. Cached snapshots preserve that status until refreshed.

## Browser sessions

Supported browsers are Chrome, Chromium, Firefox, Edge, Brave, Safari, Arc, Vivaldi, Opera, Opera GX (`opera-gx`), and LibreWolf; availability depends on your operating system. Your OS may ask for keychain/keyring access. If the cookie store is locked, close the browser and retry. Private-window sessions cannot be imported.

Warmpath imports LinkedIn's `li_at` and `JSESSIONID` tokens together with its device, security, and routing cookies, and manages the saved session automatically. `company`, `companies`, `skill`, and `human` use it on subsequent runs. Failed imports preserve the previous session.

`auth import` and `auth status` print only `Logged in as <name>` after confirming your session with LinkedIn. If `auth status` cannot confirm a login, it prints `Not logged in` and exits with status 1. Both commands require a connection to LinkedIn.

Successful requests preserve updated cookies returned by LinkedIn. When a saved session expires or is rejected, Warmpath reads the current session from the previously imported browser and retries a read once. Healthy requests need no extra auth checks or browser access. If LinkedIn has also signed out the browser, sign in there again and retry; cookie expiry dates alone cannot indicate whether LinkedIn has revoked a session. Rate limits and server errors are reported rather than treated as empty search results.

LinkedIn requests use [curl_cffi](https://curl-cffi.readthedocs.io/en/latest/impersonate/_index.html) to match the browser family's TLS and HTTP/2 behavior, rather than just changing the user-agent. Chrome, Chromium, and Edge imports use the installed browser version for the user-agent and client hints, with the closest supported transport preset. Requests are spaced at least half a second apart within a client; this does not add HTTP calls. The CLI reads the browser's cookie store without writing to it. Because the copied cookies share the browser's LinkedIn session, server-side revocation can still affect both.

## Development

Browser cookie import uses [browser-cookie3](https://github.com/borisbabic/browser_cookie3).

To test changes in this checkout, use `uv run warmpath` instead of `uvx warmpath`,
which runs the published package. For example:

```sh
uv run warmpath auth import --browser chrome
uv run warmpath auth status
```

[go-task](https://taskfile.dev/) runs the repository checks. Install it with Homebrew on macOS, or follow the [installation guide](https://taskfile.dev/docs/installation) for other platforms. Then verify the tool, install the Python development dependencies, and run the checks:

```sh
# macOS
brew install go-task

task --version
uv sync
task check
```
