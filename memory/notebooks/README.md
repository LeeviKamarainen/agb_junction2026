# notebooks/

Notebooks are **marimo** (`.py`), one folder per topic: `notebooks/<ID>/<name>.py`. Their
outputs (HTML pages, PNGs) go to `../res/items/<ID>/`. Model: `template.py`.

- **Polars + Altair** for data and plots. Fall back to matplotlib only where Altair can't do it
  (polar plots, `pcolormesh` grids); end such cells with the figure or `plt.gca()`.
- Load data through the package (`src/<pkg>`), never ad-hoc globbing over data dirs in a cell.
- Run from `memory/`: `.venv/bin/marimo edit --watch notebooks/<ID>/<name>.py`.
- After every edit run `.venv/bin/marimo check <file>` (without `--fix` it writes nothing). One
  definition per variable across cells; `_`-prefix cell-local names; read a UI element's
  `.value` in a different cell from the one that creates it.

## Quirks

- `_`-prefixed names are mangled per cell, so pandas `df.query("@_x")` can't see them.
- A running `marimo edit --watch` re-saves the file after any disk edit: one write per notebook
  per turn, and re-read the file before the next edit.
- `pl.from_pandas` turns NaN into null: test with `is_null`, not `is_nan`. Polars `cut` returns
  a Categorical.
- `app.run()` in script mode returns `(outputs, defs)`; `defs` holds every cell global.
- Altair `vconcat` shares the colour scale across rows: `resolve_scale(color="independent")`.
- Altair PNG export: `uv run --with vl-convert-python …`.

## Structure and process

1. **Imports** — one cell.
2. **Load data** — one load function and one cell that builds the table every block reads (one
   row per unit, one column per quantity), plus a small sanity table.
3. **Blocks** — each block is a markdown cell (what is computed, the population, the
   aggregation, in a few bullets) followed by the cell(s) that draw one figure. A later block
   needs new data → add columns to the load step, not a second loader.

Build **block by block with the user**: write one block, run it, report its numbers (and
anything that broke an earlier claim), then stop for the user's call on the next block. Don't
build ahead.

Statistical defaults: none yet; the notebooks here are views of the memories, not analyses.
