import { LiveHome } from "./_home/live";
import { SiteShell } from "@/components/site-shell";

export default function Home() {
  return (
    <SiteShell>
      <LiveHome />
    </SiteShell>
  );
}
