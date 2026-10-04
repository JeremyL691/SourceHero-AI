import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output: the e2e run and the Docker image both start the built server
  // without a node_modules tree.
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
