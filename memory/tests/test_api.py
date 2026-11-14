"""The frames API through FastAPI's TestClient, with the embedding model and Gemma faked and a
temporary store."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from glass_memory import api, ask, embed

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BATCH = "20261114T120000000000"  # a well-formed id that is never stored
PROTECTED = [
    ("POST", "/batches"),
    ("GET", "/batches"),
    ("GET", f"/batches/{BATCH}"),
    ("GET", f"/batches/{BATCH}/map"),
    ("GET", f"/batches/{BATCH}/keyframes/00000.jpg"),
    ("POST", f"/batches/{BATCH}/ask"),
    ("POST", "/v1/embeddings"),
]


def fake_images(paths, batch=32):
    """One unit vector per still: frame i (from its file name) gets axis i; the still must exist."""
    assert all(path.is_file() for path in paths)
    return np.eye(8, dtype=np.float32)[[int(path.stem) for path in paths]]


def fake_text(text):
    """Every question is closest to frame 3, then to frame 1, then to frame 2."""
    return np.array([0, 0.5, 0.2, 1, 0, 0, 0, 0], np.float32)


@pytest.fixture
def gemma(monkeypatch):
    """The calls made to the faked ask.store, ask.summarise and ask.ask, as (name, arguments)
    pairs; the model itself is never loaded."""
    calls = []
    monkeypatch.setattr(ask, "gemma", lambda: calls.append(("gemma", ())))
    monkeypatch.setattr(ask, "store", lambda *args: calls.append(("store", args)))
    # Still i's pooled vectors are (i, 2i), so with the layout faked too it sits at x = i, y = 2i.
    monkeypatch.setattr(
        ask,
        "pooled",
        lambda stills: (
            calls.append(("pooled", (stills,)))
            or np.array(
                [[int(Path(still).stem), 2 * int(Path(still).stem)] for still in stills], np.float32
            )
        ),
    )
    monkeypatch.setattr(
        ask, "summarise", lambda *args: calls.append(("summarise", args)) or "a faked summary"
    )
    monkeypatch.setattr(ask, "ask", lambda *args: calls.append(("ask", args)) or "a faked answer")
    return calls


@pytest.fixture
def client(monkeypatch, tmp_path, gemma):
    monkeypatch.setenv("GLASS_API_TOKEN", TOKEN)
    monkeypatch.delenv("GEMMA_URL", raising=False)
    monkeypatch.setattr(api, "ROOT", tmp_path / "API-1")
    monkeypatch.setattr(embed, "embed_images", fake_images)
    monkeypatch.setattr(embed, "embed_text", fake_text)
    monkeypatch.setattr(
        embed, "encoder", lambda: pytest.fail("the test loaded the embedding model")
    )
    with TestClient(api.app) as client:
        yield client


def jpeg(i):
    """Not a picture, only the two bytes every JPEG starts with and the frame's number."""
    return b"\xff\xd8 frame %d" % i


def post(client, n=4, **form):
    files = [("frames", (f"anything-{i}.jpg", jpeg(i), "image/jpeg")) for i in range(n)]
    return client.post("/batches", files=files, data=form, headers=AUTH)


def test_health_and_the_page_are_open(client):
    assert client.get("/health").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert client.get("/openapi.json", headers=AUTH).status_code == 404


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer " + "x" * 32}, {"Authorization": f"Basic {TOKEN}"}]
)
@pytest.mark.parametrize("method, path", PROTECTED)
def test_every_other_route_needs_the_token(client, method, path, headers):
    assert client.request(method, path, headers=headers).status_code == 401


def test_a_post_without_the_token_stores_nothing(client, gemma):
    files = [("frames", ("0.jpg", jpeg(0), "image/jpeg"))]

    assert client.post("/batches", files=files).status_code == 401
    assert not api.ROOT.exists() and gemma == [("gemma", ())]  # loaded at the start, asked nothing


@pytest.mark.parametrize(
    "path, content_type, body",
    [
        ("/batches", "multipart/form-data", b"no boundary"),
        (f"/batches/{BATCH}/ask", "application/json", b"{not json"),
    ],
)
def test_the_token_is_checked_before_the_body_is_read(client, path, content_type, body):
    """A body that cannot be parsed gets 400 or 422 from the parser, so 401 shows it was not
    reached."""
    headers = {"Content-Type": content_type}

    assert client.post(path, content=body, headers=headers).status_code == 401
    assert client.post(path, content=body, headers=headers | AUTH).status_code in (400, 422)


def test_the_app_refuses_to_start_with_a_short_token(monkeypatch):
    for token in ["t" * 31, None]:
        monkeypatch.setenv("GLASS_API_TOKEN", token) if token else monkeypatch.delenv(
            "GLASS_API_TOKEN"
        )
        with pytest.raises(RuntimeError, match="GLASS_API_TOKEN"), TestClient(api.app):
            pass


