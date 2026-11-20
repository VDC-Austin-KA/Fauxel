# Pages project `fauxel` (web portal)

The portal is a static React build served at `https://fauxel.$FAUXEL_DOMAIN` (KA: `fauxel.ackerworks.com`). It calls `/api/v1/*` on the same origin, which the Worker in `infra/cloudflare/worker` proxies to the PC.

## Build and deploy (direct upload, no GitHub integration needed)

```bash
cd apps/web-portal && npm ci && npm run build          # -> apps/web-portal/dist
cd ../../infra/cloudflare/pages
npx wrangler login                                     # once
npx wrangler pages project create fauxel --production-branch main   # once
npx wrangler pages deploy                              # reads pages/wrangler.toml, uploads ../../../apps/web-portal/dist
```

Windows PowerShell: the same commands work. Replace `main` with your branch if different.

## Custom domain

Dashboard: Workers & Pages > `fauxel` > Custom domains > Set up a custom domain > `fauxel.ackerworks.com` (DNS is created automatically because the zone is on Cloudflare).
Check that `https://fauxel.ackerworks.com/` loads the portal (it will be behind the Access login once `access-policy.md` is applied).

## How `/api/*` and `/media/*` reach the Worker

Both routes are declared in `worker/wrangler.template.toml`. A Worker route on a hostname that also serves a Pages custom domain takes priority for the matching paths, so `/api/*` and `/media/*` hit the Worker and everything else is served by Pages. SPA routing: Pages serves `index.html` for unknown paths when the build has no top-level `404.html`.

## Notes

- Do not set `VITE_API_BASE`; the portal uses same-origin `/api/v1`.
- Pages deploys are immutable; roll back from the dashboard (Deployments > Rollback) if a build misbehaves.
- No Pages Functions are used, so the free plan is enough.
