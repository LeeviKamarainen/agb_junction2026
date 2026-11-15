import Link from "next/link";
import { SiteHeader } from "@/components/site-header";

export function SiteShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="site-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <SiteHeader />
      <main id="main">{children}</main>
      <footer className="site-footer">
        <span>GLASS MEMORY / JUNCTION 2026</span>
        <Link href="/demos">Explore the demos ↗</Link>
      </footer>
    </div>
  );
}
