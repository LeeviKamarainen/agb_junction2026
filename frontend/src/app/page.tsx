import { io } from "next/cache";
import { Suspense } from "react";

import { SiteShell } from "@/components/site-shell";
import { api } from "@/lib/api/server";

import { Hero } from "./_home/hero";

async function publishedClips() {
  try {
    const { data, error } = await api.GET("/api/demo/clips", { cache: "no-store" });
    if (data) return data;
    console.error("The demo clips could not be loaded", error);
  } catch (problem) {
    console.error("The demo clips could not be loaded", problem);
  }
  return "failed" as const;
}

async function PlayingHero() {
  // The API client builds a Request before it fetches: wait for a real request first.
  await io();
  return <Hero clips={await publishedClips()} />;
}

export default function Home() {
  return (
    <SiteShell>
      <Suspense fallback={<Hero clips="loading" />}>
        <PlayingHero />
      </Suspense>
    </SiteShell>
  );
}
