# Fauxel Field (apps/field-app)

Expo SDK 58 / React Native 0.88 / TypeScript strict / expo-router. iOS and Android. Roadmap item **M8.1**.

| Tab | What it does |
|---|---|
| Camera | Connect to the Insta360 ONE X2 over its Wi-Fi AP (OSC level 2, `http://192.168.42.1`), live battery/storage/capture state, construction preset (video 5.7K 30 fps or HDR photo; shutter 1/250 + shutter priority only if `camera.getOptions` lists support, otherwise the refusal is shown), start/stop video, photo, list camera files, resumable download (HTTP Range) with SHA-256 |
| Projects | Sign in (Fauxel URL + API key in `expo-secure-store`), pick project and level, floorplan from `/projects/{pid}/lbs` zones cached in SQLite (works offline), PNG/JPG sheet import as background, pan/zoom |
| Walk | Tap start point, start walk (records start pin, timestamps, camera file names), photo pins (phone camera), field notes and QC issues with dictation, coverage per zone |
| Upload | Persistent SQLite queue, per-item progress and retry, backoff, resumes after restart, local notification when a capture reports `processed` |

## Layout

```
src/app/            expo-router screens (index = Camera, projects, walk, queue)
src/lib/osc/        OSC client + presets (parity with tools/insta360-osc/fauxel_osc/osc.py)
src/lib/download/   rangeDownload.ts (pure resume logic), sha256.ts
src/lib/floorplan/  coords.ts (world/plan/screen math), plan.ts (zones -> plan, coverage, SVG)
src/lib/queue/      stateMachine.ts (pure), worker.ts (drain loop), db.ts (expo-sqlite)
src/lib/services/   session (secure store), camera download, native upload, planStore, speech, notifications
plugins/withCameraCleartext.js   config plugin (below)
__tests__/          jest-expo unit tests
```

## Develop

```bash
cd apps/field-app
npm install                 # .npmrc sets legacy-peer-deps: expo-router's peer on react-dom 19.3.1 conflicts with SDK 58's pinned react 19.3.0
npm run typecheck           # tsc --noEmit
npm test                    # jest-expo: OSC client, queue state machine + worker, range resume, floorplan math
npx expo export --platform android   # bundle check
npx expo-doctor
```

### Expo Go vs development build

Expo Go is enough to see the UI and exercise Projects, Walk and Upload, but it **cannot** run this app fully:

- the config plugin (cleartext to the camera) needs native config, so the camera tab will be blocked by ATS/Android cleartext rules in Expo Go;
- `expo-speech-recognition` is not in Expo Go (the app detects this and falls back to the keyboard dictation key);
- `expo-notifications` push features are limited in Expo Go.

Use a development build for anything that touches the camera.

### EAS build for KA's phones

```bash
npm i -g eas-cli && eas login
eas init                                   # once; creates the EAS project id
# Android: installable APK
eas build --profile development --platform android
# iPhone: internal distribution needs the device registered and an Apple developer account
eas device:create
eas build --profile development --platform ios
npx expo start --dev-client                # then open the installed "Fauxel Field" app
```

The `development` profile sets `FAUXEL_DEV_CLEARTEXT=1`, which makes the Android security config also allow cleartext globally so Metro on a LAN IP loads. `preview` and `production` builds do not: only the camera is allowed.

## Why cleartext HTTP to 192.168.42.1

OSC has no TLS. The camera is a Wi-Fi AP serving plain `http://192.168.42.1`, and both platforms block that by default. `plugins/withCameraCleartext.js` opens the minimum:

- **Android**: `res/xml/network_security_config.xml` with `base-config cleartextTrafficPermitted="false"` and one `domain-config` for `192.168.42.1`. Everything else stays HTTPS-only.
- **iOS**: an `NSExceptionDomains` entry for `192.168.42.1` (ATS honors IP-address exception entries on current iOS; see Apple forum thread 747421). `NSAllowsLocalNetworking` is also set as a fallback for older iOS; note that flag permits cleartext to any local-network host, not only the camera. `NSLocalNetworkUsageDescription` is set so iOS shows the Local Network prompt.

The Fauxel server connection is separate and must be HTTPS (the sign-in form rejects plain http except for LAN dev hosts and always rejects 192.168.42.1).

## Behaviour notes

- **Two networks.** The phone cannot be on the camera Wi-Fi and the internet at once. The queue pauses when it sees a `192.168.42.x` address or no internet, and resumes on job-site Wi-Fi or 5G. Android may route traffic over cellular while the camera Wi-Fi is up; if the camera is unreachable, turn off "switch to mobile data automatically".
- **Downloads** write 4 MiB chunks at their offset into `<name>.part`; the resume point is the part file size. If the camera ignores Range the app falls back to one full download. SHA-256 uses native `File.digest('SHA-256')` (streaming) because `expo-crypto` only does one-shot digests, which cannot hold multi-GB files; `expo-crypto` is used for UUIDs.
- **Raw `.insv`/`.insp` are not queued.** Fauxel ingests equirect JPG/PNG/MP4 only (same rule as the Python CLI). Stitch in Insta360 Studio and use "Add stitched file" until roadmap M2.4 (OpenCV stitcher) lands.
- **Upload shape (resumable).** Each capture goes through the direct multipart API: `POST /uploads` (kind `capture`), one `PUT` per 16-64 MB part (presigned S3/R2 URL, or the API's own part URL; the API key is sent only to the API), `POST /uploads/{id}/complete`, then `POST /projects/{pid}/captures {upload_keys}` (202, queued ingest). The upload id and key are checkpointed into the queue item, so after a crash, network switch or camera-Wi-Fi detour the next attempt calls `GET /uploads/{id}` and sends only the missing parts. Expired part URLs (403) are refreshed; a dropped part is retried 3x inside the attempt, then the queue backoff takes over (2 s doubling to 5 min, 6 attempts, `Retry-After` honored). Parts are read from disk one at a time, so no request exceeds Cloudflare's 100 MB cap and multi-GB walks never enter JS memory whole. Logic: `src/lib/upload/multipart.ts` (tested in `__tests__/multipart.test.ts`).
- **QC notes** post to `POST /projects/{pid}/deviations` with `kind`, `severity` and a note that embeds level and x,y in meters (the API only derives geometry when an `element_uid` is given). Phone photos for pins are kept on the phone; the contract has no evidence upload for deviations yet.
- **Floorplan.** `/lbs` zones are `{id, name}` only in the v1 contract, so zones without a `polygon` (meters, project frame) are drawn as a schematic grid. Zones that include `polygon` render as real geometry. Imported PNG/JPG sheets are stretched to the plan bounds; PDFs are stored and opened with the OS previewer (no in-app PDF raster without another native lib). Coverage per zone is an estimate from the start point and pins (6 m radius).
- **Notifications.** The app polls `GET /captures/{cid}` (every 20 s while open) and fires a local notification on `processed`. Real push while the app is closed needs an Expo push token registered with the server and a server hook on `inference.completed`. Not built. Note the poll only sees `processed` if something calls `POST /captures/{cid}/process`.

## Limitations

No device test was possible where this was built (Linux, no phone, no ONE X2). Verified: typecheck, jest unit tests against mocked fetch/IO, Android bundle export, `expo-doctor`, and `expo prebuild` output of the config plugin. Unverified on hardware: actual OSC option names on ONE X2 firmware (`fileFormat` shape, `hdr`, `shutterSpeed`, `remainingSpace`/`totalSpace`), whether the camera honors Range, iOS Local Network prompt, Android routing on the camera Wi-Fi, background behavior of uploads (foreground session only), speech recognition, and gesture feel on the floorplan.
