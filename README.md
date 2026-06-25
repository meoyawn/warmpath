# Warmpath

Find LinkedIn mutuals and referral paths using your logged-in LinkedIn cookies.

## Setup

1. Install [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc?hl=en).
2. Log in to LinkedIn in Chrome.
3. Use the extension to export cookies for `linkedin.com` in Netscape `cookies.txt` format.
4. Create `cookies/linkedin.cookies` and paste the full export there:

```text
cookies/linkedin.cookies
```

Warmpath needs the `li_at` and `JSESSIONID` cookies. Keep this file private; it is ignored by Git.

## Usage

### Find a path to a company

```sh
uv run warmpath company https://www.linkedin.com/company/hashicorp/
```

By default, company search checks up to second-degree connections and prints mutual introducers when LinkedIn exposes them.

### Check connection status for a person

```sh
uv run warmpath human https://www.linkedin.com/in/mitchellh/
```

### Find connections by skill

```sh
uv run warmpath skill Flutter
```

By default, skill search checks first- and second-degree profiles and verifies the requested skill against each profile's listed skills.

## More Examples

```sh
uv run warmpath company "HashiCorp" --max-degree 2 --limit 5
uv run warmpath company https://www.linkedin.com/company/hashicorp/ --cookie-file cookies/linkedin.cookies
uv run warmpath human https://www.linkedin.com/in/mitchellh/ --refresh-cache
uv run warmpath skill Leadership --max-depth 2
uv run warmpath human --help
uv run warmpath company --help
uv run warmpath skill --help
```
