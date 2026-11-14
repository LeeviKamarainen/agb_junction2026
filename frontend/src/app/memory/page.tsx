import { ArrowLeft, Asterisk, Glasses } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { UserMenu } from "@/components/user-menu";

import "../glasses/glasses.css";
import { MemoryLab } from "./lab";

export const metadata: Metadata = {
  title: "Memory lab — Junction / 26",
  description:
    "Add images and notes to the memory database, ask Gemma about them and watch it search, or try it all through the glasses.",
};

export default function MemoryLabPage() {
  return (
    <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-6 px-4 py-6 md:px-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Asterisk className="text-primary" size={26} aria-hidden="true" />
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Memory lab</h1>
            <p className="text-sm text-muted-foreground">
              Add memories on the left, ask about them on the right. Every
              vector_search call Gemma makes shows up as it happens; the Glasses
              tab shows the same memory through the glasses, and the map below
              shows every stored vector.
            </p>
          </div>
        </div>
        <nav className="flex flex-wrap items-center gap-4" aria-label="Pages">
          <UserMenu requireLogin />
          <Link
            href="/glasses"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <Glasses size={15} aria-hidden="true" /> Full-screen glasses
          </Link>
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft size={15} aria-hidden="true" /> Home
          </Link>
        </nav>
      </header>
      <MemoryLab />
    </div>
  );
}
