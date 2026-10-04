---
name: angular-frontend
description: Use when working on the Angular 22 frontend in frontend/ — adding or editing components, services, models, or styles, and building/testing the UI. Covers the stack conventions (standalone, zoneless, signals, OnPush), pnpm commands, and how the build wires into the Flask backend.
---

# Angular 22 frontend

The web UI lives in `frontend/`. Source is in `frontend/src/app/`. It compiles into
`backend/static/` (see `angular.json` `outputPath`), which Flask serves at `/static/`
with `baseHref: /static/`. There is **no separate web server in production** — Flask
serves the built UI.

## Toolchain

- **Package manager is pnpm** (`pnpm@10.30.2`), Node 22. Do not use `npm`/`yarn` here.
  Install with `pnpm install --frozen-lockfile`.
- Angular **22**, TypeScript ~6.0, `@ornery/ui-grid` for the data table (Angular peer
  deps pinned via `pnpm.overrides`).
- Prettier is configured (`frontend/.prettierrc`); match existing formatting.

## Commands (run from `frontend/`)

```bash
pnpm install                 # or: pnpm install --frozen-lockfile (CI)
pnpm run build               # production build -> ../backend/static
pnpm run watch               # rebuild on change (development config)
pnpm run test                # ng test (vitest via @angular/build:unit-test)
pnpm exec ng test --watch=false   # one-shot, as CI runs it
```

From the repo root, `pnpm run build:frontend` / `watch:frontend` proxy to the above.

## Running with the backend during development

API calls use **relative `/api/*` URLs** (see `services/api.service.ts`) and there is
**no `ng serve` proxy config**. So `ng serve` alone cannot reach the API. To see the UI
and API together, build into `backend/static` and run Flask:

```bash
pnpm run build            # (or pnpm run watch in another terminal)
# then from repo root: .venv/bin/python backend/server.py  -> http://127.0.0.1:5050
```

## Conventions (match these)

- **Standalone components** only (no NgModules). `standalone: true`, `selector: 'app-…'`.
- **Zoneless** change detection (`provideZonelessChangeDetection` in `app.config.ts`);
  every component uses `changeDetection: ChangeDetectionStrategy.OnPush`.
- Use **signals** for state and `inject()` for DI (not constructor params). Component
  outputs use the `output()` function, not `@Output()`.
- Templates and styles are **separate files** (`templateUrl` / `styleUrl`), one per
  component folder: `name.ts`, `name.html`, `name.css`.
- `app.config.ts` providers: zoneless CD, global error listeners, `provideHttpClient(withFetch())`.

## Key files

- `src/app/app.ts` — root component, wires services + child components.
- `src/app/services/store.service.ts` — central signal state + localStorage persistence.
- `src/app/services/api.service.ts` — typed `HttpClient` for all `/api/*` endpoints.
- `src/app/services/events.service.ts` — SSE connection (job/queue updates).
- `src/app/models/api.models.ts` — shared response/DTO types.
- `src/app/components/*/` — header, dashboard, settings-panel, queue-panel, bulk-bar,
  detail-drawer, review-modal, browse-dialog, media-grid (the ui-grid table).

After changing the frontend, rebuild (`pnpm run build`) so `backend/static/` reflects it.
