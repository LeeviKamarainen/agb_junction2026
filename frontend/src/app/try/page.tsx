import type { Metadata } from "next";
import { Suspense } from "react";

import { SiteShell } from "@/components/site-shell";

import "../glasses/glasses.css";
import { TryView } from "./try-view";

export const metadata: Metadata = {
  title: "Try it — GlassMemory",
  description: "Watch a demo or your own camera through the glasses and ask what they have seen.",
};

export default function TryPage() {
  return (
    <SiteShell>
      {/* The clip to start with comes from ?clip=, known only in the browser. */}
      <Suspense fallback={null}>
        <TryView />
      </Suspense>
    </SiteShell>
  );
}
