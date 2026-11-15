"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, LoaderCircle, Trash2 } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";
import { useSession } from "@/lib/session";
import { Button } from "@/components/ui/button";
import { AuthForm } from "../login/auth-form";
import { CueEditor } from "./cue-editor";
import type { Cue } from "@/components/glasses-view/cues";

type Clip = components["schemas"]["ShowcasePublic"];
// How often the list is fetched again while a video is being prepared: it takes about as long as
// the video plays, so a few seconds late is soon enough.
const PREPARING_POLL_MS = 5000;
const field =
  "w-full rounded border border-border bg-background px-3 py-2 text-sm";
function message(error: unknown): string {
  if (
    error &&
    typeof error === "object" &&
    "detail" in error &&
    typeof error.detail === "string"
  )
    return error.detail;
  return "The change could not be saved. Check your connection and try again.";
}
function write(clip: Clip) {
  return {
    title: clip.title,
    description: clip.description,
    kind: clip.kind as "video" | "images",
    duration_s: clip.duration_s,
    image_seconds: clip.image_seconds,
    position: clip.position,
    published: clip.published,
    asset_order: clip.assets.map((a) => a.name),
    cues: clip.cues.filter((cue) => cue.text.trim()),
  };
}

/** An editor's cue with the fields the API fills in by default. */
const whole = (cue: Cue): Clip["cues"][number] => ({
  ...cue,
  answer: cue.answer ?? "",
  kind: cue.kind ?? "prompt",
});

