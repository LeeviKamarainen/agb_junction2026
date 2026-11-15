"""Uploaded playback media, separate from private memories. Files survive database rebuilds,
so random file names prevent a reused demo id from inheriting earlier uploads.

The demos themselves survive a rebuild too, unlike the rest of the database
(docs/adr/0001-disposable-database.md): every change is also written to a manifest beside the
database file, and `restore` reads it back at startup when the table came up empty. Curating a
demo is hand work that a model change elsewhere should not undo.

A video is never played as uploaded. It is stored as it came, and a background worker
(`worker`) re-encodes it in place, under the same name, with every face blurred frame by frame
by the detector and blur every image gets (app/faces.py), as an MP4 that starts playing at
once in a browser. That takes about as long as the video, so until it is done the video cannot
be played or published. Videos stored before this are re-encoded the same way.
"""

import asyncio
import io
import json
import logging
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import IO

import anyio
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import faces
from app.models import Showcase
from app.settings import get_settings

log = logging.getLogger(__name__)

MAX_UPLOAD = 200 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024
MAX_ASSETS = 100

# How every stored video is re-encoded before it may be played. A demo clip shows bystanders,
# whose faces must not be kept; and phones write a video's index (moov) after the video and
# record at 3-5 Mbit/s, so a browser fetched most of a 40 MB file before its first frame.
WEB_MARKER = "junction2026 web-ready"  # the comment tag of every video re-encoded here
# The frames reach the encoder raw, without their times, so at one steady rate. Our clips are
# phone and glasses recordings at about 30 fps (phones vary it a little); a faster video is
# thinned to this, which also bounds the faces searched per second of video.
RATE = 30
DECODE = [  # ffmpeg's output options, from the upload to the frames whose faces are blurred
    "-map",
    "0:v:0",
    # start_time=0 keeps the frames in step with the sound, which is timed from 0 too. At most
    # 1280 px on the long side: the view shows a clip at most about 600 px tall, 1200 pixels on
    # a 2x screen. Never enlarged; even sides, as yuv420p needs.
    "-vf",
    f"fps={RATE}:start_time=0,scale='min(1280,iw)':'min(1280,ih)'"
    ":force_original_aspect_ratio=decrease:force_divisible_by=2",
    # Each frame as an uncompressed RGB image with its size (PPM), so nothing is lost before the
    # encode.
    *("-f", "image2pipe", "-c:v", "ppm", "pipe:1"),
]
ENCODE = [  # ffmpeg's output options, from the blurred frames (input 0) and the upload (input 1)
    *("-map", "0:v:0", "-map", "1:a:0?"),  # the upload's sound, if it has any
    # H.264 in yuv420p plays in every browser. CRF 26 at the veryfast preset, measured on a 62 s
    # 958x1280 phone clip: 42 MB (5.4 Mbit/s) became 14.4 MB (1.9 Mbit/s).
    *("-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p"),
    # Converted to the colours of HD video, BT.709, and labelled so: unlabelled, players guess.
    *("-vf", "scale=out_color_matrix=bt709:out_range=tv"),
    *("-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"),
    *("-color_range", "tv"),
    *("-c:a", "aac", "-b:a", "96k"),  # the clips' sound is speech and surroundings
    *("-metadata", f"comment={WEB_MARKER}"),
    *("-movflags", "+faststart"),  # the index first, so playing starts with the first bytes
]
READY = "ready"

# Where each stored video stands since the start, by name: READY once re-encoded here, or why
# it could not be. A video missing here waits for the worker or is being re-encoded.
_states: dict[str, str] = {}
# Made by the running worker (`worker`), so it belongs to the app's event loop.
_wanted: asyncio.Event | None = None
# One re-encode at a time, in case a convert_stored is called while the worker runs one.
_one_at_a_time = threading.Lock()


class ConversionError(Exception):
    """ffmpeg could not re-encode a video, or ffprobe read it; the message is what it said."""


