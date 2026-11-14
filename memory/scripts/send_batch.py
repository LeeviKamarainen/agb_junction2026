"""Post a folder of JPEG frames to the frames API as one batch and print the JSON response.

    .venv/bin/python scripts/send_batch.py FRAMES [--every 1.0]

The API's address and token are read from the environment (GLASS_API_URL, GLASS_API_TOKEN), so
that the token stays out of the shell history. The frames are sent in file name order; the API
takes 1000 at most in one batch.
"""

import argparse
import os
import sys
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description="Post a folder of JPEG frames as one batch.")
    parser.add_argument(
        "frames", type=Path, help="folder of .jpg files whose names sort in time order"
    )
    parser.add_argument("--every", type=float, default=1.0, help="seconds between frames")
    args = parser.parse_args()
    frames = sorted(args.frames.glob("*.jpg"))
    if len(frames) > 1000:
        sys.exit(f"{len(frames)} frames: the API takes 1000 at most in one batch")
    response = httpx.post(
        os.environ["GLASS_API_URL"].rstrip("/") + "/batches",
        headers={"Authorization": f"Bearer {os.environ['GLASS_API_TOKEN']}"},
        files=[("frames", (path.name, path.read_bytes(), "image/jpeg")) for path in frames],
        data={"every_s": args.every},
        timeout=600,  # EmbeddingGemma 2 on every frame, then Gemma's summary
    )
    if response.is_error:
        sys.exit(f"HTTP {response.status_code}: {response.text}")
    print(response.text)


if __name__ == "__main__":
    main()
