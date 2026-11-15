import type { components } from "@/lib/api/schema";

import type { FlexBoxNode, LayoutNode } from "../glasses/types";

type Message = components["schemas"]["GlassesMessage"];

/**
 * The glasses' screens for what the live page gets back, so its glasses display shows what the
 * wearer would see. A trigger's message is drawn as the Android app draws it
 * (glasses-android, GlassesViewModel: a bell and the title as a heading, then the text, for
 * MESSAGE_S); an answer as the backend's memory_answer screen (backend/app/glasses/screens.py),
 * which is what the glasses show for a spoken question. Change them together.
 */

export const MESSAGE_S = 10; // MESSAGE_MS in the Android app
const PAD = 32; // PAD in screens.py

function box(children: LayoutNode[], extra: Partial<FlexBoxNode> = {}): FlexBoxNode {
  return {
    type: "flexbox",
    direction: "column",
    gap: 16,
    padding: 0,
    justify: "start",
    align: "stretch",
    children,
    ...extra,
  };
}

/** Like screens.plain: Markdown emphasis and bullets turned into one plain sentence. */
export function plain(text: string): string {
  const items = text
    .replace(/(^|\n|\s)[-*•]\s+/g, "\n")
    .replace(/[*_`#]+/g, "")
    .split("\n")
    .map((line) => line.trim().replace(/[,;]+$/, ""))
    .filter(Boolean);
  return items.join(", ").replaceAll(":,", ":").replace(/\s+/g, " ").trim();
}

/** Like screens._seconds_to_read: long enough to read it once, between 4 and 20 seconds. */
export function secondsToRead(text: string): number {
  return Math.min(20, Math.max(4, 2 + text.split(/\s+/).filter(Boolean).length * 0.4));
}

/** A trigger's message, or another message from a frame. */
export function messageScreen(message: Message): FlexBoxNode {
  const content: LayoutNode[] =
    message.type === "trigger"
      ? [
          box(
            [
              { type: "icon", name: "bell" },
              { type: "text", value: message.title, style: "HEADING" },
            ],
            { direction: "row", gap: 8, align: "center" },
          ),
          { type: "text", value: message.text, style: "BODY" },
        ]
      : [
          ...(message.title
            ? [{ type: "text", value: message.title, style: "CAPTION" } as LayoutNode]
            : []),
          { type: "text", value: message.text, style: "BODY" },
        ];
  return box(content, { padding: PAD, justify: "end" });
}

/** The answer to a question: screens.memory_answer. */
export function answerScreen(question: string, answer: string): FlexBoxNode {
  const said = plain(answer);
  return box(
    [
      { type: "text", value: question, style: "CAPTION" },
      { type: "text", value: said, style: said.length <= 160 ? "BODY" : "CAPTION" },
    ],
    { padding: PAD, justify: "end" },
  );
}