/** Download a demo's prompts and recorded answers as a JSON file. */
function exportCues(clip: Clip) {
  const data = {
    title: clip.title,
    duration_s: clip.duration_s,
    cues: clip.cues,
  };
  const link = document.createElement("a");
  link.href = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  link.download = `${clip.title.replace(/[^\w-]+/g, "-") || "demo"}-prompts.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function duration(file: File): Promise<number | null> {
  const url = URL.createObjectURL(file);
  const video = document.createElement("video");
  return new Promise((resolve) => {
    const finish = (value: number | null) => {
      clearTimeout(timer);
      video.onloadedmetadata = null;
      video.onerror = null;
      video.removeAttribute("src");
      video.load();
      URL.revokeObjectURL(url);
      resolve(value);
    };
    const timer = setTimeout(() => finish(null), 5000);
    video.onloadedmetadata = () =>
      finish(Number.isFinite(video.duration) ? video.duration : null);
    video.onerror = () => finish(null);
    video.preload = "metadata";
    video.src = url;
  });
}

export function DemoManager() {
  const { user } = useSession();
  const [clips, setClips] = useState<Clip[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<"video" | "images">("video");
  const load = useCallback(async () => {
    try {
      const { data, error } = await api.GET("/api/demo/manage");
      if (!data) throw new Error(message(error));
      setClips(data);
      setLoaded(true);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : message(e));
    }
  }, []);
  useEffect(() => {
    // Loading the protected list once the viewer is known is the purpose of this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (user?.role === "admin") void load();
  }, [load, user?.role]);
  // An uploaded video becomes playable by itself once the backend has blurred its faces and
  // re-encoded it: look again until none is left preparing.
  const preparing = clips.some((clip) => clip.converting);
  useEffect(() => {
    if (!preparing) return;
    const timer = setInterval(() => void load(), PREPARING_POLL_MS);
    return () => clearInterval(timer);
  }, [preparing, load]);
  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { data, error } = await api.POST("/api/demo/clips", {
        body: {
          title: title.trim(),
          kind,
          position: clips.length,
          description: "",
          duration_s: 30,
          image_seconds: 3,
          published: false,
        },
      });
      if (!data) throw new Error(message(error));
      setTitle("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : message(e));
    } finally {
      setBusy(false);
    }
  }
  async function move(index: number, delta: number) {
    const next = [...clips];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    setBusy(true);
    setError(null);
    try {
      const { data, error } = await api.POST("/api/demo/order", {
        body: { ids: next.map((c) => c.id) },
      });
      if (!data) throw new Error(message(error));
      setClips(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : message(e));
    } finally {
      setBusy(false);
    }
  }
  if (user === undefined)
    return (
      <p role="status" className="py-8 text-sm">
        Checking login…
      </p>
    );
  if (!user)
    return (
      <div className="flex justify-center py-8">
        <AuthForm admin redirectTo="/admin" />
      </div>
    );
  if (user.role !== "admin")
    return (
      <p role="alert" className="py-8 text-sm">
        Demo editing is available to the admin.{" "}
        <Link href="/" className="underline">
          Watch the demos
        </Link>{" "}
        or log out to use an admin login.
      </p>
    );
  return (
    <div className="flex flex-col gap-5 pb-8">
      <div className="demo-heading">
        <div>
          <h1>Manage demos</h1>
          <p>
            Upload media, arrange the sequence, then publish it on Try it.
          </p>
        </div>
        <Link href="/try" className="text-sm text-primary">
          View Try it ↗
        </Link>
      </div>
      <form
        onSubmit={create}
        className="grid items-end gap-3 rounded-md border border-border bg-card p-4 sm:grid-cols-[1fr_180px_auto]"
      >
        <label className="text-sm">
          Demo title
          <input
            className={field}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={100}
            placeholder="Finding the keys"
            disabled={busy}
          />
        </label>
        <label className="text-sm">
          Media type
          <select
            className={field}
            value={kind}
            onChange={(e) => setKind(e.target.value as "video" | "images")}
            disabled={busy}
          >
            <option value="video">Video (MP4 / WebM)</option>
            <option value="images">Image sequence</option>
          </select>
        </label>
        <Button type="submit" disabled={busy || !title.trim()}>
          {busy ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : null}
          Add draft
        </Button>
      </form>
      <p className="text-xs text-muted-foreground">
        Publishing a demo also makes it searchable: its frames are stored as
        demo memories, which questions about it search.
      </p>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}{" "}
          <button onClick={() => void load()} className="underline">
            Retry
          </button>
        </p>
      ) : null}
      {!loaded && !error ? <p role="status">Loading demos…</p> : null}
      {loaded && !clips.length ? (
        <p className="text-sm text-muted-foreground">
          No demos yet. Add a draft above to start.
        </p>
      ) : null}
      {clips.map((clip, index) => (
        <section
          key={clip.id}
          className="rounded-md border border-border bg-card p-4"
        >
          <div className="mb-4 flex items-center justify-between">
            <span className="font-mono text-xs text-muted-foreground">
              {String(index + 1).padStart(2, "0")} /{" "}
              {clip.kind === "video" ? "VIDEO" : "IMAGE SEQUENCE"}
            </span>
            <div className="flex gap-1">
              <Button
                size="icon-sm"
                variant="ghost"
                disabled={busy || index === 0}
                aria-label={`Move ${clip.title} earlier`}
                onClick={() => void move(index, -1)}
              >
                <ArrowUp aria-hidden="true" />
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                disabled={busy || index === clips.length - 1}
                aria-label={`Move ${clip.title} later`}
                onClick={() => void move(index, 1)}
              >
                <ArrowDown aria-hidden="true" />
              </Button>
            </div>
          </div>
          <ClipEditor
            key={clip.id}
            clip={clip}
            refresh={load}
            disabled={busy}
            onBusy={setBusy}
          />
        </section>
      ))}
    </div>
  );
}

function ClipEditor({
  clip,
  refresh,
  disabled,
  onBusy,
}: {
  clip: Clip;
  refresh: () => Promise<void>;
  disabled: boolean;
  onBusy: (busy: boolean) => void;
}) {
  const [draft, setDraft] = useState(clip);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blocked = busy || disabled;
  // The video's preparation, from the latest list rather than the draft, which keeps the admin's
  // unsaved edits: until it is done the video cannot be played, nor a draft published.
  const preparing = clip.kind === "video" && clip.converting;
  const failed = clip.kind === "video" ? (clip.conversion_error ?? null) : null;
  const unready = preparing || failed !== null;
  const patch = (change: Partial<Clip>) =>
    setDraft((d) => ({ ...d, ...change }));
  async function save() {
    const { data, error } = await api.PUT("/api/demo/clips/{id}", {
      params: { path: { id: clip.id } },
      body: write({ ...draft, position: clip.position }),
    });
    if (!data) throw new Error(message(error));
    setDraft(data);
    return data;
  }
  async function task(work: () => Promise<unknown>) {
    setBusy(true);
    onBusy(true);
    setError(null);
    try {
      await work();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : message(e));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  async function upload(files: File[]) {
    if (!files.length) return;
    await task(async () => {
      const seconds = draft.kind === "video" ? await duration(files[0]) : null;
      await save();
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      if (seconds && seconds <= 3600)
        body.append("video_duration_s", String(seconds));
      const response = await fetch(`/api/demo/clips/${clip.id}/media`, {
        method: "POST",
        body,
      });
      if (!response.ok)
        throw new Error(message(await response.json().catch(() => null)));
      setDraft((await response.json()) as Clip);
    });
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          Title
          <input
            className={field}
            value={draft.title}
            maxLength={100}
            required
            disabled={blocked}
            onChange={(e) => patch({ title: e.target.value })}
          />
        </label>
        <label className="text-sm">
          {draft.kind === "video"
            ? "Video duration (seconds)"
            : "Seconds per image"}
          <input
            className={field}
            type="number"
            min={draft.kind === "video" ? 0.1 : 0.25}
            max={draft.kind === "video" ? 3600 : 60}
            step="0.1"
            value={
              draft.kind === "video" ? draft.duration_s : draft.image_seconds
            }
            disabled={blocked}
            onChange={(e) =>
              patch(
                draft.kind === "video"
                  ? { duration_s: Number(e.target.value) }
                  : { image_seconds: Number(e.target.value) },
              )
            }
          />
        </label>
      </div>
      <label className="text-sm">
        Description
        <textarea
          className={field}
          value={draft.description}
          maxLength={1000}
          disabled={blocked}
          onChange={(e) => patch({ description: e.target.value })}
        />
      </label>
      <label className="text-sm">
        {draft.kind === "video"
          ? "Upload or replace video (up to 200 MB)"
          : "Add images (up to 100, 15 MB each)"}
        <input
          className={`${field} mt-1`}
          type="file"
          accept={draft.kind === "video" ? "video/mp4,video/webm" : "image/*"}
          multiple={draft.kind === "images"}
          disabled={blocked}
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            event.target.value = "";
            void upload(files);
          }}
        />
      </label>
      {preparing ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          Blurring faces and preparing the video for the web: about as long as
          it plays.
        </p>
      ) : failed ? (
        <p role="alert" className="text-sm text-destructive">
          The video could not be prepared ({failed}). Upload it again.
        </p>
      ) : null}
      <ol className="flex max-h-80 flex-col gap-2 overflow-y-auto">
        {draft.assets.map((asset, index) => (
          <li
            key={asset.name}
            className="flex min-w-0 items-center gap-2 border-b border-border py-2"
          >
            {draft.kind === "images" ? (
              // eslint-disable-next-line @next/next/no-img-element -- admin preview of an uploaded draft
              <img
                src={asset.url}
                alt=""
                className="size-12 shrink-0 object-cover"
                loading="lazy"
              />
            ) : null}
            <span className="min-w-0 flex-1 truncate text-xs">
              {index + 1}. {asset.label}
            </span>
            {draft.kind === "images" ? (
              <>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  disabled={blocked || index === 0}
                  aria-label={`Move image ${index + 1} earlier`}
                  onClick={() => {
                    const assets = [...draft.assets];
                    [assets[index], assets[index - 1]] = [
                      assets[index - 1],
                      assets[index],
                    ];
                    patch({ assets });
                  }}
                >
                  <ArrowUp aria-hidden="true" />
                </Button>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  disabled={blocked || index === draft.assets.length - 1}
                  aria-label={`Move image ${index + 1} later`}
                  onClick={() => {
                    const assets = [...draft.assets];
                    [assets[index], assets[index + 1]] = [
                      assets[index + 1],
                      assets[index],
                    ];
                    patch({ assets });
                  }}
                >
                  <ArrowDown aria-hidden="true" />
                </Button>
              </>
            ) : null}
            <Button
              size="icon-sm"
              variant="ghost"
              disabled={blocked}
              aria-label={`Remove ${asset.label}`}
              onClick={() =>
                void task(async () => {
                  await save();
                  const response = await api.DELETE(
                    "/api/demo/clips/{id}/media/{name}",
                    { params: { path: { id: clip.id, name: asset.name } } },
                  );
                  if (!response.data) throw new Error(message(response.error));
                  setDraft(response.data);
                })
              }
            >
              <Trash2 aria-hidden="true" />
            </Button>
          </li>
        ))}
      </ol>
      <CueEditor
        cues={draft.cues}
        videoUrl={
          draft.kind === "video" && !unready ? draft.assets[0]?.url : undefined
        }
        disabled={blocked}
        onChange={(cues) => patch({ cues: cues.map(whole) })}
        onRecord={(update) =>
          setDraft((d) => ({ ...d, cues: update(d.cues).map(whole) }))
        }
        onRecorded={() => void task(async () => exportCues(await save()))}
        onExport={() => exportCues(draft)}
        onEndAt={(duration_s) => patch({ duration_s })}
      />
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.published}
            disabled={
              blocked || !draft.assets.length || (unready && !clip.published)
            }
            onChange={(e) => patch({ published: e.target.checked })}
          />
          Published
        </label>
        <Button
          disabled={blocked || !draft.title.trim()}
          onClick={() => void task(save)}
        >
          {busy ? "Saving…" : "Save changes"}
        </Button>
        <Button
          variant="ghost"
          disabled={blocked}
          onClick={() => {
            if (
              window.confirm(`Delete “${clip.title}” and its uploaded media?`)
            )
              void task(async () => {
                const response = await api.DELETE("/api/demo/clips/{id}", {
                  params: { path: { id: clip.id } },
                });
                if (!response.response.ok)
                  throw new Error(message(response.error));
              });
          }}
        >
          Delete demo
        </Button>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
