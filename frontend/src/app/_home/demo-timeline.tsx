"use client";

import {
  ChevronLeft,
  ChevronRight,
  Glasses,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";

type Timeline = components["schemas"]["DemoTimeline"];
type Moment = components["schemas"]["DemoMoment"];

/** How long each moment stays on screen while playing. */
const STEP_MS = 5000;
const TICK_MS = 100;

function clock(iso: string): string {
  // The dataset's own time of day (its offset), not the viewer's time zone.
  return iso.slice(11, 16);
}

function day(iso: string): string {
  return new Date(iso.slice(0, 10) + "T12:00:00").toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/**
 * The demo dataset as the glasses saw it, earliest first, played back one moment at a time and
 * looping at the end. Anyone can watch it; no login needed.
 */
export function DemoTimeline() {
  const [timeline, setTimeline] = useState<Timeline | null | undefined>();
  // Together, so one tick moves both without side effects in a state updater.
  const [{ current, elapsed }, setPosition] = useState({
    current: 0,
    elapsed: 0,
  });
  const [playing, setPlaying] = useState(true);
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/api/demo/timeline")
      .then(({ data }) => !cancelled && setTimeline(data ?? null))
      .catch(() => !cancelled && setTimeline(null));
    return () => {
      cancelled = true;
    };
  }, []);

  const count = timeline?.moments.length ?? 0;

  useEffect(() => {
    if (!playing || count === 0) return;
    const timer = setInterval(() => {
      setPosition((at) =>
        at.elapsed + TICK_MS < STEP_MS
          ? { current: at.current, elapsed: at.elapsed + TICK_MS }
          : { current: (at.current + 1) % count, elapsed: 0 },
      );
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [playing, count]);

  // Keep the highlighted moment in view inside the list, without scrolling the page.
  useEffect(() => {
    const box = list.current;
    const item = box?.querySelector<HTMLElement>(`[data-index="${current}"]`);
    if (!box || !item) return;
    box.scrollTo({
      top: item.offsetTop - box.clientHeight / 2 + item.clientHeight / 2,
      behavior: "smooth",
    });
  }, [current]);

  function go(index: number) {
    if (!count) return;
    setPosition({ current: (index + count) % count, elapsed: 0 });
  }

  if (timeline === undefined)
    return (
      <div
        className="flex min-h-80 items-center justify-center gap-2 rounded-xl border border-border bg-card text-sm text-muted-foreground"
        role="status"
      >
        <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />
        Loading the day…
      </div>
    );

  if (timeline === null || count === 0)
    return (
      <div
        className="flex min-h-80 flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card p-6 text-center text-sm text-muted-foreground"
        role="status"
      >
        <p>The timeline couldn’t be loaded. Is the backend running?</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => window.location.reload()}
        >
          <RotateCcw aria-hidden="true" /> Try again
        </Button>
      </div>
    );

  const moment = timeline.moments[current];
  const progress = playing ? elapsed / STEP_MS : 0;

  return (
    <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
      <figure className="relative flex min-h-80 flex-col justify-between overflow-hidden rounded-xl border border-border bg-[radial-gradient(120%_90%_at_20%_0%,#2a1f19_0%,#141512_55%,#0d0e0c_100%)] p-5 md:p-7">
        <div className="flex items-center justify-between font-mono text-[11px] tracking-wider text-muted-foreground">
          <span className="inline-flex items-center gap-2">
            <Glasses size={15} className="text-primary" aria-hidden="true" />
            GLASSES VIEW · {current + 1}/{count}
          </span>
          <span>{day(moment.time).toUpperCase()}</span>
        </div>

        <div
          key={moment.index}
          className="demo-moment flex flex-col gap-3 py-8"
        >
          <p className="flex items-baseline gap-3">
            <span className="font-mono text-2xl font-semibold tracking-tight text-primary md:text-3xl">
              {clock(moment.time)}
            </span>
            <span className="text-lg font-medium">{moment.place}</span>
          </p>
          <figcaption className="max-w-2xl text-base leading-snug text-balance md:text-lg">
            {moment.text}
          </figcaption>
        </div>

        <div className="flex flex-col gap-3">
          <div
            className="h-1 overflow-hidden rounded-full bg-secondary"
            aria-hidden="true"
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-100 ease-linear"
              style={{ width: `${((current + progress) / count) * 100}%` }}
            />
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              onClick={() => go(current - 1)}
              aria-label="Previous moment"
            >
              <ChevronLeft aria-hidden="true" />
            </Button>
            <Button
              size="icon"
              onClick={() => setPlaying((on) => !on)}
              aria-label={playing ? "Pause" : "Play"}
            >
              {playing ? (
                <Pause aria-hidden="true" />
              ) : (
                <Play aria-hidden="true" />
              )}
            </Button>
            <Button
              variant="outline"
              size="icon"
              onClick={() => go(current + 1)}
              aria-label="Next moment"
            >
              <ChevronRight aria-hidden="true" />
            </Button>
            <span className="ml-auto font-mono text-[11px] text-muted-foreground">
              {playing
                ? `NEXT IN ${Math.ceil((STEP_MS - elapsed) / 1000)} S · LOOPS`
                : "PAUSED"}
            </span>
          </div>
        </div>
      </figure>

      <ol
        ref={list}
        className="relative flex max-h-[28rem] flex-col overflow-y-auto rounded-xl border border-border bg-card p-2"
        aria-label="The day, earliest first"
      >
        {timeline.moments.map((item: Moment, index) => {
          const active = index === current;
          return (
            <li key={item.index} data-index={index}>
              <button
                type="button"
                onClick={() => go(index)}
                aria-current={active ? "step" : undefined}
                className={`group grid w-full grid-cols-[3.25rem_0.75rem_1fr] items-start gap-2 rounded-lg px-2 py-2 text-left transition-colors ${
                  active ? "bg-secondary" : "hover:bg-secondary/60"
                }`}
              >
                <span
                  className={`font-mono text-xs leading-5 ${active ? "text-primary" : "text-muted-foreground"}`}
                >
                  {clock(item.time)}
                </span>
                <span
                  className={`mt-1.5 size-2.5 rounded-full border ${
                    active
                      ? "border-primary bg-primary"
                      : index < current
                        ? "border-primary/60 bg-primary/30"
                        : "border-border bg-background"
                  }`}
                  aria-hidden="true"
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">
                    {item.place}
                  </span>
                  <span
                    className={`block text-xs text-muted-foreground ${active ? "" : "line-clamp-1"}`}
                  >
                    {item.text}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
