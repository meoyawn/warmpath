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

# Find reachable people with a skill
uvx warmpath skill Flutter
uvx warmpath skill Leadership --max-depth 2

# Find mutual connections with a person
uvx warmpath human https://www.linkedin.com/in/mitchellh/

# Refresh cached results
uvx warmpath human https://www.linkedin.com/in/mitchellh/ --refresh-cache

# Show options for a command; also works with auth, skill, and human
uvx warmpath company --help
```

## Browser sessions

Supported browsers are Chrome, Chromium, Firefox, Edge, Brave, Safari, Arc, Vivaldi, Opera, Opera GX (`opera-gx`), and LibreWolf; availability depends on your operating system. Your OS may ask for keychain/keyring access. If the cookie store is locked, close the browser and retry. Private-window sessions cannot be imported.

Warmpath imports only LinkedIn's `li_at` and `JSESSIONID` cookies and manages the saved session automatically. `company`, `skill`, and `human` use it on subsequent runs. Failed imports preserve the previous session.

`auth status` shows the source browser, import time, cookie expiry, and storage location without displaying cookie values. It checks the saved session locally; LinkedIn may revoke a session before its cookies expire. If the session expires or LinkedIn stops accepting it, log in again and repeat `auth import`.

## Development

Browser cookie import uses [browser-cookie3](https://github.com/borisbabic/browser_cookie3).

[go-task](https://taskfile.dev/) runs the repository checks. Install it with Homebrew on macOS, or follow the [installation guide](https://taskfile.dev/docs/installation) for other platforms. Then verify the tool, install the Python development dependencies, and run the checks:

```sh
# macOS
brew install go-task

task --version
uv sync
task check
```
