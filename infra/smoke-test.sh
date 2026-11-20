#!/usr/bin/env bash
# End-to-end smoke test of a running stack (docker compose up -d --build first). Needs: bash, curl, docker compose.
#   infra/smoke-test.sh                       # API http://127.0.0.1:8000, storage browser URL as the API returns it
# Covers: health, API key bootstrap (admin token), project, IFC through the DIRECT multipart flow (+ CORS/ETag check as a
# browser from the portal origin would see it), worker ingest -> elements, image capture through the same flow -> frames,
# a real 2-part multipart upload, the portal's /api proxy.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
[ -f .env ] && set -a && . ./.env && set +a
API="${API_URL:-http://127.0.0.1:${API_PORT:-8000}}"
PORTAL="${PORTAL_URL:-http://127.0.0.1:${PORTAL_PORT:-8080}}"
ORIGIN="${SMOKE_ORIGIN:-https://fauxel.${FAUXEL_DOMAIN:-ackerworks.com}}"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
pass=0; fail=0
ok()  { pass=$((pass+1)); printf '  [PASS] %s\n' "$1"; }
bad() { fail=$((fail+1)); printf '  [FAIL] %s\n' "$1"; }
die() { bad "$1"; printf '\nSMOKE TEST FAILED (%s passed, %s failed)\n' "$pass" "$fail"; exit 1; }

# JSON helper: python3 on the host if there is one, else inside the api container
if command -v python3 >/dev/null 2>&1; then PY() { python3 -c "$@"; }; else PY() { docker compose exec -T api python -c "$@"; }; fi
jget() { PY 'import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"; }
hdr()  { tr -d '\r' < "$1" | grep -i "^$2:" | head -1 | sed 's/^[^:]*: *//'; }

echo "== health"
for i in $(seq 1 60); do
  h="$(curl -fsS "$API/api/v1/health" 2>/dev/null || true)"
  [ -n "$h" ] && [ "$(echo "$h" | jget 'd["status"]')" = ok ] && break
  sleep 2
done
[ -n "${h:-}" ] && [ "$(echo "$h" | jget 'd["status"]')" = ok ] || die "API health not ok: ${h:-no answer}"
ok "api health $h"

echo "== auth bootstrap"
H=(-H "Content-Type: application/json")
# Against the public hostname (API_URL=https://fauxel.ackerworks.com) Access needs the API-client service token
if [ -n "${FAUXEL_CF_ACCESS_CLIENT_ID:-}" ] && [ -n "${FAUXEL_CF_ACCESS_CLIENT_SECRET:-}" ]; then
  H+=(-H "CF-Access-Client-Id: $FAUXEL_CF_ACCESS_CLIENT_ID" -H "CF-Access-Client-Secret: $FAUXEL_CF_ACCESS_CLIENT_SECRET")
fi
if [ "${FAUXEL_AUTH:-on}" = on ]; then
  [ -n "${FAUXEL_ADMIN_TOKEN:-}" ] || die "FAUXEL_ADMIN_TOKEN missing in infra/.env"
  key="$(curl -fsS -X POST "$API/api/v1/api-keys" -H "X-Fauxel-Admin: $FAUXEL_ADMIN_TOKEN" "${H[@]}" -d '{"name":"smoke-test"}' | jget 'd["key"]')"
  [ -n "$key" ] || die "no api key"
  H+=(-H "X-Fauxel-Key: $key")
  code="$(curl -s -o /dev/null -w '%{http_code}' "$API/api/v1/projects")"   # (anonymous: no key, no Access token)
  [ "$code" = 401 ] && ok "anonymous request refused (401)" || bad "anonymous /projects returned $code, expected 401"
fi
api() { local m="$1" p="$2"; shift 2; curl -fsS -X "$m" "$API/api/v1$p" "${H[@]}" "$@"; }

echo "== project"
pid="$(api POST /projects -d '{"name":"smoke test"}' | jget 'd["id"]')"; ok "project $pid"

# direct_upload <kind> <file> <content_type> -> echoes the object key; also leaves $tmp/put.hdr from the last part PUT
direct_upload() {
  local kind="$1" file="$2" ctype="$3" size name resp uid n url
  size="$(wc -c < "$file" | tr -d ' ')"; name="$(basename "$file")"
  resp="$(api POST /uploads -d "{\"project_id\":\"$pid\",\"filename\":\"$name\",\"size\":$size,\"content_type\":\"$ctype\",\"kind\":\"$kind\"}")"
  uid="$(echo "$resp" | jget 'd["upload_id"]')"
  local psize nparts; psize="$(echo "$resp" | jget 'd["part_size"]')"; nparts="$(echo "$resp" | jget 'len(d["parts"])')"
  local parts_json=""
  for n in $(seq 1 "$nparts"); do
    url="$(echo "$resp" | jget "d['parts'][$((n-1))]['url']")"
    head -c $((n*psize)) "$file" | tail -c +$(( (n-1)*psize + 1 )) > "$tmp/part.bin"
    # Origin header = what the portal's browser sends; the response must expose ETag for the JS to read it
    curl -fsS -X PUT -D "$tmp/put.hdr" -o /dev/null -H "Origin: $ORIGIN" --data-binary @"$tmp/part.bin" "$url"
    local etag; etag="$(hdr "$tmp/put.hdr" etag)"; [ -n "$etag" ] || { echo "no ETag in PUT response" >&2; return 1; }
    parts_json="$parts_json{\"part_number\":$n,\"etag\":$(PY 'import json,sys; print(json.dumps(sys.argv[1]))' "$etag")},"
  done
  echo "$nparts" > "$tmp/nparts"
  api POST "/uploads/$uid/complete" -d "{\"parts\":[${parts_json%,}]}" | jget 'd["key"]'
}

echo "== IFC via direct multipart flow"
ifckey="$(direct_upload model "$here/smoke/tiny.ifc" application/octet-stream)" || die "IFC direct upload failed"
ok "IFC uploaded straight to storage (key $ifckey, $(cat "$tmp/nparts") part)"
expose="$(hdr "$tmp/put.hdr" access-control-expose-headers)"
case "$(echo "$expose" | tr 'A-Z' 'a-z')" in *etag*) ok "storage CORS exposes ETag to $ORIGIN";; *) bad "storage PUT response lacks Access-Control-Expose-Headers: ETag (got '$expose')";; esac
allow="$(hdr "$tmp/put.hdr" access-control-allow-origin)"
[ "$allow" = "$ORIGIN" ] && ok "storage CORS allows origin $ORIGIN" || bad "Access-Control-Allow-Origin was '$allow', expected $ORIGIN"
job="$(api POST "/projects/$pid/models" -d "{\"upload_key\":\"$ifckey\"}" | jget 'd["job_id"]')"
wait_job() { local s; for i in $(seq 1 120); do s="$(api GET "/jobs/$1")"; case "$(echo "$s" | jget 'd["status"]')" in done) return 0;; failed) echo "$s" >&2; return 1;; esac; sleep 2; done; return 2; }
wait_job "$job" || die "IFC ingest job did not finish (docker compose logs worker)"
ok "worker ingested the IFC (job $job)"
curl -fsS -D "$tmp/el.hdr" -o "$tmp/el.json" "$API/api/v1/projects/$pid/elements" "${H[@]}"
total="$(hdr "$tmp/el.hdr" x-total-count)"
[ "${total:-0}" -ge 1 ] && ok "elements exist: X-Total-Count=$total" || die "no elements after IFC ingest"

