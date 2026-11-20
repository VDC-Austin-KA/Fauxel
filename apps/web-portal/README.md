# Fauxel web portal

React 19 + TypeScript (strict) + Tailwind 4 + react-router + react-query + three.js + Recharts. Static build, talks to `/api/v1` (contract: `docs/specs/platform-contract.md`).

Screens: Portfolio, Dashboard, 3D model (level/trade filters; color by installation status, current condition, or schedule variance with an as-of date and on-time range; legend toggles groups), Reality viewer (360 + BIM, synced rig, AR overlay, minimap, compare, 3-point align), Verification queue, Elements, Schedule & EVM, QC issues (attachments, BCF 3.0/2.1 export, BCF import), Uploads (direct multipart), Integrations (API keys, write-back, Autodesk Forma/APS, Speckle, health).

## Develop
```
npm install
npm run dev          # http://localhost:5173, proxies nothing: set VITE_API_BASE or use mock
npm run typecheck
npm test             # vitest: status colors, equirect, EVM, multipart planning/retry
npm run build        # -> dist/
npm run preview      # http://localhost:4173
npm run screenshots  # needs preview running + a Chromium (CHROMIUM=/path, default /opt/pw-browsers/chromium) -> docs/screenshots/
```

## Mock (demo) mode
Set `VITE_FAUXEL_MOCK=1`, or just run with no API: if `GET /api/v1/health` fails the portal switches to an in-browser mock of the whole contract (uploads, jobs, QC, APS, Speckle included) and shows a **Demo data** badge.

## Env vars
| Var | Default | Purpose |
|---|---|---|
| `VITE_API_BASE` | `/api/v1` | API base URL |
| `VITE_FAUXEL_MOCK` | unset | `1` forces demo data |

Auth: paste an API key in Integrations (sent as `X-Fauxel-Key`), or rely on Cloudflare Access.

## Uploads and storage CORS
Parts are PUT straight to presigned URLs. The bucket CORS must allow `PUT` from the portal origin and **expose the `ETag` header** (`ExposeHeaders: ["ETag"]`), otherwise the part ETags cannot be read and completion fails with a clear error.

## Deploy to Cloudflare Pages
```
npm run build
npx wrangler pages deploy dist --project-name fauxel
```
`public/_redirects` provides the SPA fallback. Route `/api/*` to the API Worker on the same hostname.
