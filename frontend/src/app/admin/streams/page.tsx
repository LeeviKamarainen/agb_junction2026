import type { Metadata } from "next";

import { SiteShell } from "@/components/site-shell";
import { StreamWall } from "./stream-wall";

export const metadata: Metadata = {
  title: "Live streams — Recall/Remind",
  robots: { index: false },
};

export default function StreamsPage() {
  return (
    <SiteShell>
      <StreamWall />
    </SiteShell>
  );
}