def folder() -> Path:
    root = get_settings().database_path.parent / "showcase-media"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save(data: bytes, kind: str) -> tuple[str, str]:
    """Store an uploaded video or image; its file name and content type. Raises ValueError for a
    file that is not one, and ConversionError for a video in which ffprobe finds no picture. A
    video is stored as it came, under the name it keeps, for the worker to re-encode (and as an
    MP4, which it is then)."""
    if kind == "video":
        form = container(data)
        name = f"{uuid.uuid4().hex}.mp4"
        draft = folder() / f".{name}.part"
        try:
            draft.write_bytes(data)
            # Looked at now, so that a broken upload is refused at once, not in the background.
            if "video" not in (stream["codec_type"] for stream in _probe(draft, form)["streams"]):
                raise ConversionError("There is no picture in it")
            draft.replace(folder() / name)
        finally:
            draft.unlink(missing_ok=True)
        return name, "video/mp4"
    if len(data) > MAX_IMAGE:
        raise ValueError("Each image must be at most 15 MB")
    try:
        with Image.open(io.BytesIO(faces.anonymise(data))) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((1600, 1600))
            name = f"{uuid.uuid4().hex}.jpg"
            image.save(folder() / name, "JPEG", quality=90)
        return name, "image/jpeg"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Upload a readable image") from error


def container(head: bytes) -> str:
    """A video's container by its first 16 bytes, as ffmpeg names it. ffmpeg is always told it,
    never left to guess: some formats it could guess, such as playlists, open other files.
    Raises ValueError for neither an MP4 nor a WebM."""
    if len(head) >= 16 and head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "webm"
    raise ValueError("Upload an MP4 or WebM video")


def encode(source: Path, target: Path, form: str) -> int | None:
    """Re-encode the video `source`, in container `form`, at `target` as described above, with
    every face blurred: one ffmpeg decodes it into frames (DECODE), each frame has its faces
    blurred as an image's are, and a second ffmpeg encodes them with the source's sound
    (ENCODE). Written beside `target` and moved into its place in one step, so `target` is never
    half a file, even when it is `source`; the size written. Re-encoding in place, it writes
    nothing (None) when the file was deleted meanwhile: its clip was, or got another video.
    Raises ConversionError when ffmpeg fails, leaving `target` as it was."""
    draft = target.with_name(f".{target.name}.part")
    # Each ffmpeg writes its complaints to a file: a full pipe would stall it.
    with tempfile.TemporaryFile() as decoding, tempfile.TemporaryFile() as encoding:
        decoder = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", form, "-i", str(source), *DECODE],
            stdout=subprocess.PIPE,
            stderr=decoding,
        )
        assert decoder.stdout is not None
        encoder = None
        try:
            seen = blurred = b""
            for width, height, frame in _frames(decoder.stdout):
                if encoder is None:
                    raw = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}"]
                    encoder = subprocess.Popen(
                        ["ffmpeg", "-nostdin", "-v", "error", "-y", *raw, "-framerate", str(RATE)]
                        + ["-i", "pipe:0", "-f", form, "-i", str(source), *ENCODE]
                        + ["-f", "mp4", str(draft)],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=encoding,
                    )
                    assert encoder.stdin is not None
                if frame != seen:  # fps repeats a frame to keep its rate: search each one once
                    seen, blurred = frame, _blurred(width, height, frame)
                encoder.stdin.write(blurred)
            if decoder.wait() != 0:
                raise ConversionError(_complaint(decoding, decoder.returncode))
            if encoder is None:
                raise ConversionError("There are no frames in it")
            encoder.stdin.close()
            if encoder.wait() != 0:
                raise ConversionError(_complaint(encoding, encoder.returncode))
            if source == target and not target.exists():
                return None  # putting it back would leave a file that no clip has
            size = draft.stat().st_size
            draft.replace(target)
            return size
        except BrokenPipeError:  # the encoder quit before it took every frame
            assert encoder is not None
            raise ConversionError(_complaint(encoding, encoder.wait())) from None
        finally:
            decoder.stdout.close()
            for process in (decoder, encoder):
                if process is not None and process.poll() is None:
                    process.kill()  # still running only when something above failed
                    process.wait()
            draft.unlink(missing_ok=True)


