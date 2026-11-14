"use client";

import { ArrowLeft, Circle, Film, Maximize, Minimize, Pause, Play } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";
import { useSession } from "@/lib/session";

import { EVENTS, type FeedEvent, KINDS, LiveFrame, clientInfo, since } from "../stream-wall";

type Detail = components["schemas"]["FeedDetail"];

const RELOAD_MS = 1_000; // the log is reloaded this often

const time = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

/**
 * One stream in full: its frames large, about twice a second, with fullscreen, and under it the
 * verbose backend log of its client, as the Memory lab's: what became of every frame (stored or
 * why not, each trigger's score against its level, what fired), the user's questions, Gemma's
 * searches and answers, goals, reminders, warnings and errors, each with its JSON.
 */
export function StreamDetail() {
  const { id } = useParams<{ id: string }>();
  const { user } = useSession();
  const admin = user?.role === "admin";
  const [feed, setFeed] = useState<Detail | null>(null);
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(0);
  const [showFrames, setShowFrames] = useState(true);
  const [paused, setPaused] = useState(false);
  const [clearedAt, setClearedAt] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const viewRef = useRef<HTMLDivElement>(null);
  const pausedRef = useRef(paused);
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    if (!admin) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const { data, response } = await api.GET("/api/admin/streams/{feed_id}", {
          params: { path: { feed_id: id } },
          cache: "no-store",
        });
        if (stopped) return;
        if (data) {
          if (!pausedRef.current) setFeed(data);
          setEnded(false);
          setError(null);
        } else if (response.status === 404) setEnded(true);
        else setError(`Could not load the stream (HTTP ${response.status}).`);
      } catch {
        if (!stopped) setError("Could not reach the backend.");
      }
      setNow(Date.now());
      if (!stopped) timer = setTimeout(() => void load(), RELOAD_MS);
    };
    void load();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [admin, id]);

  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === viewRef.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await viewRef.current?.requestFullscreen();
    } catch {
      // Not allowed here (an iframe): the view stays as it is.
    }
  }

  if (user === undefined) return <p className="py-12 text-sm text-muted-foreground">Loading…</p>;
  if (!admin)
    return (
      <p className="py-12 text-sm text-muted-foreground">
        Only the admin sees the streams.{" "}
        <Link href="/admin" className="underline">
          Log in as the admin
        </Link>
        .
      </p>
    );

  const { label, Icon } = clientInfo(feed?.client ?? "");
  // About a frame: its result or its error. They come twice a second, so they can be hidden.
  const aboutFrame = (entry: FeedEvent) => entry.via === feed?.client;
  const log = collapse(
    (feed?.log ?? []).filter(
      (entry) => (showFrames || !aboutFrame(entry)) && (!clearedAt || entry.at > clearedAt),
    ),
  );
  const frameCount = (feed?.log ?? []).filter(aboutFrame).length;

  return (
    <div className="flex flex-col gap-4 pb-8">
      <Link
        href="/admin/streams"
        className="inline-flex w-fit items-center gap-1.5 pt-4 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={14} aria-hidden="true" /> All streams
      </Link>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {!feed && !error && (
        <p className="text-sm text-muted-foreground">
          {ended ? "This stream has stopped, or never existed." : "Loading the stream…"}
        </p>
      )}

      {feed && (
        <>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Icon size={14} aria-hidden="true" /> {label} · {KINDS[feed.kind]}
              </p>
              <h1 className="text-xl font-medium tracking-tight">{feed.user}</h1>
              <p className="text-xs text-muted-foreground">
                {feed.frames} frames since {time(feed.started_at)}
                {feed.live && feed.fps > 0 ? ` · ${feed.fps} fps` : ""}
                {feed.stream_id ? ` · stream ${feed.stream_id}` : ""}
              </p>
            </div>
            {ended && (
              <p role="status" className="rounded-md bg-secondary px-3 py-1.5 text-xs">
                The stream stopped: showing its last frame and log.
              </p>
            )}
          </div>

          <div
            ref={viewRef}
            className={`relative overflow-hidden bg-black ${
              fullscreen ? "h-screen w-screen" : "aspect-video max-h-[70vh] w-full rounded-lg"
            }`}
          >
            <LiveFrame id={feed.id} live={feed.live && !ended} alt={`${feed.user}'s ${KINDS[feed.kind]}`} />
            <span
              className={`absolute top-3 left-3 flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${
                feed.live && !ended ? "bg-red-600 text-white" : "bg-black/60 text-white/70"
              }`}
            >
              {feed.live && !ended && (
                <Circle size={8} className="animate-pulse fill-white" aria-hidden="true" />
              )}
              {feed.live && !ended ? "LIVE" : `idle · ${since(feed.last_at, now)}`}
            </span>
            {feed.demo && (
              <span className="absolute top-3 right-3 flex items-center gap-1 rounded-full bg-black/60 px-2.5 py-1 text-xs text-white">
                <Film size={12} aria-hidden="true" /> demo clip · not stored
              </span>
            )}
            <button
              type="button"
              onClick={() => void toggleFullscreen()}
              className="absolute right-3 bottom-3 inline-flex items-center gap-1.5 rounded-md bg-black/60 px-2.5 py-1.5 text-xs text-white hover:bg-black/80"
            >
              {fullscreen ? <Minimize size={14} aria-hidden="true" /> : <Maximize size={14} aria-hidden="true" />}
              {fullscreen ? "Exit fullscreen" : "Fullscreen"}
            </button>
          </div>

          <section aria-labelledby="log-title" className="flex flex-col rounded-lg border border-border bg-card">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border p-3">
              <h2 id="log-title" className="text-sm font-medium">
                Backend log{" "}
                <span className="text-muted-foreground">
                  · {log.reduce((sum, item) => sum + item.repeats, 0)} entries
                </span>
              </h2>
              <div className="flex flex-wrap items-center gap-3 text-xs">
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={showFrames}
                    onChange={(event) => setShowFrames(event.target.checked)}
                  />
                  Frames ({frameCount})
                </label>
                <button
                  type="button"
                  aria-pressed={paused}
                  onClick={() => setPaused((on) => !on)}
                  className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 hover:bg-secondary"
                >
                  {paused ? <Play size={12} aria-hidden="true" /> : <Pause size={12} aria-hidden="true" />}
                  {paused ? "Resume" : "Pause"}
                </button>
                <button
                  type="button"
                  onClick={() => setClearedAt(feed.log[0]?.at ?? null)}
                  className="rounded-md border border-border px-2 py-1 hover:bg-secondary"
                >
                  Clear
                </button>
              </div>
            </div>
            {log.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">Nothing logged yet.</p>
            ) : (
              <ol className="max-h-[60vh] overflow-y-auto font-mono text-xs">
                {log.map(({ entry, repeats, since: first }, index) => (
                  <LogEntry
                    key={`${entry.at}-${entry.kind}-${index}`}
                    entry={entry}
                    repeats={repeats}
                    first={first}
                  />
                ))}
              </ol>
            )}
          </section>
        </>
      )}
    </div>
  );
}

