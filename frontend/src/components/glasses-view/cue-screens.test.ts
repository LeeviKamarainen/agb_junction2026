// The display a demo clip's cues drive on the home page. `npm test` runs it with Node's test runner.

import assert from "node:assert/strict";
import { test } from "node:test";

import { cuesEnd, cueView, HEARD_S, triggerMessage } from "./cue-screens.ts";
import { cueState } from "./cues.ts";
import { answerScreen, messageScreen } from "./screens.ts";

const ask = {
  id: "keys",
  at_s: 2,
  text: "Where are my keys?",
  stays: false,
  answer: "On the **table**.",
  kind: "prompt" as const,
};

test("a prompt is heard first, with the recording light, then answered under the same words", () => {
  const heard = cueView([ask], 2 + HEARD_S / 2);
  const answered = cueView([ask], 2 + HEARD_S + 0.1);
  const full = answerScreen(ask.text, ask.answer);

  assert.equal(heard.listening, true);
  assert.deepEqual(heard.screen?.children, full.children.slice(0, 1));
  assert.equal(heard.speech, null);
  assert.equal(answered.listening, false);
  assert.deepEqual(answered.screen, full);
  assert.equal(answered.speech, "On the table.");
  assert.notEqual(heard.shown, answered.shown);
});

test("the display is clear before a cue and after its time is up", () => {
  assert.equal(cueView([ask], 1).screen, null);
  assert.equal(cueView([ask], 2 + 9).screen, null);
  assert.equal(cueView([ask], 2 + 9).shown, null);
});

test("a trigger shows the message it fired with, split at the first colon", () => {
  const fired = { id: "door", at_s: 8, text: "Door: Ahead: on the left.", stays: false, kind: "trigger" as const };
  const view = cueView([fired], 9);

  assert.deepEqual(view.screen, messageScreen({ type: "trigger", title: "Door", text: "Ahead: on the left." }));
  assert.equal(view.listening, false);
  assert.equal(view.speech, "Door. Ahead: on the left.");
  assert.deepEqual(triggerMessage("No title here"), { title: "", text: "No title here" });
});

test("a prompt without an answer keeps showing its words, silently", () => {
  const view = cueView([{ ...ask, answer: "" }], 2 + HEARD_S + 1);

  assert.equal(view.listening, false);
  assert.equal(view.screen?.children.length, 1);
  assert.equal(view.speech, null);
});

test("a prompt that stays is watched for once its answer shows, and after it clears", () => {
  const watch = { ...ask, id: "doors", text: "Tell me if you see any doors", stays: true };

  assert.deepEqual(cueView([watch], 2.5).watching, []);
  assert.deepEqual(cueView([watch], 4).watching.map((cue) => cue.id), ["doors"]);
  assert.deepEqual(cueView([watch], 30).watching.map((cue) => cue.id), ["doors"]);
  assert.equal(cueView([watch], 30).screen, null);
});

test("the key changes with what is shown, heard and watched, not with time", () => {
  const watch = { ...ask, stays: true };
  const unanswered = { ...ask, answer: "" }; // its words stay on the display after it is heard

  assert.equal(cueView([watch], 4).key, cueView([watch], 5).key);
  assert.notEqual(cueView([watch], 2.5).key, cueView([watch], 4).key);
  assert.notEqual(cueView([watch], 5).key, cueView([watch], 30).key);
  assert.notEqual(cueView([unanswered], 2.5).key, cueView([unanswered], 4).key);
});

test("cuesEnd is when cueState stops showing the last cue", () => {
  // The WaterBottle demo: its last question comes 0.04 s before the clip ends.
  const cues = [
    { id: "a", at_s: 0, text: "Tell me if you see any doors", stays: true, answer: "Okay." },
    { id: "b", at_s: 8.1, text: "Door: There is a door.", stays: false, kind: "trigger" as const },
    { id: "c", at_s: 19.9, text: "Where did I leave my bottle?", stays: false, answer: "On the desk." },
  ];
  const silent = [{ ...ask, answer: "" }]; // shown for less time than an answered one

  assert.equal(cueState(cues, cuesEnd(cues) - 0.01).spoken?.id, "c");
  assert.equal(cueState(cues, cuesEnd(cues)).spoken, null);
  assert.equal(cueState(silent, cuesEnd(silent) - 0.01).spoken?.id, "keys");
  assert.equal(cueState(silent, cuesEnd(silent)).spoken, null);
  assert.equal(cuesEnd([]), 0);
});
