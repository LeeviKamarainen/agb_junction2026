// The live page's glasses screens, which copy the Android app's trigger message and the backend's
// memory_answer (backend/app/glasses/screens.py). `npm test` runs it with Node's test runner.

import assert from "node:assert/strict";
import { test } from "node:test";

import { answerScreen, messageScreen, plain, secondsToRead } from "./glasses-screens.ts";

test("a trigger is a bell and its title as a heading, then the text, at the bottom", () => {
  const screen = messageScreen({ type: "trigger", title: "Keys", text: "On the bench." });

  assert.equal(screen.justify, "end");
  assert.equal(screen.padding, 32);
  const [heading, text] = screen.children;
  assert.deepEqual(heading.type === "flexbox" && heading.children, [
    { type: "icon", name: "bell" },
    { type: "text", value: "Keys", style: "HEADING" },
  ]);
  assert.deepEqual(text, { type: "text", value: "On the bench.", style: "BODY" });
});

test("a reply shows its title as a small label above the text", () => {
  const screen = messageScreen({ type: "text_reply", title: "Gemma", text: "Yes." });

  assert.deepEqual(
    screen.children.map((node) => node.type === "text" && node.style),
    ["CAPTION", "BODY"],
  );
});

test("an answer is the question small and the answer in plain text, smaller when long", () => {
  const short = answerScreen("Where are my keys?", "**In** your coat:\n- left pocket");
  assert.deepEqual(short.children, [
    { type: "text", value: "Where are my keys?", style: "CAPTION" },
    { type: "text", value: "In your coat: left pocket", style: "BODY" },
  ]);
  const long = answerScreen("?", "word ".repeat(40));
  assert.equal(long.children[1].type === "text" && long.children[1].style, "CAPTION");
});

test("plain and the reading time behave like the backend's", () => {
  assert.equal(plain("a: * b * c"), "a: b, c");
  assert.equal(secondsToRead("one two"), 4);
  assert.equal(secondsToRead("word ".repeat(100)), 20);
});