def _frames(stream: IO[bytes]) -> Iterator[tuple[int, int, bytes]]:
    """The frames ffmpeg writes as PPM images, one after another: width, height and RGB bytes."""
    while header := stream.readline():
        if header != b"P6\n":
            raise ConversionError(f"ffmpeg sent {header[:20]!r} where a frame should start")
        width, height = map(int, stream.readline().split())
        stream.readline()  # the largest value of a colour: 255
        frame = stream.read(width * height * 3)
        if len(frame) < width * height * 3:
            return  # ffmpeg stopped within the frame; its exit status says why
        yield width, height, frame


def _blurred(width: int, height: int, frame: bytes) -> bytes:
    """A frame with every face blurred, as faces.anonymise blurs an image's (without its JPEG)."""
    image = Image.frombytes("RGB", (width, height), frame)
    found = faces.detect(image)
    return faces.cover(image, found).tobytes() if found else frame


def _probe(path: Path, form: str) -> dict:
    """ffprobe's account of a video: its streams' types and its comment tag. Raises
    ConversionError when ffprobe cannot read it."""
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-f", form, "-of", "json"]
        + ["-show_entries", "stream=codec_type:format_tags=comment", str(path)],
        capture_output=True,
    )
    if done.returncode != 0:
        raise ConversionError(_said(done.stderr, done.returncode))
    return json.loads(done.stdout)


def web_ready(path: Path) -> bool:
    """Whether a stored video was re-encoded here: it carries WEB_MARKER."""
    tags = _probe(path, _container_of(path)).get("format", {}).get("tags", {})
    return tags.get("comment") == WEB_MARKER


def _container_of(path: Path) -> str:
    with path.open("rb") as file:
        head = file.read(16)
    try:
        return container(head)
    except ValueError as error:
        raise ConversionError(str(error)) from error


def _complaint(file: IO[bytes], status: int) -> str:
    file.seek(0)
    return _said(file.read(), status)


def _said(stderr: bytes, status: int) -> str:
    """What ffmpeg or ffprobe said when it failed: its first line, which names the cause (the
    lines after it repeat it in general terms)."""
    lines = stderr.decode(errors="replace").strip().splitlines()
    return lines[0] if lines else f"ffmpeg stopped with status {status}"


def request_conversion() -> None:
    """Ask the worker to re-encode soon: a video was uploaded."""
    if _wanted is not None:
        _wanted.set()


async def worker(retry_s: float) -> None:
    """Runs `convert_stored` now and again whenever `request_conversion` is called; after an
    unexpected failure (no ffmpeg, a full disk) again in `retry_s` seconds."""
    global _wanted
    _states.clear()  # what an earlier app in this process found
    _wanted = asyncio.Event()
    _wanted.set()
    try:
        while True:
            await _wanted.wait()
            _wanted.clear()
            try:
                await convert_stored()
            except Exception:
                log.exception("Re-encoding the demo videos failed; trying again in %d s", retry_s)
                await asyncio.sleep(retry_s)
                _wanted.set()
    finally:
        _wanted = None


def playable(name: str) -> bool:
    """Whether a stored video may be played: re-encoded here, with its faces blurred."""
    return _states.get(name) == READY


def failure(name: str) -> str | None:
    """Why a stored video could not be re-encoded, if it could not; it is never played then."""
    state = _states.get(name)
    return None if state in (None, READY) else state


