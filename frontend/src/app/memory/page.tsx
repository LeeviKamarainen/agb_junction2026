import type { Metadata } from "next";

import { AdminOnly } from "@/components/admin-only";
import { SiteHeader } from "@/components/site-header";

import "../glasses/glasses.css";
import { MemoryLab } from "./lab";

export const metadata: Metadata = {
  title: "Memory lab — Recall/Remind",
  robots: { index: false },
};

/** The admin's view of the memory: add, ask, stream, and the map of every stored vector. */
export default function MemoryLabPage() {
  return (
    <div className="site-shell flex flex-1 flex-col">
      <SiteHeader />
      <AdminOnly>
        <div className="flex flex-1 flex-col gap-6 py-8">
          <h1 className="text-2xl font-semibold tracking-tight">Memory lab</h1>
          <MemoryLab />
        </div>
      </AdminOnly>
    </div>
  );
}
