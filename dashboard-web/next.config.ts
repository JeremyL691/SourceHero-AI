import type { NextConfig } from "next";

// The API origin for connect-src. NEXT_PUBLIC_API_URL is "/api" behind Caddy (same
// origin, covered by 'self') or an absolute URL in local and e2e runs (a second origin
// that needs listing explicitly).
function apiOrigin(): string | null {
  const raw = process.env.NEXT_PUBLIC_API_URL ?? "";
  try {
    const url = new URL(raw);
    return url.protocol === "http:" || url.protocol === "https:" ? url.origin : null;
  } catch {
    return null;
  }
}

function contentSecurityPolicy(): string {
  const origin = apiOrigin();
  const connectSrc = origin ? `'self' ${origin}` : "'self'";
  // Next.js injects inline bootstrap scripts, so script-src needs 'unsafe-inline'.
  return [
    "default-src 'self'",
    `connect-src ${connectSrc}`,
    "frame-ancestors 'none'",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self' 'unsafe-inline'",
  ].join("; ");
}

const nextConfig: NextConfig = {
  // Standalone output: the e2e run and the Docker image both start the built server
  // without a node_modules tree.
  output: "standalone",
  reactStrictMode: true,
  async headers() {
    // HSTS is deliberately absent: it belongs to the reverse proxy / API, not the app.
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy() },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
