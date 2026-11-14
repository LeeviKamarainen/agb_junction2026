import marimo

__generated_with = "0.25.2"
app = marimo.App(width="full")


@app.cell
def _():
    import base64
    import io
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import polars as pl
    import umap
    from PIL import Image

    from glass_memory import ask, embed

    return Image, Path, alt, ask, base64, embed, io, mo, pl, umap


@app.cell
def _(mo):
    mo.md(r"""
    # Memory map

    Each point is one memory: a keyframe of the video, placed by what it shows. Hover for the
    still. A question lights up the memories closest to it; drag over points to pick your own.
    Gemma answers from the stills shown under the map.
    """)
    return


@app.cell
def _(Path, mo):
    _dirs = sorted(str(p.parent) for p in Path("res/items/EMB-1").glob("*/index.parquet"))
    memory_dir = mo.ui.dropdown(_dirs, value=_dirs[0] if _dirs else None, label="Video")
    memory_dir
    return (memory_dir,)


@app.cell
def _(Image, Path, base64, embed, io, memory_dir, mo, pl, umap):
    mo.stop(
        memory_dir.value is None,
        mo.md("No memories yet: run `.venv/bin/python -m glass_memory.embed VIDEO` first."),
    )

    def _thumbnail(path, side=160):
        image = Image.open(path)
        image.thumbnail((side, side))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()

    _index, vectors = embed.load(Path(memory_dir.value))
    _xy = umap.UMAP(
        n_neighbors=min(15, len(_index) - 1), metric="cosine", random_state=0
    ).fit_transform(vectors)
    memories = _index.with_columns(
        pl.Series("x", _xy[:, 0]),
        pl.Series("y", _xy[:, 1]),
        pl.Series("image", [_thumbnail(p) for p in _index["keyframe"]]),
    )
    mo.md(f"{len(memories)} memories over {memories['t_s'].max():.0f} s")
    return memories, vectors


@app.cell
def _(mo):
    question = mo.ui.text(
        label="Question", placeholder="where did I leave my keys?", full_width=True
    )
    top_k = mo.ui.slider(1, 12, value=5, label="Memories to retrieve")
    mo.hstack([question, top_k], widths=[3, 1])
    return question, top_k


@app.cell
def _(alt, embed, memories, mo, pl, question, top_k, vectors):
    _similarity = (
        vectors @ embed.embed_text(question.value) if question.value else [0.0] * len(memories)
    )
    scored = memories.with_columns(
        pl.Series("similarity", _similarity, dtype=pl.Float32)
    ).with_columns(
        retrieved=pl.col("similarity").rank(method="ordinal", descending=True)
        <= (top_k.value if question.value else 0)
    )
    _colour = (
        alt.Color("similarity:Q", title="match to question")
        if question.value
        else alt.Color("t_s:Q", title="seconds into video")
    )
    chart = mo.ui.altair_chart(
        alt.Chart(scored)
        .mark_circle(stroke="black")
        .encode(
            x=alt.X("x:Q", axis=None),
            y=alt.Y("y:Q", axis=None),
            color=_colour.scale(scheme="viridis"),
            size=alt.Size("retrieved:N", legend=None).scale(domain=[False, True], range=[60, 300]),
            strokeWidth=alt.StrokeWidth("retrieved:N", legend=None).scale(
                domain=[False, True], range=[0, 2]
            ),
            tooltip=["image", alt.Tooltip("t_s:Q", title="seconds", format=".0f")],
        )
        .properties(width="container", height=520)
    )
    chart
    return chart, scored


@app.cell
def _(chart, mo, pl, scored):
    _selected = chart.value
    chosen = (
        (_selected if len(_selected) else scored.filter(pl.col("retrieved"))).sort("t_s").head(12)
    )
    mo.hstack(
        [
            mo.image(src=_row["keyframe"], width=200, caption=f"{_row['t_s']:.0f} s")
            for _row in chosen.iter_rows(named=True)
        ],
        wrap=True,
        justify="start",
    ) if len(chosen) else mo.md("Type a question, or drag over points on the map, to pick memories.")
    return (chosen,)


@app.cell
def _(mo):
    ask_button = mo.ui.run_button(label="Ask Gemma about these stills")
    ask_button
    return (ask_button,)


@app.cell
def _(ask, ask_button, chosen, mo, question):
    mo.stop(not ask_button.value)
    mo.stop(
        not question.value or not len(chosen),
        mo.md("Gemma needs a question and at least one memory."),
    )
    mo.md(ask.ask(question.value, chosen["keyframe"].to_list(), chosen["t_s"].to_list()))
    return


if __name__ == "__main__":
    app.run()
