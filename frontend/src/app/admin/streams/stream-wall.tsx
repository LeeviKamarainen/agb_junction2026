"use client";

import {
  Bell,
  BellRing,
  Circle,
  Film,
  Glasses,
  MessageSquareText,
  MonitorPlay,
  Radio,
  Smartphone,
  Image as ImageIcon,
  Search,
  Sparkles,
  TriangleAlert,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";
import { useSession } from "@/lib/session";

export type Feed = components["schemas"]["FeedPublic"];
export type FeedEvent = Feed["events"][number];

/** What the user sent the backend, or what fired for them. */
export const EVENTS: Record<FeedEvent["kind"], { label: string; Icon: typeof Bell; tone: string }> = {
  question: { label: "Question", Icon: MessageSquareText, tone: "text-sky-300" },
  goal: { label: "Watch for", Icon: Zap, tone: "text-amber-300" },
  reminder: { label: "Reminder", Icon: Bell, tone: "text-amber-300" },
  trigger: { label: "Trigger fired", Icon: BellRing, tone: "text-red-300" },
  warning: { label: "Warning", Icon: TriangleAlert, tone: "text-red-300" },
  answer: { label: "Answer", Icon: Sparkles, tone: "text-emerald-300" },
  search: { label: "Search", Icon: Search, tone: "text-violet-300" },
  frame: { label: "Frame", Icon: ImageIcon, tone: "text-muted-foreground" },
  error: { label: "Error", Icon: TriangleAlert, tone: "text-red-400" },
};

const LIST_EVERY_MS = 2_000; // how often the list of feeds is reloaded
const FRAME_EVERY_MS = 500; // each tile asks for its newest frame at most this often: 2 fps

/** The apps that send frames, as they name themselves (backend/app/glasses/feeds.py). */
const CLIENTS: Record<string, { label: string; Icon: typeof Glasses }> = {
  glasses: { label: "Meta glasses app", Icon: Glasses },
  live: { label: "Live page", Icon: Smartphone },
  harness: { label: "Glasses harness", Icon: MonitorPlay },
};
export const clientInfo = (name: string) => CLIENTS[name] ?? { label: name, Icon: Radio };
const order = (name: string) => {
  const index = Object.keys(CLIENTS).indexOf(name);
  return index < 0 ? 99 : index;
};

export const KINDS: Record<Feed["kind"], string> = {
  stream: "stream",
  photo: "photos",
  watch: "reminder watch",
};

export function since(iso: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  return seconds < 60 ? `${seconds} s ago` : `${Math.floor(seconds / 60)} min ago`;
}

/**
 * The admin's stream wall: every client sending frames to the backend right now, grouped by app
 * (the Meta glasses app, the live page, the glasses harness), each with its newest frame about
 * twice a second and, under it, the log of what its user sent: questions, goals and reminders,
 * and the triggers and warnings that fired. Faces are blurred in every frame before the backend
 * keeps it.
 */
export function StreamWall() {
  const { user } = useSession();
  const [feeds, setFeeds] = useState<Feed[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [only, setOnly] = useState<string | null>(null);
  const [now, setNow] = useState(0); // set on every reload of the list, never while rendering
  const admin = user?.role === "admin";

  useEffect(() => {
    if (!admin) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const { data, response } = await api.GET("/api/admin/streams", { cache: "no-store" });
        if (stopped) return;
        if (data) {
          setFeeds(data);
          setError(null);
        } else setError(`Could not load the streams (HTTP ${response.status}).`);
      } catch {
        if (!stopped) setError("Could not reach the backend.");
      }
      setNow(Date.now());
      if (!stopped) timer = setTimeout(() => void load(), LIST_EVERY_MS);
    };
    void load();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [admin]);

  if (user === undefined) return <p className="py-12 text-sm text-muted-foreground">Loading…</p>;
  if (!admin)
    return (
      <p className="py-12 text-sm text-muted-foreground">
        Only the admin sees the streams. <Link href="/admin" className="underline">Log in as the admin</Link>.
      </p>
    );

  const clients = [...new Set((feeds ?? []).map((feed) => feed.client))].sort(
    (a, b) => order(a) - order(b) || a.localeCompare(b),
  );
  const shown = clients.filter((client) => only === null || client === only);

  return (
    <div className="flex flex-col gap-6 pb-8">
      <div className="demo-heading">
        <div>
          <h1>Live streams</h1>
          <p>
            Every client sending frames to the backend in the last minute, by app. Faces are
            blurred before the backend keeps a frame.
          </p>
        </div>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Show streams from">
          {[null, ...clients].map((client) => (
            <button
              key={client ?? "all"}
              type="button"
              aria-pressed={only === client}
              onClick={() => setOnly(client)}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                only === client
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {client === null
                ? `All (${feeds?.length ?? 0})`
                : `${clientInfo(client).label} (${feeds?.filter((f) => f.client === client).length})`}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {feeds === null && !error && <p className="text-sm text-muted-foreground">Loading streams…</p>}
      {feeds?.length === 0 && (
        <p className="rounded-md border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          No client is sending frames. Start the stream on the{" "}
          <Link href="/" className="underline">
            live page
          </Link>
          , in the{" "}
          <Link href="/glasses" className="underline">
            glasses harness
          </Link>{" "}
          or on the glasses, and it appears here.
        </p>
      )}

      {shown.map((client) => {
        const { label, Icon } = clientInfo(client);
        const group = (feeds ?? []).filter((feed) => feed.client === client);
        return (
          <section key={client} aria-labelledby={`client-${client}`} className="flex flex-col gap-3">
            <h2 id={`client-${client}`} className="flex items-center gap-2 text-sm font-medium">
              <Icon size={16} aria-hidden="true" /> {label}
              <span className="text-muted-foreground">· {group.length}</span>
            </h2>
            <ul className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(min(100%,280px),1fr))]">
              {group.map((feed) => (
                <li key={feed.id}>
                  <FeedTile feed={feed} now={now} />
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function FeedTile({ feed, now }: { feed: Feed; now: number }) {
  return (
    <article className="flex flex-col overflow-hidden rounded-lg border border-border bg-card">
      <Link
        href={`/admin/streams/${feed.id}`}
        aria-label={`Open ${feed.user}'s ${KINDS[feed.kind]} in full`}
        className="group relative block aspect-video bg-black"
      >
        <LiveFrame id={feed.id} live={feed.live} alt={`${feed.user}'s ${KINDS[feed.kind]}`} />
        <span
          className={`absolute top-2 left-2 flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${
            feed.live ? "bg-red-600 text-white" : "bg-black/60 text-white/70"
          }`}
        >
          {feed.live && <Circle size={8} className="animate-pulse fill-white" aria-hidden="true" />}
          {feed.live ? "LIVE" : `idle · ${since(feed.last_at, now)}`}
        </span>
        {feed.demo && (
          <span className="absolute top-2 right-2 flex items-center gap-1 rounded-full bg-black/60 px-2 py-0.5 text-[11px] text-white">
            <Film size={11} aria-hidden="true" /> demo clip
          </span>
        )}
        <span className="absolute right-2 bottom-2 rounded bg-black/60 px-2 py-0.5 text-[11px] text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          Open full view ↗
        </span>
      </Link>
      <div className="flex flex-col gap-0.5 p-3 text-xs">
        <p className="truncate text-sm font-medium" title={feed.user}>
          {feed.user}
        </p>
        <p className="text-muted-foreground">
          {KINDS[feed.kind]}
          {feed.live && feed.fps > 0 ? ` · ${feed.fps} fps` : ""} · {feed.frames} frame
          {feed.frames === 1 ? "" : "s"} · since {new Date(feed.started_at).toLocaleTimeString()}
        </p>
        {feed.stream_id && (
          <p className="truncate font-mono text-[11px] text-muted-foreground" title={feed.stream_id}>
            {feed.stream_id}
          </p>
        )}
      </div>
      <EventLog events={feed.events} now={now} />
    </article>
  );
}

/** Under the view: the questions this user sent, what they asked to watch for, and what fired. */
function EventLog({ events, now }: { events: FeedEvent[]; now: number }) {
  return (
    <div className="border-t border-border">
      <p className="px-3 pt-2 text-[11px] font-medium tracking-wide text-muted-foreground">
        SENT TO THE BACKEND
      </p>
      {events.length === 0 ? (
        <p className="px-3 pt-1 pb-3 text-xs text-muted-foreground">
          No question or trigger yet.
        </p>
      ) : (
        <ol className="flex max-h-44 flex-col gap-1.5 overflow-y-auto px-3 pt-1 pb-3" aria-live="polite">
          {events.map((event, index) => {
            const { label, Icon, tone } = EVENTS[event.kind];
            return (
              <li key={`${event.at}-${index}`} className="flex gap-2 text-xs">
                <Icon size={13} className={`mt-0.5 shrink-0 ${tone}`} aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className={`font-medium ${tone}`}>{label}</span>{" "}
                  <span className="break-words">{event.text}</span>
                  <span className="block text-[11px] text-muted-foreground">
                    {since(event.at, now)} · via {event.via}
                  </span>
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/**
 * The feed's newest frame, asked for again FRAME_EVERY_MS after the last one loaded: a slow
 * connection shows fewer frames instead of queueing requests. Frames are fetched as blobs and
 * swapped in once loaded, so the picture never flashes empty.
 */
export function LiveFrame({ id, live, alt }: { id: string; live: boolean; alt: string }) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const next = async () => {
      const started = performance.now();
      try {
        const response = await fetch(`/api/admin/streams/${id}/frame`, { cache: "no-store" });
        if (response.ok && !stopped) setSrc(URL.createObjectURL(await response.blob()));
      } catch {
        // The next try comes anyway; the list says when the feed is gone.
      }
      // An idle feed changes rarely: look again slowly.
      const wait = (live ? FRAME_EVERY_MS : 5_000) - (performance.now() - started);
      if (!stopped) timer = setTimeout(() => void next(), Math.max(0, wait));
    };
    void next();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [id, live]);

  // A frame's blob URL is let go once the <img> shows the next one, or the tile goes.
  useEffect(
    () => () => {
      if (src) URL.revokeObjectURL(src);
    },
    [src],
  );

  return src ? (
    // A plain <img>: a blob URL of the backend's JPEG.
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={alt} className="absolute inset-0 h-full w-full object-contain" />
  ) : (
    <span className="absolute inset-0 flex items-center justify-center text-xs text-white/60">
      Waiting for a frame…
    </span>
  );
}
