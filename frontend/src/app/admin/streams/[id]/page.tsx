import type { Metadata } from "next";
import { Suspense } from "react";

import { SiteShell } from "@/components/site-shell";
import { StreamDetail } from "./stream-detail";

export const metadata: Metadata = {
  title: "Stream — Recall/Remind",
  robots: { index: false },
};

export default function StreamPage() {
  return (
    <SiteShell>
      {/* The stream's id is only known per request: the rest of the page prerenders. */}
      <Suspense fallback={<p className="py-12 text-sm text-muted-foreground">Loading…</p>}>
        <StreamDetail />
      </Suspense>
    </SiteShell>
  );
}
