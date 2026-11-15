import { SiteHeader } from "@/components/site-header";

export function SiteShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="site-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <SiteHeader />
      <main id="main">{children}</main>
    </div>
  );
}
