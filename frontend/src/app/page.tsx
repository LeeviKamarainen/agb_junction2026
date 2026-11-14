import { LiveHome } from "./_home/live";
import { SiteShell } from "./_home/site-shell";

export default function Home() {
  return (
    <SiteShell>
      <LiveHome />
    </SiteShell>
  );
}
