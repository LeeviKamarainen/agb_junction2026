import marimo

__generated_with = "0.25.2"
app = marimo.App(width="full")


@app.cell
def _():
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

    return Path, alt, base64, dotenv_values, httpx, json, mo, np, os, pl


@app.cell
def _(mo):
    mo.md(r"""
    # TRIG-1: can a text trigger tell when its thing comes into view?

    A trigger is a short text ("a green watering can") that Gemma would write for a goal ("remind
    me to water the plants"). It fires when a still is close enough to it in embedding space. One
    table is built once below, one row per still and trigger; each block reads it.
    """)
    return


@app.cell
def _(Path, mo, os):
    _dirs = sorted({str(p.parent) for p in Path("res/items/TRIG-1").glob("*/*.jpg")})
    stills_dir = mo.ui.dropdown(_dirs, value=_dirs[0] if _dirs else None, label="Stills")
    token = mo.ui.text(
        os.environ.get("GLASS_API_TOKEN", ""), kind="password", label="API token (`make memory-token`)"
    )
    triggers = mo.ui.text_area(
        "a green watering can\na potted plant\na light switch\na speed limit sign",
        label="Triggers, one per line",
        rows=5,
        full_width=True,
    )
    mo.hstack([mo.vstack([stills_dir, token]), triggers], widths=[1, 3])
    return stills_dir, token, triggers


@app.cell
def _(
    Path,
    base64,
    dotenv_values,
    httpx,
    mo,
    np,
    os,
    pl,
    stills_dir,
    token,
    triggers,
):
    URL = os.environ.get("GLASS_API_URL") or "https://{}.sslip.io".format(
        dotenv_values(".env")["VM2_IP"].replace(".", "-")
    )
    mo.stop(
        stills_dir.value is None,
        mo.md("No stills yet: put one `.jpg` per second in `res/items/TRIG-1/<video>/`."),
    )
    mo.stop(not token.value, mo.md("Paste the API token above: the stills are embedded on VM2."))


    def data_url(still):
        return "data:image/jpeg;base64," + base64.b64encode(still.read_bytes()).decode()


    def embedded(inputs):
        """EmbeddingGemma 2 vectors from the memory API, L2-normalised, one row per input: a text,
        or a still as a `data:image/` URL."""
        rows = []
        for _i in range(0, len(inputs), 16):  # the API takes 16 inputs a call
            _response = httpx.post(
                URL.rstrip("/") + "/v1/embeddings",
                headers={"Authorization": f"Bearer {token.value}"},
                json={"input": inputs[_i : _i + 16]},
                timeout=300,
            )
            _response.raise_for_status()
            rows += [_row["embedding"] for _row in _response.json()["data"]]
        return np.asarray(rows, np.float32)


    # The stills are embedded once and kept in their folder; a still's file name is its second.
    stills = sorted(Path(stills_dir.value).glob("*.jpg"))
    _kept = Path(stills_dir.value) / "embeddings.npy"
    if not _kept.exists():
        np.save(_kept, embedded([data_url(p) for p in stills]))
    vectors = np.load(_kept)


    def load(texts):
        """One row per still and trigger: the cosine similarity of the two."""
        similarity = vectors @ embedded(texts).T
        return pl.concat(
            pl.DataFrame(
                {
                    "t_s": [float(p.stem) for p in stills],
                    "keyframe": [str(p) for p in stills],
                    "trigger": text,
                    "similarity": similarity[:, j],
                }
            )
            for j, text in enumerate(texts)
        )


    data = load([t.strip() for t in triggers.value.splitlines() if t.strip()])
    # sanity table: one row per trigger, and the second its best still is at
    data.group_by("trigger", maintain_order=True).agg(
        pl.len().alias("stills"),
        pl.col("similarity").median().alias("median"),
        pl.col("similarity").max().alias("max"),
        pl.col("t_s").sort_by("similarity").last().alias("best_t_s"),
    )
    return URL, data, data_url, embedded, stills, vectors


@app.cell
def _(mo):
    mo.md(r"""
    **Block 1: similarity over the video.**

    - Computed: cosine similarity of each still to each trigger text, shown through a filter.
      *None*: the similarity itself. *Baseline*: the similarity minus the trigger's median over the
      seconds before (the window), so a still is scored against where the video has just been.
      *Rolling mean*: the mean over the window up to and including the still, so only what stays in
      view counts
    - Population: every still of the video, one per second
    - Aggregation: none; the stills under the chart are each trigger's three best after the filter
    """)
    return


@app.cell
def _(mo):
    filter_by = mo.ui.radio(["None", "Baseline", "Rolling mean"], value="None", label="Filter", inline=True)
    window = mo.ui.slider(steps=[2, 3, 5, 8, 12, 20, 30, 45, 60, 90], value=20, label="Window, seconds")
    mo.hstack([filter_by, window], justify="start", gap=2)
    return filter_by, window


