import type { NextConfig } from "next";

const host = process.env.PUBLIC_HOST || "localhost";
const nextConfig: NextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_GATEWAY_URL:
      process.env.NEXT_PUBLIC_GATEWAY_URL || `http://${host}:8000`,
  },
};

export default nextConfig;
