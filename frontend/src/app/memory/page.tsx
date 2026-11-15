import type { Metadata } from "next";

import { SiteHeader } from "@/components/site-header";

import "../glasses/glasses.css";
import { MemoryLab } from "./lab";

export const metadata: Metadata = {
  title: "Memory lab — Glass memory",
  description:
    "Add images and notes to the memory database, ask Gemma about them and watch it search, or try it all through the glasses.",
};

export default function MemoryLabPage() {
  return (
    <div className="site-shell flex flex-1 flex-col">
      <SiteHeader requireLogin />
      <div className="flex flex-1 flex-col gap-6 py-8">
        <div className="flex flex-col gap-2">
          <p className="eyebrow">MEMORY LAB</p>
          <h1 className="text-2xl font-semibold tracking-tight">
            Add, ask and inspect
          </h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Add memories on the left, ask about them on the right. Every
            vector_search call Gemma makes shows up as it happens; the Memory
            tab sets up streaming and triggers, and the map below shows every
            stored vector.
          </p>
        </div>
        <MemoryLab />
      </div>
    </div>
  );
}
