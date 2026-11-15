import { Suspense } from "react";
import type { Metadata } from "next";
import { io } from "next/cache";
import { api } from "@/lib/api/server";
import { SiteShell } from "@/components/site-shell";
import { ShowcasePlayer } from "./showcase-player";

export const metadata: Metadata = { title: "Demos — Glass memory" };
async function Demos() {
  // The API client constructs a Request before fetch; defer that work past prerendering.
  await io();
  const response = await api
    .GET("/api/demo/clips", { cache: "no-store" })
    .catch(() => null);
  if (!response?.data)
    return (
      <p role="alert" className="text-sm text-destructive">
        Demos could not be loaded. Refresh to try again.
      </p>
    );
  return <ShowcasePlayer clips={response.data} />;
}
export default function DemoPage() {
  return (
    <SiteShell>
      <div className="demo-heading">
        <div>
          <h1>A day through the glasses</h1>
          <p>A curated sequence of videos and captured moments.</p>
        </div>
        <span className="font-mono text-xs text-muted-foreground">
          JUNCTION / 2026
        </span>
      </div>
      <Suspense
        fallback={
          <p role="status" className="py-12 text-sm text-muted-foreground">
            Loading demos…
          </p>
        }
      >
        <Demos />
      </Suspense>
    </SiteShell>
  );
}
