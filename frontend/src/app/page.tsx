import { Asterisk, BellRing, Glasses } from "lucide-react";

import { DemoAsk } from "./_home/demo-ask";
import { DemoTimeline } from "./_home/demo-timeline";
import { SiteNav } from "./_home/site-nav";
import { YourSpace } from "./_home/your-space";

export default function Home() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="site-shell">
        <header className="site-header">
          <a className="wordmark" href="#" aria-label="Glass memory home">
            <Asterisk
              aria-hidden="true"
              className="brand-symbol"
              strokeWidth={2.5}
            />
            GLASS<span className="wordmark-accent">/MEMORY</span>
          </a>
          <SiteNav />
        </header>

        <main id="main">
          <section
            className="flex flex-col gap-4 py-12 md:py-16"
            aria-labelledby="hero-title"
          >
            <p className="eyebrow">
              <Glasses size={14} className="text-primary" aria-hidden="true" />A
              VISUAL MEMORY FOR SMART GLASSES
            </p>
            <h1
              id="hero-title"
              className="max-w-4xl text-4xl leading-[1.05] font-semibold tracking-tight text-balance md:text-6xl"
            >
              Your glasses remember the day,
              <span className="text-primary"> so you don’t have to.</span>
            </h1>
            <p className="max-w-2xl text-base text-muted-foreground md:text-lg">
              Every moment the glasses see becomes a searchable memory. Watch a
              recorded day play back below, then ask Gemma about it.
            </p>
          </section>

          <Section
            id="timeline"
            eyebrow="01 / ONE DAY, REMEMBERED"
            title="The timeline"
            intro="Our demo dataset: a day at Junction as the glasses saw it, in order. It plays on its own and starts over at the end; pause it or pick any moment."
          >
            <DemoTimeline />
          </Section>

          <Section
            id="ask"
            eyebrow="02 / ASK YOUR MEMORY"
            title="Ask about the day"
            intro="Gemma searches only the demo day’s memories and answers from what it finds."
          >
            <DemoAsk />
          </Section>

          <Section
            id="trigger"
            eyebrow="03 / TRIGGERS"
            title="The glasses speak up"
            intro="Tell the glasses what to watch for, and they tell you when it shows up."
          >
            {/* Placeholder: the trigger demo goes here. */}
            <div className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-card/60 p-8 text-center">
              <BellRing size={28} className="text-primary" aria-hidden="true" />
              <h3 className="text-lg font-semibold">Trigger demo</h3>
              <p className="max-w-md text-sm text-muted-foreground">
                Coming soon: watch a trigger fire on the demo day, such as a
                warning when the bar stool comes into view.
              </p>
            </div>
          </Section>

          <YourSpace />
        </main>

        <footer className="site-footer">
          <span className="footer-brand">
            <Asterisk size={18} aria-hidden="true" /> GLASS MEMORY · JUNCTION
            2026
          </span>
          <span>Built in Espoo, 13–15 November 2026.</span>
        </footer>
      </div>
    </>
  );
}

function Section({
  id,
  eyebrow,
  title,
  intro,
  children,
}: {
  id: string;
  eyebrow: string;
  title: string;
  intro: string;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      className="flex flex-col gap-5 border-t border-border py-12"
      aria-labelledby={`${id}-title`}
    >
      <div className="flex flex-col gap-2">
        <p className="eyebrow">{eyebrow}</p>
        <h2
          id={`${id}-title`}
          className="text-3xl font-semibold tracking-tight"
        >
          {title}
        </h2>
        <p className="max-w-2xl text-sm text-muted-foreground">{intro}</p>
      </div>
      {children}
    </section>
  );
}
