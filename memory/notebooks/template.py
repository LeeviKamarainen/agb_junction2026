import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import altair as alt
    import marimo as mo
    import polars as pl

    return alt, mo, pl


@app.cell
def _(mo):
    mo.md(r"""
    # <ID>: <question>, block by block

    One table is built once below (one row per unit, one column per quantity); each block reads
    it and draws one figure.
    """)
    return


@app.cell
def _(pl):
    def load():
        """One row per unit, one column per quantity. Replace with the package loader."""
        return pl.DataFrame({
            "group": ["a", "a", "b", "b"],
            "x": [0.0, 1.0, 0.0, 1.0],
            "y": [0.1, 0.4, 0.2, 0.7],
        })


    data = load()
    # sanity table: one row per group
    data.group_by("group").agg(pl.len().alias("n"), pl.col("y").mean().alias("y_mean")).sort("group")
    return (data,)


@app.cell
def _(mo):
    mo.md(r"""
    **Block 1 — <what is shown>.**

    - Computed: <quantity>
    - Population: <which rows, which cuts>
    - Aggregation: <per unit or pooled; how groups are weighted>
    """)
    return


@app.cell
def _(alt, data):
    alt.Chart(data).mark_line(point=True).encode(
        x=alt.X("x:Q", title="x"),
        y=alt.Y("y:Q", title="y"),
        color=alt.Color("group:N", title="group"),
    ).properties(width=600, height=360)
    return


if __name__ == "__main__":
    app.run()
