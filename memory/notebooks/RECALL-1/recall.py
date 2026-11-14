import marimo

__generated_with = "0.25.2"
app = marimo.App(width="full")


@app.cell
def _():
    import ast
    import base64
    import json
    import os
    from pathlib import Path

    import altair as alt
    import httpx
    import marimo as mo
    import numpy as np
    import polars as pl
    from dotenv import dotenv_values

    return Path, alt, ast, base64, dotenv_values, httpx, json, mo, np, os, pl


@app.cell
def _(mo):
    mo.md(r"""
    # RECALL-1: does a still find the earlier time the wearer saw the same thing?

    "Where do I know this from?" would embed the current still and look up the nearest earlier
    stills. Here every still of a recorded video is the question in turn, and the stills at least
    a gap before it are the memory. It reads the stills and the embeddings that TRIG-1 kept in
    `res/items/TRIG-1/<video>/`. One table is built once below, one row per still; each block
    reads it.
    """)
    return


@app.cell
def _(mo):
    gap = mo.ui.slider(
        steps=[5, 10, 20, 30, 45, 60], value=30, label="Gap: a memory is at least this many seconds old"
    )
    gap
    return (gap,)


@app.cell
def _(Path, gap, mo, np, pl):
    _dirs = sorted(p.parent for p in Path("res/items/TRIG-1").glob("*/embeddings.npy"))
    mo.stop(not _dirs, mo.md("No embeddings yet: open TRIG-1 once for each video."))
    # Per video: its stills, a still's file name being its second, and their L2-normalised vectors.
    videos = {d.name: (sorted(d.glob("*.jpg")), np.load(d / "embeddings.npy")) for d in _dirs}


    def earlier(video, i, most=3):
        """The stills of the video most like its still i among those at least the gap before it,
        best first, as (index, similarity)."""
        stills, vectors = videos[video]
        seconds = np.array([float(p.stem) for p in stills])
        old = np.flatnonzero(seconds <= seconds[i] - gap.value)
        similarity = vectors[old] @ vectors[i]
        return [(int(old[j]), float(similarity[j])) for j in np.argsort(-similarity)[:most]]


    def load():
        """One row per still that has a memory to look in: its best earlier still, and for
        comparison its best still in the other videos, where the wearer has not been."""
        rows = []
        for video, (stills, vectors) in videos.items():
            elsewhere = [v for name, (_, v) in videos.items() if name != video]
            for i, still in enumerate(stills):
                best = earlier(video, i, 1)
                if not best:
                    continue
                rows.append(
                    {
                        "video": video,
                        "i": i,
                        "t_s": float(still.stem),
                        "keyframe": str(still),
                        "match_t_s": float(stills[best[0][0]].stem),
                        "earlier": best[0][1],
                        "elsewhere": max((float((v @ vectors[i]).max()) for v in elsewhere), default=None),
                    }
                )
        return pl.DataFrame(rows)


    data = load()
    # sanity table: one row per video
    data.group_by("video", maintain_order=True).agg(
        pl.len().alias("stills"),
        pl.col("earlier").quantile(0.1).alias("earlier_q10"),
        pl.col("earlier").median().alias("earlier_median"),
        pl.col("earlier").max().alias("earlier_max"),
        pl.col("elsewhere").median().alias("elsewhere_median"),
        pl.col("elsewhere").max().alias("elsewhere_max"),
    )
    return data, earlier, videos


@app.cell
def _(mo):
    mo.md(r"""
    **Block 1: the best earlier still, over the video.**

    - Computed: cosine similarity of each still to its nearest still at least the gap before it
      (*earlier*), and to its nearest still in the other videos (*elsewhere*), which is what a
      place never visited gives
    - Population: every still with at least one still the gap before it, one per second
    - Aggregation: none; under the chart, the chosen still and its three best earlier stills
    """)
    return


@app.cell
def _(alt, data):
    alt.Chart(data.unpivot(["earlier", "elsewhere"], index=["video", "t_s", "match_t_s"])).mark_line(
        point=True
    ).encode(
        x=alt.X("t_s:Q", title="seconds into video"),
        y=alt.Y("value:Q", title="similarity to the best still").scale(zero=False),
        color=alt.Color("variable:N", title="memory"),
        tooltip=[
            alt.Tooltip("t_s:Q", format=".0f"),
            alt.Tooltip("value:Q", format=".3f"),
            alt.Tooltip("match_t_s:Q", format=".0f", title="earlier still at"),
        ],
    ).properties(width="container", height=220).facet(row=alt.Row("video:N", title=None)).resolve_scale(
        x="independent"
    )
    return


@app.cell
def _(data, mo):
    video = mo.ui.dropdown(data["video"].unique(maintain_order=True).to_list(), label="Video")
    video
    return (video,)


@app.cell
def _(data, mo, pl, video):
    _of_video = data.filter(pl.col("video") == (video.value or data["video"][0]))
    still = mo.ui.slider(
        steps=_of_video["i"].to_list(), value=_of_video["i"][0], label="Still, seconds", full_width=True
    )
    still
    return (still,)


