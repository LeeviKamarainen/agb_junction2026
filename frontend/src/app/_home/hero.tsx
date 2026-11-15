"use client";

import {
  ArrowRight,
  BellRing,
  Eye,
  Play,
  ScanEye,
  ShieldCheck,
  Volume2,
  VolumeX,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { say } from "@/app/glasses/speech";
import { cuesEnd, cueView } from "@/components/glasses-view/cue-screens";
import { clock } from "@/components/glasses-view/cues";
import { GlassesView } from "@/components/glasses-view/glasses-view";
import type { components } from "@/lib/api/schema";

import "./home.css";

type Clip = components["schemas"]["ShowcasePublic"];

const TICK_MS = 100; // the display follows the clip to a tenth of a second

/**
 * The home page: the product name and pitch, a published demo clip playing as the wearer saw
 * it (the glasses' display driven by the clip's recorded cues), a picker for the other clips
 * and the way to try it.
 */
export function Hero({ clips }: { clips: Clip[] | "loading" | "failed" }) {
  const playable = Array.isArray(clips) ? clips.filter((clip) => clip.assets.length) : [];
  // `run` restarts a clip picked again.
  const [choice, setChoice] = useState({ index: 0, run: 0 });
  const [sound, setSound] = useState(false);
  const clip = playable.at(choice.index);

  const note =
    clips === "failed"
      ? "The demo clips could not be loaded. Refresh to try again."
      : Array.isArray(clips) && !playable.length
        ? "No demo clips yet."
        : null;

  return (
    <section className="home" aria-labelledby="home-title">
      <div className="home-text">
        <div className="home-lead">
          <h1 id="home-title">GlassMemory</h1>
          <p className="home-pitch">
            Give your Meta glasses a private visual memory and real-time notifications
            triggered from visual cues.
          </p>
          <Link className="home-cta" href={clip ? `/try?clip=${clip.id}` : "/try"}>
            Try it out <ArrowRight aria-hidden="true" />
          </Link>
        </div>
        <div className="home-more">
          <ul className="home-caps">
            <li>
              <ScanEye aria-hidden="true" />
              <p>
                <strong>Recall</strong> Ask about anything you&apos;ve seen, and get the answer
                with the visual evidence.
              </p>
            </li>
            <li>
              <BellRing aria-hidden="true" />
              <p>
                <strong>Remind</strong> Tell it what to watch for, and it notifies you when it
                appears.
              </p>
            </li>
          </ul>
          <p className="home-trust">
            <ShieldCheck aria-hidden="true" />
            Faces blurred before processing · self-hosted models, no third parties
          </p>
        </div>
      </div>

      <div className="home-demo">
        {clip ? (
          <ClipPlayer key={`${clip.id}:${choice.run}`} clip={clip} sound={sound} />
        ) : (
          <GlassesView
            className="home-view"
            screen={null}
            overlay={note ? <p className="home-note">{note}</p> : null}
          >
            {null}
          </GlassesView>
        )}
        {playable.length ? (
          <div className="home-controls">
            <div className="home-clips" role="group" aria-label="Demo clips">
              {playable.map((item, index) => (
                <button
                  key={item.id}
                  type="button"
                  className="home-clip"
                  aria-pressed={item === clip}
                  onClick={() => setChoice(({ run }) => ({ index, run: run + 1 }))}
                >
                  {item.title}
                  <span className="home-clip-length">{clock(clipLength(item))}</span>
                </button>
              ))}
            </div>
            <button
              type="button"
              className="home-sound"
              aria-pressed={sound}
              onClick={() => {
                // Safari on an iPhone only lets a page speak once it has spoken during a tap.
                if (!sound) say("");
                setSound((on) => !on);
              }}
            >
              {sound ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}
              Sound
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

const clipLength = (clip: Clip) =>
  clip.kind === "images" ? clip.assets.length * clip.image_seconds : clip.duration_s;

/**
 * One clip on a loop, its cues on the glasses' display. A clip that ends before its last cue
 * has been shown holds its last frame until then. With the sound on, the clip's own audio
 * plays and each answer or trigger is read out once as it appears.
 */
function ClipPlayer({ clip, sound }: { clip: Clip; sound: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const [view, setView] = useState(() => cueView(clip.cues, 0));
  const [image, setImage] = useState(0);
  const [trouble, setTrouble] = useState<Trouble>(null);

  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = !sound;
  }, [sound]);

  useEffect(() => {
    const video = videoRef.current;
    if (video) void play(video).then(setTrouble);
  }, []);

  // The clip's clock: the video's own time while it plays, then the hold on its last frame; an
  // image sequence runs on the clock alone, each image for its image_seconds.
  useEffect(() => {
    const end = cuesEnd(clip.cues);
    const images = clip.assets.length * clip.image_seconds;
    let started = performance.now();
    let endedAt: number | null = null;
    const timer = setInterval(() => {
      let at: number;
      if (clip.kind === "images") {
        at = (performance.now() - started) / 1000;
        if (at >= Math.max(images, end)) {
          started = performance.now();
          at = 0;
        }
        setImage(Math.min(clip.assets.length - 1, Math.floor(at / clip.image_seconds)));
      } else {
        const video = videoRef.current;
        if (!video) return;
        // The clip ends where its video does, or sooner where the admin trimmed it (duration_s).
        // The video's own length is NaN until its metadata has loaded.
        const stop = Math.min(Number.isNaN(video.duration) ? Infinity : video.duration, clip.duration_s);
        if (!video.ended && video.currentTime < stop) {
          endedAt = null;
          at = video.currentTime;
        } else {
          if (!video.paused) video.pause();
          endedAt ??= performance.now();
          at = stop + (performance.now() - endedAt) / 1000;
          if (at >= end) {
            endedAt = null;
            at = 0;
            video.currentTime = 0;
            void play(video).then(setTrouble);
          }
        }
      }
      const next = cueView(clip.cues, at);
      setView((shown) => (shown.key === next.key ? shown : next));
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [clip]);

  // Read each answer and trigger out once, as it appears; stop reading when the sound goes off.
  const lastShown = useRef<string | null>(null);
  useEffect(() => {
    if (view.shown === lastShown.current) return;
    lastShown.current = view.shown;
    if (sound && view.speech) say(view.speech);
  }, [view, sound]);
  useEffect(() => {
    if (!sound) return;
    return () => {
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    };
  }, [sound]);

  const asset = clip.assets[image];
  return (
    <GlassesView
      className="home-view"
      media={clip.kind === "images" ? imageRef : videoRef}
      // Nothing plays while there is trouble, so the display shows nothing either.
      screen={trouble ? null : view.screen}
      listening={!trouble && view.listening}
      status={
        view.watching.length
          ? view.watching.map((cue) => (
              <span key={cue.id}>
                <Eye aria-hidden="true" />
                <span className="home-watching">Watching</span>
                <span>{cue.text}</span>
              </span>
            ))
          : null
      }
      overlay={
        trouble === "failed" ? (
          <p className="home-note">This clip could not be played.</p>
        ) : trouble === "blocked" ? (
          <button
            type="button"
            className="home-play"
            onClick={() => {
              const video = videoRef.current;
              if (video) void play(video).then(setTrouble);
            }}
          >
            <Play aria-hidden="true" /> Play
          </button>
        ) : null
      }
    >
      {clip.kind === "images" ? (
        // A plain <img>: the backend serves the demo images as they are.
        // eslint-disable-next-line @next/next/no-img-element
        <img ref={imageRef} src={asset.url} alt={asset.label} onError={() => setTrouble("failed")} />
      ) : (
        <video
          ref={videoRef}
          src={clip.assets[0].url}
          playsInline
          preload="auto"
          aria-label={clip.title}
          onError={() => setTrouble("failed")}
        />
      )}
    </GlassesView>
  );
}

/** Autoplay refused, until a tap on Play; or the clip cannot be played at all. */
type Trouble = "blocked" | "failed" | null;

/** Plays the video, and says what stands in the way, if anything. */
async function play(video: HTMLVideoElement): Promise<Trouble> {
  try {
    await video.play();
    return null;
  } catch (problem) {
    if (problem instanceof DOMException && problem.name === "NotAllowedError") return "blocked";
    // A newer load or a pause interrupted it: nothing is wrong.
    if (problem instanceof DOMException && problem.name === "AbortError") return null;
    console.error("The demo clip could not be played", problem);
    return "failed";
  }
}
