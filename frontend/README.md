# Frontend — Immich Optimizer

Angular 22 (standalone, signals, zoneless) UI, served by the Flask backend. Uses
**[pnpm](https://pnpm.io/)**. Builds into `../backend/static`; talks to the
backend over relative `/api/...` calls, so no URL config is needed.

```bash
pnpm install      # install dependencies
pnpm run build    # production build -> ../backend/static
pnpm run watch    # dev rebuild on change
pnpm test         # unit tests (Vitest)
```

See the [root README](../README.md) for the full picture.