@app.cell
def _(data, filter_by, pl, window):
    def through(column):
        """The column through the chosen filter. The stills are one second apart, so a window of n
        seconds is n rows."""
        before = column.rolling_median(window.value, min_samples=1).shift(1)
        return {
            "None": column,
            "Baseline": column - before.fill_null(column),
            "Rolling mean": column.rolling_mean(window.value, min_samples=1),
        }[filter_by.value]


    filtered = data.with_columns(value=through(pl.col("similarity")).over("trigger"))
    value_title = {
        "None": "similarity to trigger",
        "Baseline": f"similarity above the median of the {window.value} s before",
        "Rolling mean": f"mean similarity over the last {window.value} s",
    }[filter_by.value]
    # sanity table: how far each trigger's best still stands out from its median, after the filter
    filtered.group_by("trigger", maintain_order=True).agg(
        pl.col("value").median().alias("median"),
        pl.col("value").max().alias("max"),
        ((pl.col("value").max() - pl.col("value").median()) / pl.col("value").std()).alias(
            "max_in_sd"
        ),
        pl.col("t_s").sort_by("value").last().alias("best_t_s"),
    )
    return filtered, through, value_title


@app.cell
def _(alt, filtered, value_title):
    alt.Chart(filtered).mark_line(point=True).encode(
        x=alt.X("t_s:Q", title="seconds into video"),
        y=alt.Y("value:Q", title=value_title).scale(zero=False),
        color=alt.Color("trigger:N", title="trigger"),
        tooltip=[
            "trigger",
            alt.Tooltip("t_s:Q", format=".0f"),
            alt.Tooltip("value:Q", format=".3f"),
            alt.Tooltip("similarity:Q", format=".3f"),
        ],
    ).properties(width="container", height=360)
    return


