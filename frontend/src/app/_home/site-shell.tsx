import Link from "next/link";
import { SiteNav } from "./site-nav";

export function SiteShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="site-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <Link href="/" className="wordmark" aria-label="Glass memory home">
          GLASS<span className="wordmark-accent">/MEMORY</span>
        </Link>
        <SiteNav />
      </header>
      <main id="main">{children}</main>
      <footer className="site-footer">
        <span>GLASS MEMORY / JUNCTION 2026</span>
        <Link href="/demos">Explore the demos ↗</Link>
      </footer>
    </div>
  );
}
