"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { UserMenu } from "@/components/user-menu";
import { useSession } from "@/lib/session";

const PUBLIC_LINKS = [
  { href: "/", label: "Live" },
  { href: "/demos", label: "Demos" },
];

// Anyone logged in, a guest too: the demo memories and their own, from every client.
const USER_LINKS = [{ href: "/memory", label: "Memory lab" }];

// The developer views, for the admin only.
const ADMIN_LINKS = [
  { href: "/admin", label: "Manage demos" },
  { href: "/admin/streams", label: "Streams" },
  { href: "/glasses", label: "Glasses harness" },
];

type Props = {
  /** On a page that needs a login: the user menu sends the visitor to /login without one. */
  requireLogin?: boolean;
};

/** The header every page shares: the wordmark, the page links and the login menu. */
export function SiteHeader({ requireLogin = false }: Props) {
  const { user } = useSession();
  const links = [
    ...PUBLIC_LINKS,
    ...(user ? USER_LINKS : []),
    ...(user?.role === "admin" ? ADMIN_LINKS : []),
  ];

  return (
    <header className="site-header">
      <Link href="/" className="wordmark" aria-label="Glass memory home">
        GLASS<span className="wordmark-accent">/MEMORY</span>
      </Link>
      <nav
        aria-label="Main navigation"
        className="flex flex-wrap items-center justify-end gap-x-1 gap-y-2"
      >
        {/* The current page is only known per request on a dynamic route (/admin/streams/<id>):
            the links prerender without the highlight, which streams in after. */}
        <Suspense fallback={<NavLinks links={links} current={null} />}>
          <CurrentNavLinks links={links} />
        </Suspense>
        <UserMenu requireLogin={requireLogin} className="ml-3" />
      </nav>
    </header>
  );
}

type NavLink = { href: string; label: string };

function CurrentNavLinks({ links }: { links: NavLink[] }) {
  return <NavLinks links={links} current={usePathname()} />;
}

function NavLinks({ links, current }: { links: NavLink[]; current: string | null }) {
  return links.map(({ href, label }) => (
    <Link
      key={href}
      href={href}
      className="site-nav-link"
      aria-current={current === href ? "page" : undefined}
    >
      {label}
    </Link>
  ));
}
