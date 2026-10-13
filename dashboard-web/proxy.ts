import { NextResponse, type NextRequest } from "next/server";

/** Route guard.
 *
 * The cookie set at sign-in is what this reads; localStorage is invisible on the
 * server. A request without it is sent to /login with the original path preserved,
 * so the user lands where they were going rather than at the overview.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (pathname === "/login" || pathname.startsWith("/_next")) return NextResponse.next();

  const token = request.cookies.get("sh_token")?.value;
  if (token) return NextResponse.next();

  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = `?next=${encodeURIComponent(pathname + search)}`;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/app/:path*"],
};
