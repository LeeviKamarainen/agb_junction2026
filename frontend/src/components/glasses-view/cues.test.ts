import assert from "node:assert/strict";
import { test } from "node:test";

import { cueState } from "./cues.ts";

const cues = [
  { id: "bus", at_s: 2, text: "Tell me when my bus comes", stays: true },
  { id: "keys", at_s: 5, text: "Where are my keys?", stays: false },
  { id: "dog", at_s: 10, text: "Warn me about dogs", stays: true },
  {
    id: "no-bus",
    at_s: 20,
    text: "Forget the bus",
    stays: false,
    stops: "bus",
  },
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

test("a prompt with a stop time is active until then", () => {
  const timed = [{ ...cues[0], until_s: 8 }, cues[2]];
  assert.deepEqual(cueState(timed, 7.9).active.length, 1);
  assert.deepEqual(cueState(timed, 8).active, []);
  assert.equal(cueState(timed, 12).active[0].id, "dog");
});

test("a prompt with an answer is shown longer", () => {
  const answered = [{ ...cues[1], answer: "On the table." }];
  assert.equal(cueState(answered, 11).spoken?.id, "keys");
  assert.equal(cueState(answered, 13.5).spoken, null);
});

test("a trigger's message is shown like an answer and never active", () => {
  const fired = [
    {
      id: "t",
      at_s: 3,
      text: "Bus: it is here",
      stays: false,
      kind: "trigger" as const,
    },
  ];
  assert.equal(cueState(fired, 10).spoken?.id, "t");
  assert.deepEqual(cueState(fired, 10).active, []);
  assert.equal(cueState(fired, 11.5).spoken, null);
});

test("a prompt is being said for a few seconds", () => {
  assert.equal(cueState(cues, 5.5).spoken?.id, "keys");
  assert.equal(cueState(cues, 9.5).spoken, null);
});
