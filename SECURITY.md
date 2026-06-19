# Security Policy

Immich Optimizer is in **early development**, may still contain bugs, and is
provided **as is, with no warranty**. It modifies, replaces and deletes media in
your Immich library, so **data can be lost**. Run it **at your own risk** and
keep your own backups.

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue. Use GitHub's
**"Report a vulnerability"** button (under the repository's Security tab) or email
the maintainer. I'll try to respond within a reasonable time.

This project is in early development; only the latest `main` is supported, and
there are no security backports.

## Security model

Immich Optimizer is meant to run on your own machine or a trusted private
network, next to your Immich server. Please understand the following before
running it:

- **No authentication.** The dashboard has no login. Anyone who can reach it can
  browse, download, re-encode and **trash or replace** assets in your Immich
  library, using the API key the server holds.
- **It holds your Immich API key.** The key is read from `.env` (git-ignored) and
  kept on the server side. Never commit `.env`, and rotate the key in Immich if
  you suspect it has leaked.
- **It can change or delete media.** Replacing an asset uploads a new copy and
  moves the original to the Immich trash (recoverable for the retention period),
  writing a local backup first. Bugs could still cause data loss, so keep your
  own backups.

## Deploying safely

- The local dev server (`python backend/server.py`) binds to `127.0.0.1`
  (localhost only) by default. Set `HOST=0.0.0.0` only when you intend to.
- The Docker image binds to `0.0.0.0` so the container is reachable from its
  host. Do **not** publish that port to a LAN or the internet directly.
- To expose it beyond localhost, put it behind an **authenticating reverse
  proxy** (for example Authelia, oauth2-proxy, or Caddy with basic auth) or a
  **private network or VPN** (for example Tailscale or WireGuard).
