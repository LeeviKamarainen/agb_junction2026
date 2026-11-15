"""Downloads a recorded demo from a running backend into backend/demo-data/<tag>/.

    uv run python scripts/pull_demo.py <tag> --api https://<host>

Logs in as the admin (the password from ADMIN_PASSWORD, else asked for), asks
GET /api/memories/export?collection=<tag> for the zip and unpacks it. Only the standard library,
so any Python 3 does. See demo-data/README.md.
"""

import argparse
import getpass
import io
import json
import os
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent / "demo-data"


def call(request: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(request, timeout=120) as reply:
        return reply.read()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the tag the demo was recorded under")
    parser.add_argument("--api", required=True, help="the backend's address without /api")
    parser.add_argument("--user", default=os.environ.get("ADMIN_USERNAME", "admin"))
    args = parser.parse_args()
    base = args.api.rstrip("/")
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass(f"Password of {args.user}: ")

    login = urllib.request.Request(
        f"{base}/api/auth/login",
        data=json.dumps({"username": args.user, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    token = json.loads(call(login))["token"]
    query = urllib.parse.urlencode({"collection": args.tag})
    export = urllib.request.Request(
        f"{base}/api/memories/export?{query}", headers={"Authorization": f"Bearer {token}"}
    )
    data = call(export)

    target = FOLDER / args.tag.strip().lower()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            (target / Path(name).name).write_bytes(archive.read(name))  # .name: stay in the folder
    print(f"Saved {len(archive.namelist()) - 1} stills to {target}")


if __name__ == "__main__":
    main()