@app.cell
def _(filtered, mo):
    mo.vstack(
        [
            mo.hstack(
                [mo.md(f"**{_trigger}**")]
                + [
                    mo.image(
                        src=_row["keyframe"],
                        width=200,
                        caption=f"{_row['t_s']:.0f} s, {_row['value']:.3f}",
                    )
                    for _row in _best.iter_rows(named=True)
                ],
                justify="start",
            )
            for (_trigger,), _best in filtered.sort("value", descending=True)
            .group_by("trigger", maintain_order=True)
            .head(3)
            .group_by("trigger", maintain_order=True)
        ]
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    **Block 2: Gemma writes the triggers, sets the level each fires at, and decides at each firing.**

    - Computed: Gemma turns the goal into triggers, each with the level it fires at, starting low.
      A still's score for a trigger is its similarity through block 1's filter and window. The
      stills are walked in time order, and the first one that reaches a trigger's level wakes Gemma
      with that still, its score, the goal and its own earlier notes. It answers with what to say
      (or nothing), the triggers and levels to watch from then on (raised after a false alarm), any
      trigger to stop checking for a number of seconds (a thing that is in view and need not wake it
      again yet), and a note to itself
    - Level: with the automatic level on, a new trigger starts at its own noise: the median of its
      scores over the minute before it was set, plus the chosen number of spreads (robust standard
      deviations). Gemma still raises it after a false alarm
    - Population: every still from the second the goal is said at; after a firing the next few
      seconds are skipped
    - Aggregation: none; one row per firing
    """)
    return


@app.cell
def _(mo, os):
    goal = mo.ui.text_area(
        "When I come back in from the balcony, make sure I don't trip over the watering can.",
        label="Goal, said at the start of the video",
        rows=2,
        full_width=True,
    )
    gemma_key = mo.ui.text(
        os.environ.get("GEMMA_API_KEY", ""), kind="password", label="Gemma key (`GEMMA_API_KEY` on VM2)"
    )
    cooldown = mo.ui.slider(1, 15, value=4, label="Seconds skipped after a firing")
    go = mo.ui.run_button(label="Run Gemma over the video")
    mo.vstack([goal, mo.hstack([gemma_key, cooldown, go], justify="start")])
    return cooldown, gemma_key, go, goal


@app.cell
def _(filter_by, mo, stills):
    # Scores through the Baseline filter sit around zero, the others around 0.6.
    start_at = (
        mo.ui.slider(0.0, 0.2, step=0.005, value=0.02, label="New triggers start at")
        if filter_by.value == "Baseline"
        else mo.ui.slider(0.55, 0.75, step=0.01, value=0.62, label="New triggers start at")
    )
    auto_k = mo.ui.slider(
        0, 4, step=0.5, value=2, label="Automatic level: times the trigger's own noise (0 = off)"
    )
    said_at = mo.ui.slider(0, len(stills) - 1, value=0, label="Goal said at second")
    mo.hstack([start_at, auto_k, said_at], justify="start", gap=2)
    return auto_k, said_at, start_at


@app.cell(hide_code=True)
def _(Path, mo):
    skill = mo.ui.text_area(
        Path("../backend/app/glasses/trigger_skill.md").read_text(),
        label="Trigger skill: what Gemma reads with every prompt. Edits here are tried at once;"
        " the kept copy is `../backend/app/glasses/trigger_skill.md`",
        rows=12,
        full_width=True,
    )
    skill
    return (skill,)


@app.cell
def _(
    URL,
    auto_k,
    cooldown,
    data_url,
    embedded,
    filter_by,
    gemma_key,
    go,
    goal,
    httpx,
    json,
    mo,
    np,
    os,
    pl,
    said_at,
    skill,
    start_at,
    stills,
    through,
    vectors,
    window,
):
    mo.stop(not go.value, mo.md("Press the button: each firing is one call to Gemma."))
    mo.stop(not gemma_key.value, mo.md("Paste Gemma's key above."))

    scale = {
        "None": "A score is the still's similarity to the trigger, not a probability. A still that has"
        " nothing to do with the trigger scores about 0.55 to 0.65, a clear close view about 0.75 to"
        " 0.80, and a small or distant one in between. The highest level that makes sense is 0.80.",
        "Baseline": "A score is how far the still's similarity to the trigger is above its median"
        f" over the {window.value} seconds before, so it shows what has just come into view. A still"
        " with nothing new scores within 0.02 of zero, a small or distant thing about 0.03 to 0.08,"
        " and a clear close view 0.10 to 0.20. Something that stays in view stops scoring as the"
        " median catches up. The highest level that makes sense is 0.15.",
        "Rolling mean": "A score is the still's similarity to the trigger, averaged over the last"
        f" {window.value} seconds, so only what stays in view scores. Nothing related scores about"
        " 0.55 to 0.65 and a lasting clear view about 0.70 to 0.78. The highest level that makes"
        " sense is 0.78.",
    }[filter_by.value]

    SYSTEM = """You run on a person's smart glasses and help them with a goal they told you. You \
    cannot watch the camera yourself. You set triggers: short descriptions of one concrete, visible \
    thing each, like "a green watering can". Each trigger has a level: every still gets a similarity \
    score to the trigger, and you are woken with the still when the score reaches the level.

    """ + skill.value.strip() + "\n\n" + scale + """ A new trigger starts \
    at a low level, as you are told with each message. When you are woken and the thing is not in the still, raise that \
    trigger's level a little above the score that woke you. When the thing is in the still, leave \
    the level alone and snooze the trigger instead.

    Always answer with JSON only: {"say": what to say to the person out loud now, or null to stay \
    quiet, "triggers": an object from each trigger to watch from now on to its level, like \
    {"a green watering can": its level} (empty once the goal is done), "snooze": an object from a trigger to the \
    number of seconds to stop checking it, like {"a green watering can": 20}, for a thing that is in \
    view and that you do not want to be woken by again yet (leave it out otherwise), "note": one line to yourself on \
    what you saw and what you are waiting for}."""


    def gemma(content):
        """Gemma's JSON answer to one message (text, or text and image parts), its triggers as
        {text: level}."""
        _response = httpx.post(
            URL.rstrip("/") + "/v1/chat/completions",
            headers={"Authorization": f"Bearer {gemma_key.value}"},
            json={
                "model": os.environ.get("GEMMA_MODEL", "google/gemma-4-31B-it"),
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": content},
                ],
                "max_tokens": 600,  # room for ten triggers and a note
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        _response.raise_for_status()
        _text = _response.json()["choices"][0]["message"]["content"]
        _reply = json.loads(_text[_text.find("{") : _text.rfind("}") + 1])
        _reply["triggers"] = {str(k): float(v) for k, v in (_reply.get("triggers") or {}).items()}
        _reply["snooze"] = {str(k): float(v) for k, v in (_reply.get("snooze") or {}).items()}
        return _reply


    def shown(watching):
        return ", ".join(f"{text} ≥ {level:.2f}" for text, level in watching.items())


    def scores(texts):
        """Every still's score for each trigger, through block 1's filter: stills by triggers."""
        return pl.DataFrame(vectors @ embedded(texts).T).select(through(pl.all())).to_numpy()


    NOISE_S = 60  # a new trigger's noise is measured over this many seconds before it is set


    def levelled(triggers, known, i, k):
        """The triggers with their texts and every still's scores for them. With k, each trigger
        not in `known` gets its level from its own noise: k times the spread of its scores over the
        NOISE_S seconds before still i (1.4826 times the median distance from the median, which is
        one standard deviation for normal noise), above their median. Too little video before i
        leaves the level as given."""
        texts = list(triggers)
        scored = scores(texts) if texts else None
        past = scored[max(0, i - NOISE_S) : i] if texts else []
        if k and len(past) >= 10:
            middle = np.median(past, axis=0)
            spread = 1.4826 * np.median(np.abs(past - middle), axis=0)
            triggers = {
                text: triggers[text] if text in known else float(middle[j] + k * spread[j])
                for j, text in enumerate(texts)
            }
        return triggers, texts, scored


    def walk(goal_text, low, skip_s, begin, k):
        """Gemma's plan for the goal, said at second `begin`, and one row per firing as the stills
        after it go by in time order."""
        start = (
            "A new trigger's level is set for you from its own noise, so give it 0."
            if k
            else f"New triggers start at the low level {low:.2f}."
        )
        plan = gemma(f"Goal: {goal_text}\nNothing has been seen yet. Set your first triggers. {start}")
        watching, texts, scored = levelled(plan["triggers"], {}, begin, k)
        plan["triggers"] = watching
        notes, rows, quiet_until, asleep = [f"start: {plan['note']}"], [], begin, {}
        for _i, _still in enumerate(stills):
            if not watching:
                break
            if _i < quiet_until:
                continue
            # how far each trigger's score is over its level; the furthest over wakes Gemma
            _similarity = scored[_i]
            _over = _similarity - np.array([watching[text] for text in texts])
            _over[[_i < asleep.get(text, 0) for text in texts]] = -np.inf  # snoozed by Gemma
            _j = int(_over.argmax())
            if _over[_j] < 0:
                continue
            _t, _fired, _score = float(_still.stem), texts[_j], float(_similarity[_j])
            _reply = gemma(
                [
                    {
                        "type": "text",
                        "text": f"Goal: {goal_text}\nYour notes so far:\n"
                        + "\n".join(notes)
                        + f"\nYou are watching: {shown(watching)}. {start}"
                        + f'\nNow, at {_t:.0f} s, "{_fired}" scored {_score:.2f} on this still:',
                    },
                    {"type": "image_url", "image_url": {"url": data_url(_still)}},
                ]
            )
            _level = watching[_fired]
            watching, texts, scored = levelled(_reply["triggers"], watching, _i + 1, k)
            rows.append(
                {
                    "t_s": _t,
                    "keyframe": str(_still),
                    "trigger": _fired,
                    "similarity": _score,
                    "fires_at": _level,
                    "say": _reply.get("say"),
                    "note": _reply.get("note"),
                    "watching_after": shown(watching),
                    "snoozed": ", ".join(
                        f"{text} for {seconds:.0f} s" for text, seconds in _reply["snooze"].items()
                    ),
                }
            )
            notes.append(f"{_t:.0f} s: {_reply.get('note')}")
            asleep |= {text: _i + 1 + seconds for text, seconds in _reply["snooze"].items()}
            quiet_until = _i + 1 + skip_s
        return plan, pl.DataFrame(
            rows,
            schema={
                "t_s": pl.Float64,
                "keyframe": pl.String,
                "trigger": pl.String,
                "similarity": pl.Float64,
                "fires_at": pl.Float64,
                "say": pl.String,
                "note": pl.String,
                "watching_after": pl.String,
                "snoozed": pl.String,
            },
        )


    plan, firings = walk(goal.value, start_at.value, cooldown.value, said_at.value, auto_k.value)
    mo.md(
        f"**Gemma answers the goal:** {plan.get('say') or '(stays quiet)'}  \n"
        f"**Its first triggers:** {shown(plan['triggers'])}  \n**Its note:** {plan['note']}"
        f"  \n**Woken** {len(firings)} times, **spoke** {firings['say'].count()} times"
    )
    return (firings,)


@app.cell
def _(firings, mo):
    mo.vstack(
        [
            mo.hstack(
                [
                    mo.image(src=_row["keyframe"], width=260),
                    mo.md(
                        f"**{_row['t_s']:.0f} s**: \"{_row['trigger']}\" scored"
                        f" {_row['similarity']:.2f} (level {_row['fires_at']:.2f})\n\n"
                        f"**Says:** {_row['say'] or '(stays quiet)'}\n\n"
                        f"**Note:** {_row['note']}\n\n"
                        f"**Watching next:** {_row['watching_after'] or '(nothing, done)'}"
                        + (f"\n\n**Snoozed:** {_row['snoozed']}" if _row["snoozed"] else "")
                    ),
                ],
                justify="start",
            )
            for _row in firings.iter_rows(named=True)
        ]
    ) if len(firings) else mo.md("No trigger fired.")
    return


if __name__ == "__main__":
    app.run()