@app.cell
def _(data, earlier, mo, still, video, videos):
    _name = video.value or data["video"][0]
    _stills = videos[_name][0]
    mo.hstack(
        [mo.image(src=str(_stills[still.value]), width=260, caption=f"asked at {_stills[still.value].stem} s")]
        + [
            mo.image(src=str(_stills[_i]), width=260, caption=f"{_stills[_i].stem} s, {_similarity:.3f}")
            for _i, _similarity in earlier(_name, still.value)
        ],
        justify="start",
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    **Block 2: does Gemma find the nearest stills the same thing?**

    - Computed: Gemma is shown a still and its three best earlier stills, with the prompt the
      glasses use (`SYSTEM` in `backend/app/glasses/dejavu.py`, read from that file), and answers
      which of them show the very same thing and what to say. Answers are kept in
      `res/items/RECALL-1/verdicts.json`, so a still is only asked about once per gap
    - Population: every n-th still of each video that has a memory to look in
    - Aggregation: none; a point is one still, at its similarity to its best earlier still,
      coloured by whether Gemma found any of the three the same. The table holds what it said
    """)
    return


@app.cell
def _(mo, os):
    token = mo.ui.text(
        os.environ.get("GEMMA_API_KEY", ""),
        kind="password",
        # Caddy on VM2 sends /v1/chat/* straight to vLLM, which takes its own key, not the API's.
        label="vLLM key (`GEMMA_API_KEY` in VM2's `/opt/glass-memory/.env`)",
    )
    every = mo.ui.slider(steps=[5, 10, 20], value=10, label="Ask about every n-th still")
    ask = mo.ui.run_button(label="Ask Gemma about the stills not asked yet")
    mo.hstack([token, every, ask], justify="start", gap=2)
    return ask, every, token


@app.cell
def _(
    Path,
    ask,
    ast,
    base64,
    data,
    dotenv_values,
    earlier,
    every,
    gap,
    httpx,
    json,
    mo,
    os,
    pl,
    token,
    videos,
):
    # The prompt the glasses use, read from the backend's source: it is not installed here.
    SYSTEM = next(
        ast.literal_eval(_node.value)
        for _node in ast.parse(Path("../backend/app/glasses/dejavu.py").read_text()).body
        if isinstance(_node, ast.Assign) and _node.targets[0].id == "SYSTEM"
    )
    _kept = Path("res/items/RECALL-1/verdicts.json")
    _answers = json.loads(_kept.read_text()) if _kept.exists() else {}


    def _image(still):
        _url = "data:image/jpeg;base64," + base64.b64encode(still.read_bytes()).decode()
        return {"type": "image_url", "image_url": {"url": _url}}


    def _ago(seconds):
        return f"{seconds:.0f} seconds ago" if seconds < 60 else f"{seconds // 60:.0f} minutes ago"


    def asked(video, i):
        """Gemma's answer about still i of the video and its best earlier stills, as the glasses
        would read it: `same` is None when the answer was not the JSON asked for."""
        stills = videos[video][0]
        content = [{"type": "text", "text": "The current still:"}, _image(stills[i])]
        for number, (j, _) in enumerate(earlier(video, i), 1):
            when = _ago(float(stills[i].stem) - float(stills[j].stem))
            content += [{"type": "text", "text": f"Earlier still {number}, seen {when}:"}]
            content += [_image(stills[j])]
        url = os.environ.get("GLASS_API_URL") or "https://{}.sslip.io".format(
            dotenv_values(".env")["VM2_IP"].replace(".", "-")
        )
        response = httpx.post(
            url.rstrip("/") + "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token.value}"},
            json={
                "model": os.environ.get("GEMMA_MODEL", "google/gemma-4-31B-it"),
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": content},
                ],
                # As the backend asks (backend/app/llm.py).
                "max_tokens": 800,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=300,
        )
        response.raise_for_status()
        text = (response.json()["choices"][0]["message"].get("content") or "").strip()
        try:
            answer = json.loads(text[text.find("{") : text.rfind("}") + 1])
            return {"same": [n for n in answer["same"] if isinstance(n, int)], "say": answer["say"]}
        except (json.JSONDecodeError, KeyError, TypeError):
            return {"same": None, "say": text}


    _sample = data.filter(pl.col("i") % every.value == 0)
    # In script mode there is no button: ask whenever there is a token.
    if token.value and (ask.value or not mo.running_in_notebook()):
        for _video, _i in _sample.select("video", "i").iter_rows():
            _key = f"{_video}/{_i}/{gap.value}"
            if _key not in _answers:
                _answers[_key] = asked(_video, _i)
                _kept.parent.mkdir(parents=True, exist_ok=True)
                _kept.write_text(json.dumps(_answers, indent=1))

    _rows = [
        {
            "video": _row["video"],
            "t_s": _row["t_s"],
            "earlier": _row["earlier"],
            "verdict": "unreadable"
            if _answer["same"] is None
            else "same thing"
            if _answer["same"]
            else "not the same",
            "same": ", ".join(
                f"{videos[_row['video']][0][earlier(_row['video'], _row['i'])[_n - 1][0]].stem} s"
                for _n in _answer["same"] or []
                if 1 <= _n <= len(earlier(_row["video"], _row["i"]))
            ),
            "say": _answer["say"],
        }
        for _row in _sample.iter_rows(named=True)
        if (_answer := _answers.get(f"{_row['video']}/{_row['i']}/{gap.value}"))
    ]
    mo.stop(not _rows, mo.md("Nothing asked yet at this gap: paste the key and press the button."))
    verdicts = pl.DataFrame(_rows)
    verdicts
    return (verdicts,)


@app.cell
def _(alt, verdicts):
    alt.Chart(verdicts).mark_circle(size=90).encode(
        x=alt.X("t_s:Q", title="seconds into video"),
        y=alt.Y("earlier:Q", title="similarity to the best earlier still").scale(zero=False),
        color=alt.Color("verdict:N", title="Gemma"),
        tooltip=[alt.Tooltip("t_s:Q", format=".0f"), alt.Tooltip("earlier:Q", format=".3f"), "same", "say"],
    ).properties(width="container", height=200).facet(row=alt.Row("video:N", title=None)).resolve_scale(
        x="independent"
    )
    return


if __name__ == "__main__":
    app.run()
