# fauxel-osc

Field capture CLI for the Insta360 ONE X2 (and any camera speaking the Open Spherical Camera API level 2), plus upload to the Fauxel API.

```bash
pip install -e tools/insta360-osc           # from the repo root; only dependency is requests
# join the camera's Wi-Fi AP first (ONE X2 default address 192.168.42.1)
fauxel-osc info
fauxel-osc state                              # battery, storage, capture status
fauxel-osc set-options --mode photo --iso 200 --ev 0
fauxel-osc take-photo --download ./raw
fauxel-osc start-video ; fauxel-osc stop-video --download ./raw
fauxel-osc list-files --type image
fauxel-osc download --latest --out ./raw      # resumable (HTTP Range), writes <file>.sha256
export FAUXEL_API_URL=https://fauxel.ackerworks.com FAUXEL_API_KEY=... \
       FAUXEL_CF_ACCESS_CLIENT_ID=... FAUXEL_CF_ACCESS_CLIENT_SECRET=...
fauxel-osc upload ./stitched --project <project-uuid> --level-id <level-uuid>
```

## Behaviour notes

- Endpoints: `GET /osc/info`, `POST /osc/state`, `POST /osc/commands/execute`, `POST /osc/commands/status` (long commands are polled until `done`).
- `set-options` reads `camera.getOptions` for each `<option>Support` list first. Unsupported options or values are refused with a reason (exit code 3 only when nothing could be applied); a camera-side rejection falls back to one-by-one so one bad option never blocks the rest. Options the camera does not report support for are attempted anyway.
- `download` resumes a `<file>.part` with `Range: bytes=N-`; if the camera ignores Range it restarts. A SHA-256 is printed and stored in `<file>.sha256`.
- `upload` uses the direct multipart flow, so no request body passes through Cloudflare (which rejects bodies over 100 MB): `POST /uploads` per file, `PUT` each 16-64 MB part straight to R2/MinIO (presigned URLs, ETag read from the response), `POST /uploads/{id}/complete`, then one `POST /projects/{pid}/captures {upload_keys, level_id, captured_at, camera_model}` (202 + `job_id`; `--wait` polls `GET /jobs/{id}`). Interrupted runs resume: the upload id is kept in `<file>.fauxel-upload.json`, a re-run asks `GET /uploads/{id}` and sends only the missing parts (and gets fresh URLs after the 1 h expiry). Retries use exponential backoff with jitter on 408/425/429/5xx and connection errors; other 4xx are not retried. Presigned part URLs never get the Fauxel/Access headers; through the Cloudflare hostname the API calls need the Access service-token headers (`FAUXEL_CF_ACCESS_CLIENT_ID` / `FAUXEL_CF_ACCESS_CLIENT_SECRET`; the older `CF_ACCESS_*` names still work; an Access login page is detected and reported with this hint, see `infra/cloudflare/access-policy.md`). From the PC itself use `--api-url http://127.0.0.1:8000`. If storage answers without an `ETag`, bucket CORS is missing `ExposeHeaders: ["ETag"]`.

## Raw files

The ONE X2 stores `.insp` (photos) and `.insv` (video, one file per lens) as dual-fisheye raw. The API worker stitches raw `.insv/.insp` (`fauxel_capture.insta360.stitch_file`) and keyframes video, so `upload` accepts raw files as well as already-stitched equirect JPG/PNG/MP4/MOV. Upload the raw files of one capture together.

## Tests

`python -m pytest tools/insta360-osc` runs against a fake OSC camera and fake Fauxel API + object store in `tests/fakes.py` (no hardware needed). Not yet verified against a real ONE X2: field-test `info`, `set-options` and `take-photo` first, since camera firmware varies in which options it exposes.
