"use client";

import { Glasses, MessageSquareText } from "lucide-react";
import { useState } from "react";

import { GlassesHarness } from "../glasses/harness";
import { AskChat } from "./ask-chat";
import { MemoryLibrary } from "./memory-library";

type Tab = "ask" | "glasses";

const tabs: { id: Tab; label: string; Icon: typeof Glasses }[] = [
  { id: "ask", label: "Ask", Icon: MessageSquareText },
  { id: "glasses", label: "Glasses", Icon: Glasses },
];

/**
 * The memories on the left; on the right, either the Ask chat (Gemma's searches step by step) or
 * the glasses harness (what the wearer sees and hears). Both stay mounted, so switching tabs
 * keeps the conversation and the display; photos the glasses store show up in the library.
 */
export function MemoryLab() {
  const [tab, setTab] = useState<Tab>("ask");
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <main className="grid flex-1 gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <MemoryLibrary refreshKey={refreshKey} />
      <div className="flex min-w-0 flex-col gap-3">
        <div
          role="tablist"
          aria-label="Ask or glasses"
          className="inline-flex w-fit gap-1 rounded-lg border border-border bg-card p-1"
        >
          {tabs.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`lab-tab-${id}`}
              aria-selected={tab === id}
              aria-controls={`lab-panel-${id}`}
              onClick={() => setTab(id)}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                tab === id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon size={15} aria-hidden="true" /> {label}
            </button>
          ))}
        </div>
        <div
          role="tabpanel"
          id="lab-panel-ask"
          aria-labelledby="lab-tab-ask"
          hidden={tab !== "ask"}
        >
          <AskChat />
        </div>
        <div
          role="tabpanel"
          id="lab-panel-glasses"
          aria-labelledby="lab-tab-glasses"
          hidden={tab !== "glasses"}
        >
          <GlassesHarness
            embedded
            active={tab === "glasses"}
            onPhotoStored={() => setRefreshKey((key) => key + 1)}
          />
        </div>
      </div>
    </main>
  );
}
