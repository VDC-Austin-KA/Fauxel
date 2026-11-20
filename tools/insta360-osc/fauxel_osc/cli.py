"""fauxel-osc command line."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

from .osc import DEFAULT_HOST, OscClient, OscError
from .upload import CAPTURE_EXT, UploadError, Uploader, target_from_env


def _emit(args, obj: Any, text: str | None = None) -> None:
    if args.json or text is None:
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(text)


def _coerce(v: str) -> Any:
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.startswith(("{", "[")):
        return json.loads(v)
    return v


def _client(args) -> OscClient:
    return OscClient(args.host, args.port, args.timeout)


# ---- commands -----------------------------------------------------------------------------------------------
def cmd_info(args) -> int:
    i = _client(args).info()
    _emit(args, i, "\n".join(f"{k}: {i.get(k)}" for k in ("manufacturer", "model", "serialNumber", "firmwareVersion", "apiLevel") if k in i))
    return 0


def cmd_state(args) -> int:
    st = _client(args).state().get("state", {})
    batt = st.get("batteryLevel")
    lines = [
        f"battery: {round(batt * 100)}%" if isinstance(batt, (int, float)) else "battery: unknown",
        f"storageUri: {st.get('storageUri', 'unknown')}",
    ]
    for k in ("_captureStatus", "_recordedTime", "_recordableTime", "_storageFree", "_storageTotal"):
        if k in st:
            lines.append(f"{k.lstrip('_')}: {st[k]}")
    _emit(args, st, "\n".join(lines))
    return 0


def cmd_get_options(args) -> int:
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    names += [f"{n}Support" for n in list(names)] if args.support else []
    _emit(args, _client(args).get_options(names))
    return 0


def cmd_set_options(args) -> int:
    opts: dict[str, Any] = {}
    if args.mode:
        opts["captureMode"] = {"photo": "image", "video": "video"}[args.mode]
    if args.exposure_program is not None:
        opts["exposureProgram"] = args.exposure_program
    if args.iso is not None:
        opts["iso"] = args.iso
    if args.shutter_speed is not None:
        opts["shutterSpeed"] = args.shutter_speed
    if args.ev is not None:
        opts["exposureCompensation"] = args.ev
    if args.white_balance:
        opts["whiteBalance"] = args.white_balance
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        if not v:
            print(f"--set expects name=value, got {kv!r}", file=sys.stderr)
            return 2
        opts[k] = _coerce(v)
    if not opts:
        print("nothing to set", file=sys.stderr)
        return 2
    res = _client(args).set_options(opts)
    _emit(args, {"applied": res.applied, "refused": res.refused},
          "applied: " + (json.dumps(res.applied) or "{}") + "".join(f"\nrefused {k}: {why}" for k, why in res.refused.items()))
    if res.refused and not res.applied:
        return 3
    return 0


def cmd_take_photo(args) -> int:
    c = _client(args)
    res = c.set_mode("photo")
    for k, why in res.refused.items():
        print(f"note: {k} not changed ({why})", file=sys.stderr)
    url = c.take_picture()
    if not url:
        print("camera returned no fileUrl (some models only report it via list-files)", file=sys.stderr)
        return 1
    print(url)
    if args.download:
        path, sha = c.download(url, args.download)
        print(f"{path}  sha256={sha}")
    return 0


def cmd_start_video(args) -> int:
    c = _client(args)
    res = c.set_mode("video")
    for k, why in res.refused.items():
        print(f"note: {k} not changed ({why})", file=sys.stderr)
    c.start_video()
    print("recording")
    return 0


def cmd_stop_video(args) -> int:
    c = _client(args)
    urls = c.stop_video()
    for u in urls:
        print(u)
    if args.download:
        for u in urls:
            path, sha = c.download(u, args.download)
            print(f"{path}  sha256={sha}")
    return 0


def cmd_list_files(args) -> int:
    rows = list(_client(args).list_files(args.type, limit=args.limit))
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for e in rows:
            print(f"{e.get('size', '?'):>12}  {e.get('dateTimeZone', e.get('dateTime', '')):<28} {e.get('fileUrl') or e.get('name')}")
        print(f"{len(rows)} file(s)")
    return 0


def cmd_download(args) -> int:
    c = _client(args)
    urls = list(args.url or [])
    if args.latest or args.all:
        entries = list(c.list_files(args.type, limit=None if args.all else 1))
        urls += [e.get("fileUrl") or e.get("name") for e in entries]
    if not urls:
        print("give a URL, --latest or --all", file=sys.stderr)
        return 2
    last_print = [0.0]

    def prog(n: int) -> None:
        if time.monotonic() - last_print[0] > 1.0:
            last_print[0] = time.monotonic()
            print(f"\r  {n / 1e6:8.1f} MB", end="", file=sys.stderr, flush=True)

    for u in urls:
        path, sha = c.download(u, args.out, progress=prog)
        print(f"\n{path}  sha256={sha}", file=sys.stderr)
        print(path)
    return 0


def cmd_upload(args) -> int:
    key = args.api_key or os.environ.get("FAUXEL_API_KEY")
    base = args.api_url or os.environ.get("FAUXEL_API_URL")
    if not base:
        print("set --api-url or FAUXEL_API_URL (e.g. https://fauxel.ackerworks.com or http://127.0.0.1:8000)", file=sys.stderr)
        return 2
    target = target_from_env(base, key, args.cf_client_id, args.cf_client_secret)
    files: list[str] = []
    for p in args.files:
        if os.path.isdir(p):
            files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(CAPTURE_EXT))
        else:
            files.append(p)
    bad = [f for f in files if not f.lower().endswith(CAPTURE_EXT)]
    if bad:
        print("unsupported file type (equirect JPG/PNG, MP4/MOV, or raw .insp/.insv): " + ", ".join(bad), file=sys.stderr)
        return 2
    if not files:
        print("no uploadable files (JPG/PNG/MP4/MOV or raw .insp/.insv)", file=sys.stderr)
        return 2
    up = Uploader(target, attempts=args.attempts, log=lambda m: print(m, file=sys.stderr))
    try:
        res = up.upload_capture(files, args.project, args.level_id, args.captured_at, args.camera_model, wait=args.wait)
    except (UploadError, requests.RequestException) as e:
        print(f"upload failed: {e}", file=sys.stderr)
        return 1
    _emit(args, res, f"capture {res.get('id') or res.get('capture_id')}: {res}")
    return 0


# ---- parser -------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fauxel-osc", description="Insta360 / OSC field capture and Fauxel upload")
    p.add_argument("--host", default=os.environ.get("FAUXEL_OSC_HOST", DEFAULT_HOST), help="camera address (Wi-Fi AP default 192.168.42.1)")
    p.add_argument("--port", type=int, default=80)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="/osc/info").set_defaults(fn=cmd_info)
    sub.add_parser("state", help="battery, storage, capture status").set_defaults(fn=cmd_state)

    g = sub.add_parser("get-options", help="read camera options")
    g.add_argument("names", help="comma list, e.g. captureMode,iso")
    g.add_argument("--support", action="store_true", help="also read <name>Support lists")
    g.set_defaults(fn=cmd_get_options)

    s = sub.add_parser("set-options", help="set options; unsupported ones are refused, not fatal")
    s.add_argument("--mode", choices=["photo", "video"])
    s.add_argument("--exposure-program", type=int, help="0 manual, 1 normal, 2 aperture priority, 4 shutter priority")
    s.add_argument("--iso", type=int)
    s.add_argument("--shutter-speed", type=float, help="seconds, e.g. 0.004")
    s.add_argument("--ev", type=float, help="exposure compensation")
    s.add_argument("--white-balance")
    s.add_argument("--set", action="append", metavar="NAME=VALUE", help="any OSC option")
    s.set_defaults(fn=cmd_set_options)

    t = sub.add_parser("take-photo")
    t.add_argument("--download", metavar="DIR")
    t.set_defaults(fn=cmd_take_photo)
    sub.add_parser("start-video").set_defaults(fn=cmd_start_video)
    sv = sub.add_parser("stop-video")
    sv.add_argument("--download", metavar="DIR")
    sv.set_defaults(fn=cmd_stop_video)

    lf = sub.add_parser("list-files")
    lf.add_argument("--type", default="all", choices=["all", "image", "video"])
    lf.add_argument("--limit", type=int)
    lf.set_defaults(fn=cmd_list_files)

    d = sub.add_parser("download", help="resumable download with SHA-256")
    d.add_argument("url", nargs="*")
    d.add_argument("--latest", action="store_true")
    d.add_argument("--all", action="store_true")
    d.add_argument("--type", default="all", choices=["all", "image", "video"])
    d.add_argument("--out", default=".", metavar="DIR")
    d.set_defaults(fn=cmd_download)

    u = sub.add_parser("upload", help="direct multipart upload (POST /uploads, PUT parts, complete) then POST /projects/{pid}/captures")
    u.add_argument("files", nargs="+", help="equirect JPG/PNG/MP4/MOV or raw .insp/.insv files, or folders (raw files are stitched by the worker)")
    u.add_argument("--project", required=True, help="project id (uuid)")
    u.add_argument("--level-id")
    u.add_argument("--captured-at", help="ISO 8601 UTC; default now")
    u.add_argument("--camera-model", default="insta360_one_x2")
    u.add_argument("--api-url", help="env FAUXEL_API_URL")
    u.add_argument("--api-key", help="env FAUXEL_API_KEY")
    u.add_argument("--cf-client-id", help="Cloudflare Access service token id (env FAUXEL_CF_ACCESS_CLIENT_ID)")
    u.add_argument("--cf-client-secret", help="env FAUXEL_CF_ACCESS_CLIENT_SECRET")
    u.add_argument("--wait", action="store_true", help="poll the ingest job until the worker is done")
    u.add_argument("--attempts", type=int, default=5)
    u.set_defaults(fn=cmd_upload)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except OscError as e:
        print(f"camera error{f' [{e.code}]' if e.code else ''}: {e}", file=sys.stderr)
        return 1
    except requests.ConnectionError:
        print(f"cannot reach {args.host}:{args.port}. Join the camera's Wi-Fi (e.g. 'ONE X2 xxxxxx.OSC') and retry.", file=sys.stderr)
        return 1
    except requests.Timeout:
        print("timed out talking to the camera", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
