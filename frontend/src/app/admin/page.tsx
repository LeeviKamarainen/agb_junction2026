import type { Metadata } from "next";
import { SiteShell } from "@/components/site-shell";
import { DemoManager } from "./demo-manager";

export const metadata: Metadata = {
  title: "Manage demos — GlassMemory",
  robots: { index: false },
};
export default function AdminPage() {
  return (
    <SiteShell>
      <DemoManager />
    </SiteShell>
  );
}