async def convert_stored() -> list[str]:
    """Re-encode, in place and under its name, every stored video not re-encoded here yet: an
    upload, or a video stored before uploads were. Returns the names re-encoded. The names stay,
    so the clips' demo memories, which are keyed by them (demo.source), stay too. Each video is
    first only checked, so that those done already are played at once after a start."""
    waiting = []
    for path in await anyio.to_thread.run_sync(_videos):
        if path.name in _states:
            continue  # re-encoded, or failed, since the start
        try:
            if await anyio.to_thread.run_sync(web_ready, path):
                _states[path.name] = READY
                continue
        except FileNotFoundError:
            if path.exists():
                raise  # not the video but ffprobe is missing
            continue  # deleted meanwhile: its clip was, or got another video
        except ConversionError as error:
            _fail(path, error)
            continue
        waiting.append(path)
    converted = []
    for path in waiting:
        started = time.monotonic()
        try:
            sizes = await anyio.to_thread.run_sync(_convert, path)
        except FileNotFoundError:
            if path.exists():
                raise  # not the video but ffmpeg is missing
            continue
        except ConversionError as error:
            _fail(path, error)
            continue
        _states[path.name] = READY
        if sizes is not None:
            log.warning(
                "Re-encoded the demo video %s with its faces blurred in %.0f s: %.1f MB, was"
                " %.1f MB",
                path.name,
                time.monotonic() - started,
                sizes[1] / 1e6,
                sizes[0] / 1e6,
            )
            converted.append(path.name)
    return converted


def _videos() -> list[Path]:
    """Every stored video. An upload is stored as .mp4 whatever it is, until re-encoded; a .webm
    was stored before videos were re-encoded."""
    return sorted([*folder().glob("*.mp4"), *folder().glob("*.webm")])


def _convert(path: Path) -> tuple[int, int] | None:
    """Re-encode a stored video in place unless it was re-encoded here already: its sizes before
    and after."""
    if path.suffix != ".mp4":
        # As an MP4 it would need another name, and its clip new demo memories with it.
        raise ConversionError(
            "It was stored as WebM before videos were re-encoded: upload it again"
        )
    with _one_at_a_time:
        if web_ready(path):
            return None
        before = path.stat().st_size
        after = encode(path, path, _container_of(path))
        return None if after is None else (before, after)


def _fail(path: Path, error: ConversionError) -> None:
    if not path.exists():
        return  # deleted while it was looked at: no clip has it now
    _states[path.name] = str(error)
    log.error("The demo video %s cannot be played: %s", path.name, error)


def assets(item) -> list[dict]:
    """A Showcase's uploaded media, in order: name, label and content_type each."""
    return json.loads(item.assets_json)


def delete(names: list[str]) -> None:
    for name in names:
        (folder() / Path(name).name).unlink(missing_ok=True)


def manifest() -> Path:
    return get_settings().database_path.parent / "showcases.json"


def _write(text: str) -> None:
    draft = manifest().with_suffix(".tmp")
    draft.write_text(text)
    draft.replace(manifest())  # in one step, so a crash never leaves half a manifest


async def commit(session: AsyncSession) -> None:
    """Commit a change to the demos and write all of them to the manifest."""
    await session.commit()
    rows = [row.model_dump() for row in await session.exec(select(Showcase))]
    await anyio.to_thread.run_sync(_write, json.dumps(rows))


async def restore(session: AsyncSession) -> int:
    """At startup: bring the demos back from the manifest when the database was rebuilt, and
    return how many. With demos and no manifest yet (the first start with this code), write
    one."""
    if (await session.exec(select(Showcase))).first() is not None:
        if not manifest().is_file():
            await commit(session)
        return 0
    if not manifest().is_file():
        return 0
    try:
        rows = json.loads(manifest().read_text())
    except (OSError, json.JSONDecodeError):
        log.exception("The demo manifest %s cannot be read", manifest())
        return 0
    restored = 0
    for row in rows if isinstance(rows, list) else []:
        try:
            session.add(Showcase.model_validate(row))
            restored += 1
        except ValidationError:
            log.warning("Skipping a demo the manifest no longer describes: %r", row)
    await session.commit()
    if restored:
        log.warning("Restored %d demo(s) from %s after a database rebuild", restored, manifest())
    return restored
