import { type NextRequest, NextResponse } from "next/server";

// An optimistic check only: pages that need a login send visitors without a session cookie to
// /login. The backend checks the session itself on every /api call and decides what each user may
// see. /api is left out on purpose: the proxy would buffer request bodies (uploads) in memory.
export function proxy(request: NextRequest) {
  if (request.cookies.has("session")) return NextResponse.next();
  const login = new URL("/login", request.url);
  login.searchParams.set(
    "next",
    request.nextUrl.pathname + request.nextUrl.search,
  );
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/memory/:path*", "/glasses/:path*"],
};
