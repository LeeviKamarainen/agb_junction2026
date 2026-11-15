"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Pause,
  Play,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { components } from "@/lib/api/schema";

import { CueOverlay } from "@/components/glasses-view/cue-overlay";
import { clock } from "@/components/glasses-view/cues";

type Clip = components["schemas"]["ShowcasePublic"];

export function ShowcasePlayer({ clips }: { clips: Clip[] }) {
  const [playback, setPlayback] = useState({ position: 0, playing: false });
  const [muted, setMuted] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const sequence = clips
    .filter((c) => c.assets.length)
    .reduce<
      Array<{ clip: Clip; start: number; duration: number; end: number }>
    >((items, clip) => {
      const duration =
        clip.kind === "images"
          ? clip.assets.length * clip.image_seconds
          : clip.duration_s;
      const start = items.at(-1)?.end ?? 0;
      return [...items, { clip, start, duration, end: start + duration }];
    }, []);
  const total = sequence.at(-1)?.end ?? 0;
  const current =
    sequence.find((c) => playback.position < c.end) ?? sequence.at(-1);
  const id = current?.clip.id;
  const kind = current?.clip.kind;
  const start = current?.start ?? 0;
  const local = playback.position - start;

  // Synchronize only when switching chapters or play state; timeupdate drives the clock.
  const positionRef = useRef(playback.position);
  useEffect(() => {
    positionRef.current = playback.position;
  }, [playback.position]);
  useEffect(() => {
    const element = video.current;
    if (!element || kind !== "video") return;
    element.currentTime = Math.max(0, positionRef.current - start);
    if (playback.playing)
      void element.play().catch(() => {
        setError(
          "Playback could not start. Try Play again or choose another demo.",
        );
        setPlayback((p) => ({ ...p, playing: false }));
      });
    else element.pause();
    return () => element.pause();
  }, [id, kind, start, playback.playing]);

  useEffect(() => {
    if (!playback.playing || kind !== "images") return;
    let previous = performance.now();
    const timer = setInterval(() => {
      const now = performance.now();
      const delta = (now - previous) / 1000;
      previous = now;
      setPlayback((p) => {
        const position = Math.min(total, p.position + delta);
        return { position, playing: position < total };
      });
    }, 100);
    return () => clearInterval(timer);
  }, [id, kind, playback.playing, total]);

  function seek(position: number) {
    setError(null);
    const bounded = Math.max(0, Math.min(total, position));
    setPlayback((p) => ({
      position: bounded,
      playing: p.playing && bounded < total,
    }));
    if (
      video.current &&
      current &&
      bounded >= current.start &&
      bounded < current.end
    )
      video.current.currentTime = bounded - current.start;
  }
  function mediaError() {
    setError(
      "This media could not be played. Choose another chapter or reload the page.",
    );
    setPlayback((p) => ({ ...p, playing: false }));
  }
  function finish() {
    if (current)
      setPlayback((p) => ({
        position: current.end,
        playing: p.playing && current.end < total,
      }));
  }

  if (!current)
    return (
      <div className="rounded-md border border-dashed border-border p-8 text-sm text-muted-foreground">
        No demos have been published yet. The admin can upload videos or image
        sequences in{" "}
        <Link className="underline" href="/admin">
          Manage demos
        </Link>
        .
      </div>
    );
  const activeIndex = sequence.indexOf(current);
  const asset =
    current.clip.assets[
      Math.min(
        current.clip.assets.length - 1,
        Math.floor(Math.max(0, local) / current.clip.image_seconds),
      )
    ];

  return (
    <section aria-label="Curated demo playback">
      <div className="showcase-grid">
        <div className="min-w-0">
          <div className="showcase-media relative">
            {current.clip.kind === "video" ? (
              <video
                key={id}
                ref={video}
                src={current.clip.assets[0].url}
                muted={muted}
                playsInline
                preload="metadata"
                onLoadedMetadata={() => {
                  if (video.current)
                    video.current.currentTime = Math.max(
                      0,
                      positionRef.current - start,
                    );
                }}
                onTimeUpdate={(event) => {
                  const time = event.currentTarget.currentTime;
                  if (time >= current.duration) finish();
                  else
                    setPlayback((p) => ({
                      ...p,
                      position: current.start + time,
                    }));
                }}
                onEnded={finish}
                onError={mediaError}
                aria-label={current.clip.title}
              />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element -- public uploaded demo asset
              <img
                key={asset.name}
                src={asset.url}
                alt={asset.label}
                onError={mediaError}
              />
            )}
            <CueOverlay cues={current.clip.cues} at={local} />
          </div>
          <div className="flex items-start justify-between gap-4 py-3">
            <div>
              <h2 className="text-base font-medium">{current.clip.title}</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {current.clip.description}
              </p>
            </div>
            <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">
              {current.clip.kind === "video"
                ? "VIDEO"
                : `IMAGE ${Math.min(current.clip.assets.length, Math.floor(local / current.clip.image_seconds) + 1)} / ${current.clip.assets.length}`}
            </span>
          </div>
        </div>
        <aside className="showcase-queue" aria-label="Demo chapters">
          <p className="mb-2 text-xs text-muted-foreground">In this sequence</p>
          {sequence.map((entry, index) => (
            <button
              key={entry.clip.id}
              type="button"
              aria-current={entry === current}
              onClick={() => seek(entry.start)}
            >
              <span className="font-mono text-xs text-primary">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0">
                <span className="block break-words text-sm">
                  {entry.clip.title}
                </span>
                <span className="text-xs text-muted-foreground">
                  {entry.clip.kind === "video"
                    ? "Video"
                    : `${entry.clip.assets.length} images`}{" "}
                  · {clock(entry.duration)}
                </span>
              </span>
            </button>
          ))}
        </aside>
      </div>
      <div className="showcase-chapters" aria-label="Timeline chapters">
        {sequence.map((entry) => (
          <button
            key={entry.clip.id}
            type="button"
            style={{ flexGrow: entry.duration, flexBasis: 0 }}
            aria-current={entry === current}
            onClick={() => seek(entry.start)}
          >
            {entry.clip.title}
          </button>
        ))}
      </div>
      <label
        className="block text-xs text-muted-foreground"
        htmlFor="demo-position"
      >
        Shared timeline · {clock(playback.position)} / {clock(total)}
      </label>
      <input
        id="demo-position"
        type="range"
        min={0}
        max={total}
        step={0.1}
        value={playback.position}
        onChange={(event) => seek(Number(event.target.value))}
        className="mt-2 w-full accent-primary"
      />
      <div className="showcase-transport">
        <Button
          onClick={() => {
            setError(null);
            setPlayback((p) => ({
              position: p.position >= total ? 0 : p.position,
              playing: !p.playing,
            }));
          }}
        >
          {playback.playing ? (
            <Pause aria-hidden="true" />
          ) : (
            <Play aria-hidden="true" />
          )}
          {playback.playing
            ? "Pause"
            : playback.position >= total
              ? "Replay"
              : "Play"}
        </Button>
        <Button
          variant="outline"
          size="icon"
          aria-label="Previous demo"
          disabled={activeIndex === 0}
          onClick={() => seek(sequence[activeIndex - 1].start)}
        >
          <SkipBack aria-hidden="true" />
        </Button>
        <Button
          variant="outline"
          size="icon"
          aria-label="Next demo"
          disabled={activeIndex === sequence.length - 1}
          onClick={() => seek(sequence[activeIndex + 1].start)}
        >
          <SkipForward aria-hidden="true" />
        </Button>
        {kind === "video" ? (
          <Button
            variant="ghost"
            aria-label={muted ? "Unmute video" : "Mute video"}
            onClick={() => setMuted(!muted)}
          >
            {muted ? (
              <VolumeX aria-hidden="true" />
            ) : (
              <Volume2 aria-hidden="true" />
            )}
          </Button>
        ) : null}
        <Link href="/" className="ml-auto text-sm text-primary">
          Ask your day ↗
        </Link>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </section>
  );
}
