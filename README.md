![Warmpath banner](warmpath.jpg)

# Warmpath

Find LinkedIn mutuals and warm paths using your logged-in LinkedIn cookies.

## Setup

1. Install [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc?hl=en).
2. Log in to LinkedIn in Chrome.
3. Use the extension to export cookies for `linkedin.com` in Netscape `cookies.txt` format.
4. Create `~/.config/warmpath/linkedin.cookies` and paste the full export there:

```text
~/.config/warmpath/linkedin.cookies
```

Warmpath needs the `li_at` and `JSESSIONID` cookies. Keep this file private; it lives outside the repository.

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
uvx warmpath company https://www.linkedin.com/company/hashicorp/ --cookie-file ~/.config/warmpath/linkedin.cookies
uvx warmpath skill Leadership --max-depth 2
uvx warmpath human https://www.linkedin.com/in/mitchellh/ --refresh-cache
uvx warmpath company --help
uvx warmpath skill --help
uvx warmpath human --help
```
