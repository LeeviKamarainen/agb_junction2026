import type { Metadata } from "next";
import { SiteShell } from "../_home/site-shell";
import { DemoManager } from "./demo-manager";

export const metadata: Metadata = {
  title: "Manage demos — Glass memory",
  robots: { index: false },
};
export default function AdminPage() {
  return (
    <SiteShell>
      <DemoManager />
    </SiteShell>
  );
}
