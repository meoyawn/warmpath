![Warmpath banner](warmpath.jpg)

# Warmpath

Find LinkedIn mutuals and warm paths using your logged-in browser session.

## Setup

Log in to LinkedIn in a regular browser window, then import that session:

```sh
uvx warmpath auth import --browser chrome
uvx warmpath auth status
```

Import uses [browser-cookie3](https://github.com/borisbabic/browser_cookie3) to read your browser's local cookie store. Supported browsers are Chrome, Chromium, Firefox, Edge, Brave, Safari, Arc, Vivaldi, Opera, Opera GX (`opera-gx`), and LibreWolf; availability depends on your operating system. Your OS may ask for keychain/keyring access. If the cookie store is locked, close the browser and retry. Private-window sessions cannot be imported.

Warmpath imports only LinkedIn's `li_at` and `JSESSIONID` cookies and manages the saved session automatically. `company`, `skill`, and `human` use it on subsequent runs. Failed imports preserve the previous session.

`auth status` shows the source browser, import time, cookie expiry, and storage location without displaying cookie values. It checks the saved session locally; LinkedIn may revoke a session before its cookies expire. If the session expires or LinkedIn stops accepting it, log in again and repeat `auth import`.

## Development

[go-task](https://taskfile.dev/) is required to run the repository checks. Install it with Homebrew on macOS:

```sh
brew install go-task
```

For other platforms, follow the [go-task installation guide](https://taskfile.dev/docs/installation). Verify that the `task` command is available before running any checks:

```sh
task --version
```

Then install the Python development dependencies and run the full check suite:

```sh
uv sync
task check
```

## Usage

### Company

Who can introduce me into this company?

```sh
uvx warmpath company HashiCorp
```

### Skill

Which reachable people match this recruiting need?

```sh
uvx warmpath skill Flutter
```

### Human

Can I reach this exact person, and through whom?

```sh
uvx warmpath human https://www.linkedin.com/in/mitchellh/
```

## More Examples

```sh
uvx warmpath company "HashiCorp" --max-degree 2 --limit 5
uvx warmpath auth import --browser firefox
uvx warmpath company https://www.linkedin.com/company/hashicorp/
uvx warmpath skill Leadership --max-depth 2
uvx warmpath human https://www.linkedin.com/in/mitchellh/ --refresh-cache
uvx warmpath company --help
uvx warmpath skill --help
uvx warmpath human --help
```
