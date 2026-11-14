import { DemoAsk } from "./_home/demo-ask";
import { SiteShell } from "./_home/site-shell";

export default function Home() {
  return (
    <SiteShell>
      <section className="home-search" aria-labelledby="ask-day-title">
        <p className="eyebrow">YOUR DAY, REMEMBERED</p>
        <h1 id="ask-day-title">What would you like to remember?</h1>
        <p className="text-sm text-muted-foreground">
          Ask about your day, or try a question from the Junction demo day.
        </p>
        <DemoAsk />
      </section>
    </SiteShell>
  );
}
