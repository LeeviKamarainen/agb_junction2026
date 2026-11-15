"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { UserMenu } from "@/components/user-menu";
import { useSession } from "@/lib/session";

const PUBLIC_LINKS = [
  { href: "/", label: "Live" },
  { href: "/demos", label: "Demos" },
];

// The developer views, for the admin only.
const ADMIN_LINKS = [
  { href: "/admin", label: "Manage demos" },
  { href: "/admin/streams", label: "Streams" },
  { href: "/glasses", label: "Glasses harness" },
  { href: "/memory", label: "Memory lab" },
];

type Props = {
  /** On a page that needs a login: the user menu sends the visitor to /login without one. */
  requireLogin?: boolean;
};

/** The header every page shares: the wordmark, the page links and the login menu. */
export function SiteHeader({ requireLogin = false }: Props) {
  const { user } = useSession();
  const path = usePathname();
  const links =
    user?.role === "admin" ? [...PUBLIC_LINKS, ...ADMIN_LINKS] : PUBLIC_LINKS;

  return (
    <header className="site-header">
      <Link href="/" className="wordmark" aria-label="Glass memory home">
        GLASS<span className="wordmark-accent">/MEMORY</span>
      </Link>
      <nav
        aria-label="Main navigation"
        className="flex flex-wrap items-center justify-end gap-x-1 gap-y-2"
      >
        {links.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className="site-nav-link"
            aria-current={path === href ? "page" : undefined}
          >
            {label}
          </Link>
        ))}
        <UserMenu requireLogin={requireLogin} className="ml-3" />
      </nav>
    </header>
  );
}
