"use client";

import { MessageSquareText, Video } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { RECALL } from "@/components/glasses-view/glasses";
import { api } from "@/lib/api/browser";

import { GlassesHarness } from "../glasses/harness";
import { AskChat } from "./ask-chat";
import { useCollections } from "./collections";
import type { GlassesReply } from "../glasses/types";
import Link from "next/link";
import { MemoryLibrary } from "./memory-library";
import { MemoryMap } from "./memory-map";
import { type Match, TriggerEditor } from "./trigger-editor";
import { errorText } from "./types";

type Tab = "ask" | "stream";

const tabs: { id: Tab; label: string; Icon: typeof Video }[] = [
  { id: "ask", label: "Ask", Icon: MessageSquareText },
  { id: "stream", label: "Memory", Icon: Video },
];

// What the chat under the stream suggests: déjà vu, a goal for the triggers, a memory question.
const STREAM_EXAMPLES = [
  "Where have I seen this one?",
  "Warn me when I pick up food that might have nuts",
  "Where did I leave my keys?",
];
const REFRESH_EVERY_MS = 5_000; // while streaming, reload the library and map at most this often

/**
 * The memories on the left; on the right, either the Ask chat (Gemma's searches step by step) or
 * the glasses harness (what the wearer sees and hears). Both stay mounted, so switching tabs
 * keeps the conversation and the display. The Stream tab streams the webcam as the glasses would,
 * with the triggers it is checked against; the frames it stores show up in the library.
 * Below, the map of every stored vector, ringing what the latest question found.
 */
export function MemoryLab() {
  const [tab, setTab] = useState<Tab>("stream");
  const [refreshKey, setRefreshKey] = useState(0); // the Stream tab stored a frame
  const [version, setVersion] = useState(0); // memories changed here or on the glasses
  const [found, setFound] = useState<number[]>([]);
  const collections = useCollections(version);
  const changed = useCallback(() => setVersion((n) => n + 1), []);
  const [matches, setMatches] = useState<Map<number, Match>>(new Map());
  const lastRefresh = useRef(0);
  const [fired, setFired] = useState(0); // triggers fired, to refresh "last fired" and levels
  // The reply to a déjà vu question, on the glasses view for as long as the glasses would show it.
  const [recalled, setRecalled] = useState<GlassesReply | null>(null);
  const recallTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(recallTimer.current), []);

  /** Déjà vu: asks, as the glasses would, where the newest stream frame was seen before, shows
   * their reply on the glasses view, and gives what they say for the chat. */
  async function recall(question: string): Promise<string> {
    const { data, error } = await api.POST("/api/glasses/command", {
      body: { question, mine: false },
    });
    if (error || !data)
      throw new Error(errorText(error, "Could not ask the glasses"));
    setRecalled(data);
    clearTimeout(recallTimer.current);
    recallTimer.current = setTimeout(
      () => setRecalled(null),
      (data.dismiss_after_s ?? 10) * 1000,
    );
    document
      .getElementById("stream-title")
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return data.speech ?? "";
  }

  return (
    <>
      <main className="grid flex-1 gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <MemoryLibrary
          refreshKey={refreshKey}
          collections={collections}
          onChange={changed}
        />
        <div className="flex min-w-0 flex-col gap-3">
          <div
            role="tablist"
            aria-label="Ask or Memory"
            className="inline-flex w-fit gap-1 rounded-lg border border-border bg-card p-1"
          >
            {tabs.map(({ id, label, Icon }) => (
              <button
                key={id}
                type="button"
                role="tab"
                id={`lab-tab-${id}`}
                aria-selected={tab === id}
                aria-controls={`lab-panel-${id}`}
                onClick={() => setTab(id)}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  tab === id
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Icon size={15} aria-hidden="true" /> {label}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id="lab-panel-ask"
            aria-labelledby="lab-tab-ask"
            hidden={tab !== "ask"}
          >
            <AskChat collections={collections} onFound={setFound} />
          </div>
          <div
            role="tabpanel"
            id="lab-panel-stream"
            aria-labelledby="lab-tab-stream"
            hidden={tab !== "stream"}
            className={tab === "stream" ? "flex flex-col gap-4" : "hidden"}
          >
            <Link href="/glasses" className="text-sm text-primary underline">
              Open fullscreen glasses emulator ↗
            </Link>
            <p className="text-sm text-muted-foreground">
              Set up your memories and triggers here. The fullscreen emulator
              uses the same camera, microphone and backend pipeline.
            </p>
            <GlassesHarness
              embedded
              active={tab === "stream"}
              recalled={recalled}
              // "Warn me…" said to the glasses set up a goal: reload the list below.
              onGoal={() => setFired((n) => n + 1)}
              onFrame={(reply) => {
                setMatches(
                  new Map(
                    reply.matches?.map((m) => [
                      m.trigger_id,
                      { score: m.score ?? null, level: m.level ?? null },
                    ]),
                  ),
                );
                // A firing changes "last fired", and a false alarm the level too.
                if (reply.matches?.some((m) => m.fired)) setFired((n) => n + 1);
                const now = Date.now();
                if (
                  reply.stored &&
                  now - lastRefresh.current > REFRESH_EVERY_MS
                ) {
                  lastRefresh.current = now;
                  setRefreshKey((key) => key + 1);
                  changed();
                }
              }}
            />
            <AskChat
              collections={collections}
              onFound={setFound}
              examples={STREAM_EXAMPLES}
              intro="Ask Gemma anything the Ask tab takes, about your memories or to start and stop watching for something. Asking where you have seen this one looks up what the stream shows now."
              className="min-h-[22rem]"
              special={(question) =>
                RECALL.test(question) ? recall(question) : null
              }
              // Gemma may have added or removed a goal: reload the list below.
              onDone={() => setFired((n) => n + 1)}
            />
            <TriggerEditor
              lastMatches={matches}
              firedCount={fired}
              visible={tab === "stream"}
            />
          </div>
        </div>
      </main>
      <MemoryMap collections={collections} version={version} found={found} />
    </>
  );
}
