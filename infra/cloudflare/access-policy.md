# Cloudflare Access policies

Two Access applications, both in Zero Trust > Access > Applications. `$FAUXEL_DOMAIN` = `ackerworks.com` for KA.

## 1. `Fauxel portal` : human login at `fauxel.$FAUXEL_DOMAIN`

| Field | Value |
|---|---|
| Type | Self-hosted |
| Application domain | `fauxel.ackerworks.com` (no path = whole hostname, covers `/`, `/api/*`, `/media/*`) |
| Session duration | 24 hours |
| Policy 1 | Name `KA`, Action **Allow**, Include: Emails = KA's email address |
| Policy 2 (optional, for plugins/scripts) | Name `Fauxel machines`, Action **Service Auth**, Include: Service Token = `fauxel-clients` |

Identity provider: One-time PIN works with no setup; add Google if preferred.

The Worker receives `Cf-Access-Authenticated-User-Email` and `Cf-Access-Jwt-Assertion` only for requests that passed this app, and forwards the email to the API when it also sees the JWT. Set `FAUXEL_TRUST_ACCESS_HEADER=1` in `infra/.env` once the API is reachable only through the Worker origin app below. It is optional (the portal works with an API key in Settings) and must stay `0` whenever the portal app has a Bypass policy: the Worker cannot tell a forged identity header from a real one then.

**Public for a day:** `.\Start-Fauxel.ps1 -PublicToday` opens a keyless, read-only window that the API closes by itself at 23:59 (integrations, uploads and training export stay closed; writes still need a key). Then add a policy `Public today` (Action Bypass, Include Everyone) at the top of the `Fauxel portal` app, and delete it the next day. `-Private` closes the window early.

Revit, Navisworks and field scripts (`fauxel-osc upload`) cannot do a browser login. They call `https://fauxel.ackerworks.com/api/v1/...` with `X-Fauxel-Key` plus the headers `CF-Access-Client-Id` and `CF-Access-Client-Secret` of the `fauxel-clients` token (Policy 2). `fauxel-osc` reads these from `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`.

## 2. `Fauxel origin` : the Worker to PC hop at `fauxel-origin.$FAUXEL_DOMAIN`

| Field | Value |
|---|---|
| Type | Self-hosted |
| Application domain | `fauxel-origin.ackerworks.com` |
| Policy | Name `Worker service token`, Action **Service Auth**, Include: Service Token = `fauxel-worker` |

Never add an Allow/Bypass-everyone policy here. Without a valid token nothing can reach the tunnel, which is what makes it safe to publish the API through `cloudflared`.

## 3. `fauxel-files.$FAUXEL_DOMAIN` (only without R2): no Access app

When media stays on the PC (`FAUXEL_MEDIA=local`, see `docs/setup/quickstart.md` section 5) the tunnel also publishes the stack's S3 storage at `fauxel-files.$FAUXEL_DOMAIN` → `http://s3:9000` (never the console port 9001). Do not put an Access app on it: browsers and phones PUT to presigned URLs cross-origin without Access cookies or headers. The S3 signature is the protection: the bucket has no anonymous policy, so unsigned requests get 403 (verify: `curl -si https://fauxel-files.ackerworks.com/fauxel-media/ | head -1`).

## Service tokens

Zero Trust > Access > Service Auth > Create Service Token. Create two:

1. `fauxel-worker` (duration: non-expiring, or 1 year with a reminder). Copy the Client ID and Client Secret once; store them in the Worker:
   ```bash
   cd infra/cloudflare/worker
   npx wrangler secret put ORIGIN_ACCESS_CLIENT_ID
   npx wrangler secret put ORIGIN_ACCESS_CLIENT_SECRET
   ```
2. `fauxel-clients` for plugins and field scripts (only if you use Policy 2 above).

Rotate: create a new token, `wrangler secret put` the new values, update the policy, delete the old token.

## Verify

```bash
# 1. origin refuses anonymous callers (expect 302/403, never JSON)
curl -si https://fauxel-origin.ackerworks.com/api/v1/health | head -3
# 2. origin accepts the worker token (expect 200 {"status":"ok",...})
curl -s -H "CF-Access-Client-Id: $ID" -H "CF-Access-Client-Secret: $SECRET" https://fauxel-origin.ackerworks.com/api/v1/health
# 3. portal host without a login (expect redirect to cloudflareaccess.com)
curl -si https://fauxel.ackerworks.com/api/v1/health | head -3
# 4. machine call
curl -s -H "CF-Access-Client-Id: $CID" -H "CF-Access-Client-Secret: $CSECRET" -H "X-Fauxel-Key: $KEY" https://fauxel.ackerworks.com/api/v1/health
```
