"""Send a local image to POST /api/analyze and print the result.

Usage:
    python test_analyze.py path/to/photo.jpg [--zone chem-lab] [--url http://localhost:8000]
Server must be running (uvicorn main:app). Works in MOCK_MODE or real mode.
"""
import argparse
import base64
import json
import sys
import urllib.error
import urllib.request

import config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--zone", default="chem-lab")
    ap.add_argument("--url", default="http://localhost:8000")
    args = ap.parse_args()

    with open(args.image, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    body = json.dumps({"image": b64, "zone_id": args.zone}).encode()
    req = urllib.request.Request(
        args.url + "/api/analyze", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach server at {args.url}: {e.reason}")

    print(f"Zone: {data['zone_name']}  required: {', '.join(data['required'])}"
          + ("   [MOCK]" if data.get("mock") else ""))
    print(f"Persons detected: {len(data['persons'])}")
    for p in data["persons"]:
        b = p["box"]
        label = lambda gs: ", ".join(config.GEAR_LABELS[g] for g in gs) or "-"
        print(f"  Person {p['id']}: {p['status'].upper():9}  missing: {label(p['missing'])}  "
              f"not visible: {label(p['unknown'])}  "
              f"box(l={b['left']:.2f} t={b['top']:.2f} w={b['width']:.2f} h={b['height']:.2f})")
    for g in data["missing_gear"]:
        r = config.OSHA_RULES[g]
        print(f"  ! {config.GEAR_LABELS[g]} missing -> {r['rule']} {r['title']}, "
              f"potential fine exposure ${r['fine']:,}")


if __name__ == "__main__":
    main()
