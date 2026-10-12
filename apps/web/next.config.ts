import type { NextConfig } from "next";

// Platform API origin. In the compose network this is http://api:8080; it is
// resolved at build time because rewrites are compiled into the standalone
// build, so compose passes it as a build arg.
const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8080";

const config: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  devIndicators: false,
  // Emit a self-contained server for the Docker image (see Dockerfile).
  output: process.env.SITES_BUILD === "1" ? undefined : "standalone",
  // OAuth callbacks carry one-time codes: never write incoming URLs to dev logs.
  logging: false,
  async rewrites() {
    if (process.env.SITES_BUILD === "1") return [];
    // afterFiles: the lab BFF routes still in apps/web win; everything else
    // under /api and /v1 goes to the platform API on the same origin, so
    // cookies and CSRF origin checks stay first-party.
    return {
      afterFiles: [
        { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
        { source: "/v1/:path*", destination: `${API_ORIGIN}/v1/:path*` },
      ],
    };
  },
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      ],
    }];
  },
};

export default config;
