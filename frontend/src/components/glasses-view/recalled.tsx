"use client";

import { History } from "lucide-react";

import type { components } from "@/lib/api/schema";

import type { LayoutNode } from "@/app/glasses/types";

export type GlassesReply = components["schemas"]["GlassesReply"];

// The glasses stream this many frames a second.
export const FPS = 2;
// The questions the backend takes for déjà vu: a copy of _ASKS in backend/app/glasses/dejavu.py,
// so change the two together.
export const RECALL =
  /deja vu|déjà vu|\b(know|remember|recogni[sz]e|seen|see) (this|that)( one| thing| place)?( before| already| from( somewhere| before)?)?\W*$/i;

/** The glasses' déjà vu screen in the glasses view: its stills with their captions, and its text. */
export function Recalled({
  screen,
  inline = false,
}: {
  screen: LayoutNode;
  /** In the flow of its container, not laid over the bottom of the view. */
  inline?: boolean;
}) {
  const stills: { src: string; alt: string; caption: string }[] = [];
  const lines: { value: string; heading: boolean }[] = [];
  (function walk(node: LayoutNode) {
    if (node.type === "text")
      lines.push({ value: node.value, heading: node.style === "HEADING" });
    if (node.type !== "flexbox") return;
    const [image, caption] = node.children;
    // A still is a box of an image and the caption under it.
    if (image?.type === "image" && caption?.type === "text")
      stills.push({ src: image.src, alt: image.alt, caption: caption.value });
    else node.children.forEach(walk);
  })(screen);
  return (
    <span
      role="status"
      className={`${inline ? "" : "absolute inset-x-4 bottom-4 "}flex flex-col gap-2 rounded-lg bg-black/75 p-3 text-sm text-white`}
    >
      {lines
        .filter((line) => line.heading)
        .map((line) => (
          <span
            key={line.value}
            className="flex items-center gap-1.5 font-semibold"
          >
            <History size={14} aria-hidden="true" /> {line.value}
          </span>
        ))}
      {stills.length > 0 && (
        <span className="grid grid-cols-3 gap-2">
          {stills.map((still) => (
            <span key={still.src} className="flex flex-col gap-1">
              {/* A plain <img>: the backend serves the still as it is. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={still.src}
                alt={still.alt}
                className={`aspect-video w-full rounded-md object-cover ${
                  still.caption.startsWith("Same") ? "ring-2 ring-white" : ""
                }`}
              />
              <span className="text-xs text-white/70">{still.caption}</span>
            </span>
          ))}
        </span>
      )}
      {lines
        .filter((line) => !line.heading)
        .map((line) => (
          <span key={line.value}>{line.value}</span>
        ))}
    </span>
  );
}
