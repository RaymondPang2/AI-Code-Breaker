/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a self-contained production server (.next/standalone) so the Docker
  // image can run without the full node_modules tree. See apps/frontend/Dockerfile.
  output: "standalone",
};

export default nextConfig;
