import assert from "node:assert/strict";
import { test } from "node:test";

import { cueState } from "./cues.ts";

const cues = [
  { id: "bus", at_s: 2, text: "Tell me when my bus comes", stays: true },
  { id: "keys", at_s: 5, text: "Where are my keys?", stays: false },
  { id: "dog", at_s: 10, text: "Warn me about dogs", stays: true },
  { id: "no-bus", at_s: 20, text: "Forget the bus", stays: false, stops: "bus" },
  { id: "all", at_s: 30, text: "Stop everything", stays: false, stops: "*" },
];
const ids = (at: number) => cueState(cues, at).active.map((c) => c.id);

test("a prompt that stays is active until another stops it", () => {
  assert.deepEqual(ids(1), []);
  assert.deepEqual(ids(6), ["bus"]);
  assert.deepEqual(ids(12), ["bus", "dog"]);
  assert.deepEqual(ids(20), ["dog"]);
  assert.deepEqual(ids(31), []);
});

test("a prompt is being said for a few seconds", () => {
  assert.equal(cueState(cues, 5.5).spoken?.id, "keys");
  assert.equal(cueState(cues, 9.5).spoken, null);
});