/** Runs of the same entry (the same failure on every frame, say) as one line with a count. */
function collapse(entries: FeedEvent[]): { entry: FeedEvent; repeats: number; since: string }[] {
  const out: { entry: FeedEvent; repeats: number; since: string }[] = [];
  for (const entry of entries) {
    const last = out.at(-1);
    if (last && last.entry.kind === entry.kind && last.entry.text === entry.text && !entry.detail) {
      last.repeats += 1;
      last.since = entry.at; // newest first: this one is older
    } else out.push({ entry, repeats: 1, since: entry.at });
  }
  return out;
}

function LogEntry({ entry, repeats, first }: { entry: FeedEvent; repeats: number; first: string }) {
  const { label, Icon, tone } = EVENTS[entry.kind];
  const line = (
    <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2">
      <time className="text-muted-foreground" title={repeats > 1 ? `since ${time(first)}` : undefined}>
        {time(entry.at)}
      </time>
      {repeats > 1 && (
        <span className="rounded bg-secondary px-1.5 text-[11px]" title={`since ${time(first)}`}>
          ×{repeats}
        </span>
      )}
      <span className={`inline-flex items-center gap-1 font-medium ${tone}`}>
        <Icon size={12} className="self-center" aria-hidden="true" /> {label}
      </span>
      <span className={`min-w-0 break-words font-sans ${entry.kind === "error" ? "text-red-400" : ""}`}>
        {entry.text}
      </span>
      <span className="text-muted-foreground">
        {entry.ms !== null && entry.ms !== undefined ? `${entry.ms} ms · ` : ""}via {entry.via}
      </span>
    </span>
  );
  return (
    <li className={`border-b border-border/60 px-3 py-1.5 ${entry.kind === "error" ? "bg-destructive/10" : ""}`}>
      {entry.detail ? (
        <details>
          <summary className="flex cursor-pointer list-none gap-2">{line}</summary>
          <pre className="mt-1 overflow-x-auto rounded bg-background/60 p-2 text-[11px]">
            {JSON.stringify(entry.detail, null, 2)}
          </pre>
        </details>
      ) : (
        <div className="flex gap-2">{line}</div>
      )}
    </li>
  );
}