echo "== image capture via direct multipart flow"
pkey="$(direct_upload capture "$here/smoke/pano.jpg" image/jpeg)" || die "capture direct upload failed"
cap="$(api POST "/projects/$pid/captures" -d "{\"upload_keys\":[\"$pkey\"],\"camera_model\":\"insta360_one_x2\"}")"
cid="$(echo "$cap" | jget 'd.get("capture_id") or d["id"]')"; cjob="$(echo "$cap" | jget 'd["job_id"]')"
wait_job "$cjob" || die "capture ingest job failed (docker compose logs worker)"
cs="$(api GET "/captures/$cid" | jget 'd["status"]')"; [ "$cs" = ready ] && ok "capture $cid status ready" || die "capture status '$cs', expected ready"
nf="$(api GET "/captures/$cid/frames" | jget 'len(d)')"; [ "$nf" -ge 1 ] && ok "capture has $nf panorama frame(s)" || die "no frames"
code="$(curl -s -o "$tmp/f.jpg" -w '%{http_code}' "$API/api/v1/frames/$(api GET "/captures/$cid/frames" | jget 'd[0]["id"]')/image?w=256" "${H[@]}")"
[ "$code" = 200 ] && ok "frame image served (200)" || bad "frame image HTTP $code"

echo "== real 2-part multipart upload (20 MB, parts of 16 MB)"
head -c 20971520 /dev/urandom > "$tmp/big.mov"
bkey="$(direct_upload capture "$tmp/big.mov" video/quicktime)" || die "2-part upload failed"
[ "$(cat "$tmp/nparts")" = 2 ] && ok "2 parts PUT and completed (key $bkey)" || bad "expected 2 parts, got $(cat "$tmp/nparts")"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/projects/$pid/captures" "${H[@]}" -d "{\"upload_keys\":[\"uploads/$pid/not-a-real-key/x.mov\"]}")"
[ "$code" = 422 ] && ok "unknown upload_key refused (422)" || bad "unknown upload_key returned $code, expected 422"

echo "== portal proxy"
[ "$(curl -fsS "$PORTAL/api/v1/health" | jget 'd["status"]')" = ok ] && ok "portal $PORTAL proxies /api/v1/health" || bad "portal proxy"
curl -fsS "$PORTAL/" | grep -qi "<div id=\"root\"\|<title" && ok "portal serves index.html" || bad "portal index"

printf '\nSMOKE TEST %s (%s passed, %s failed)\n' "$([ "$fail" = 0 ] && echo PASSED || echo FAILED)" "$pass" "$fail"
[ "$fail" = 0 ]
