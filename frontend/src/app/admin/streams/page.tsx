import type { Metadata } from "next";

import { SiteShell } from "../../_home/site-shell";
import { StreamWall } from "./stream-wall";

export const metadata: Metadata = {
  title: "Live streams — Glass memory",
  robots: { index: false },
};

export default function StreamsPage() {
  return (
    <SiteShell>
      <StreamWall />
    </SiteShell>
  );
}