def test_a_posted_batch_round_trips_through_every_read_route(client, gemma):
    assert client.get("/batches", headers=AUTH).json() == []

    posted = post(client, every_s=0.5).json()

    batch, names = posted["batch"], [f"{i:05d}.jpg" for i in range(4)]
    assert posted == {"batch": batch, "memories": 4, "summary": "a faked summary"}
    info = client.get(f"/batches/{batch}", headers=AUTH).json()
    assert info == posted | {"created": info["created"]}
    assert datetime.now(UTC) - datetime.fromisoformat(info["created"]) < timedelta(minutes=1)
    assert client.get("/batches", headers=AUTH).json() == [info]
    assert client.get(f"/batches/{batch}/map", headers=AUTH).json() == [
        {"t_s": 0.5 * i, "x": float(i), "y": 0.0, "keyframe": name} for i, name in enumerate(names)
    ]
    for i, name in enumerate(names):
        still = client.get(f"/batches/{batch}/keyframes/{name}", headers=AUTH)
        assert still.content == jpeg(i) and still.headers["content-type"] == "image/jpeg"
    # On disk: the layout embed.load reads, plus the summary Gemma was asked for with every still.
    folder = api.ROOT / batch
    index, embeddings = embed.load(folder)
    assert index["keyframe"].to_list() == [str(folder / "keyframes" / name) for name in names]
    assert np.array_equal(embeddings, np.eye(8, dtype=np.float32)[:4])
    assert (folder / "summary.txt").read_text() == "a faked summary"
    # Gemma is loaded when the app starts, then stores every still's vectors before it summarises.
    stills = [folder / "keyframes" / name for name in names]
    assert gemma == [
        ("gemma", ()),
        ("store", (stills,)),
        ("summarise", (stills, [0.0, 0.5, 1.0, 1.5])),
    ]


def test_batches_are_listed_newest_first(client):
    first, second = post(client, 1).json()["batch"], post(client, 2).json()["batch"]

    listed = client.get("/batches", headers=AUTH).json()
    assert [(b["batch"], b["memories"]) for b in listed] == [(second, 2), (first, 1)]
    assert listed[0]["created"] > listed[1]["created"]


def test_ask_answers_from_the_stills_the_embeddings_retrieve(client, gemma):
    batch = post(client, every_s=2).json()["batch"]

    response = client.post(
        f"/batches/{batch}/ask", json={"question": "where?", "k": 3}, headers=AUTH
    )

    # The three closest of the four, sent and named in time order, not in order of closeness.
    names = ["00001.jpg", "00002.jpg", "00003.jpg"]
    assert response.json() == {"answer": "a faked answer", "keyframes": names}
    stills = [api.ROOT / batch / "keyframes" / name for name in names]
    assert gemma[-1] == (
        "ask",
        ("where?", [str(still) for still in stills], [2.0, 4.0, 6.0], False),
    )
    # The same memories, with Gemma told to read its stored vectors instead of the stills.
    client.post(
        f"/batches/{batch}/ask", json={"question": "where?", "k": 3, "vectors": True}, headers=AUTH
    )
    assert gemma[-1] == ("ask", ("where?", [str(still) for still in stills], [2.0, 4.0, 6.0], True))
    assert all(still.is_file() for still in stills)


def test_the_map_of_gemmas_vectors_is_laid_out_once_and_kept(client, gemma, monkeypatch):
    batch = post(client).json()["batch"]
    monkeypatch.setattr(embed, "layout", lambda rows: rows * 10)

    def vector_map():
        return client.get(f"/batches/{batch}/map", params={"vectors": True}, headers=AUTH).json()

    embedded = client.get(f"/batches/{batch}/map", headers=AUTH).json()
    assert (
        vector_map()
        == [m | {"x": 10.0 * i, "y": 20.0 * i} for i, m in enumerate(embedded)]
        == vector_map()
    )
    stills = [str(api.ROOT / batch / "keyframes" / m["keyframe"]) for m in embedded]
    assert [call for call in gemma if call[0] == "pooled"] == [("pooled", (stills,))]
    assert client.get(f"/batches/{batch}/map", headers=AUTH).json() == embedded


def test_vectors_are_refused_when_an_api_serves_gemma(client, monkeypatch):
    batch = post(client).json()["batch"]
    monkeypatch.setenv("GEMMA_URL", "http://gemma.test")

    def asked(**body):
        return client.post(
            f"/batches/{batch}/ask", json={"question": "where?", **body}, headers=AUTH
        )

    assert asked(vectors=True).status_code == 400 and asked().status_code == 200
    assert (
        client.get(f"/batches/{batch}/map", params={"vectors": True}, headers=AUTH).status_code
        == 400
    )
    assert client.get(f"/batches/{batch}/map", headers=AUTH).status_code == 200


