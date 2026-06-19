# Contributing

Thanks for your interest in contributing! This is a small project — issues and
pull requests are welcome.

## Getting started

See the [README](README.md) for setup. In short:

```bash
# Backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then fill in IMMICH_URL + IMMICH_API_KEY
.venv/bin/python backend/server.py

# Frontend (Angular, pnpm)
cd frontend && pnpm install
pnpm run build                  # builds into backend/static
pnpm test                       # unit tests
```

## Guidelines

- Keep the UI **in English**.
- Open an issue first for larger changes so we can agree on direction.
- Run the frontend tests (`pnpm test`) and make sure the Python server still
  starts before opening a PR.
- Be mindful of the security model (see [SECURITY.md](SECURITY.md)) — this app
  can delete/replace media, so changes to the encode/replace path deserve extra
  care.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
