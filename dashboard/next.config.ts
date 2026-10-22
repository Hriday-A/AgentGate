import type { NextConfig } from "next";

// The browser only ever talks to this Next.js server. API calls, OAuth and screenshots
// are proxied to Spring Boot, so everything is same-origin and the session cookie just works.
const API_URL = process.env.API_URL ?? "http://localhost:8080";
const PROXIED = ["/graphql", "/api/artifacts/:path*", "/oauth2/:path*", "/login/oauth2/:path*",
                 "/logout", "/dev/login", "/auth/providers"];

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return PROXIED.map((source) => ({ source, destination: `${API_URL}${source}` }));
  },
};

export default nextConfig;