def test_ask_takes_four_memories_by_default_and_never_more_than_there_are(client):
    batch = post(client, 3).json()["batch"]

    def asked(**body):
        return client.post(
            f"/batches/{batch}/ask", json={"question": "where?", **body}, headers=AUTH
        )

    assert api.Question(question="where?").k == 4
    assert asked(k=1).json()["keyframes"] == ["00001.jpg"]  # the closest: frame 3 was not posted
    assert asked().json()["keyframes"] == ["00000.jpg", "00001.jpg", "00002.jpg"]
    assert asked(k=0).status_code == 422 and asked(question=None).status_code == 422
    assert asked(k=48).status_code == 200 and asked(k=49).status_code == 422


def test_path_traversal_is_refused(client):
    batch = post(client).json()["batch"]
    # What a batch folder holds, one level up: reachable if ".." were accepted as a batch.
    for path in api.ROOT.parent / "index.parquet", api.ROOT.parent / "keyframes" / "00000.jpg":
        path.parent.mkdir(exist_ok=True)
        path.write_bytes((api.ROOT / batch / path.relative_to(api.ROOT.parent)).read_bytes())
    # The same in a folder beside the batches whose name is not a batch id.
    (api.ROOT / batch).copy(other := api.ROOT / "other")
    assert client.get(f"/batches/{batch}/map", headers=AUTH).status_code == 200

    for path in [
        "/batches/%2E%2E",
        "/batches/%2E%2E/map",
        "/batches/%2E%2E/keyframes/00000.jpg",
        "/batches/%2E/map",
        f"/batches/{other.name}/map",
        f"/batches/{batch}%2F..%2F{batch}/map",
        f"/batches/{batch}/keyframes/%2E%2E",
        f"/batches/{batch}/keyframes/%2E%2E%2Fsummary.txt",
        f"/batches/{batch}/keyframes/%2E%2E%2Fkeyframes%2F00000.jpg",
        f"/batches/{batch}/keyframes/..%5Csummary.txt",
    ]:
        assert client.get(path, headers=AUTH).status_code == 404, path
    assert (
        client.post("/batches/%2E%2E/ask", json={"question": "where?"}, headers=AUTH).status_code
        == 404
    )
    # The checks themselves, in case a server ever lets a "/" through to them.
    with pytest.raises(HTTPException):
        api._folder(f"{batch}/../{batch}")
    with pytest.raises(HTTPException):
        api.keyframe(api.ROOT / batch, "../../../keyframes/00000.jpg")


def test_a_batch_is_stored_with_an_empty_summary_when_gemma_fails(client, monkeypatch):
    def fail(*args):
        raise OSError("Gemma is down")

    monkeypatch.setattr(ask, "summarise", fail)

    posted = post(client).json()

    assert posted == {"batch": posted["batch"], "memories": 4, "summary": ""}
    assert client.get(f"/batches/{posted['batch']}", headers=AUTH).json()["summary"] == ""
    assert len(client.get(f"/batches/{posted['batch']}/map", headers=AUTH).json()) == 4


def test_a_batch_without_frames_or_with_a_frame_that_is_no_jpeg_is_refused(client):
    assert client.post("/batches", data={"every_s": 1}, headers=AUTH).status_code == 422
    assert post(client, every_s=0).status_code == 422
    assert (
        post(client, every_s="inf").status_code == 422
        and post(client, every_s="nan").status_code == 422
    )
    assert post(client, 1001).status_code == 400  # 1000 frames at most
    png = [
        ("frames", ("0.jpg", jpeg(0), "image/jpeg")),
        ("frames", ("1.png", b"\x89PNG", "image/png")),
    ]
    assert client.post("/batches", files=png, headers=AUTH).status_code == 415
    assert client.get("/batches", headers=AUTH).json() == []


def test_text_embeddings_come_back_one_per_input_in_order(client):
    response = client.post(
        "/v1/embeddings", json={"model": embed.MODEL, "input": ["keys", "a hand"]}, headers=AUTH
    )

    data = response.json()["data"]
    assert [row["index"] for row in data] == [0, 1]
    assert data[0]["embedding"] == fake_text("keys").tolist()
    assert (
        client.post("/v1/embeddings", json={"input": "keys"}, headers=AUTH).json()["model"]
        == embed.MODEL
    )
    assert (
        client.post(
            "/v1/embeddings", json={"model": "other", "input": ["x"]}, headers=AUTH
        ).status_code
        == 400
    )
    assert client.post("/v1/embeddings", json={"input": []}, headers=AUTH).status_code == 422
