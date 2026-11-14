"""scripts/send_batch.py with httpx.post faked: no request leaves the test."""

import runpy
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "send_batch.py"


def test_more_than_1000_frames_are_refused_before_anything_is_sent(monkeypatch, tmp_path):
    for i in range(1001):
        (tmp_path / f"{i:05d}.jpg").touch()
    monkeypatch.setattr(
        httpx, "post", lambda *args, **kwargs: pytest.fail("the test sent a request")
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), str(tmp_path)])

    with pytest.raises(SystemExit, match="1001 frames"):
        runpy.run_path(str(SCRIPT), run_name="__main__")
