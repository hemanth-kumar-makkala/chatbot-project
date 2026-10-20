/** @type {import('next').NextConfig} */
const apiOrigin = process.env.API_ORIGIN;
if (!apiOrigin) {
  throw new Error(
    "Missing required environment variable: API_ORIGIN must be set (e.g. http://localhost:8000)"
  );
}

const cleanedOrigin = apiOrigin.replace(/\/+$/, "");

const nextConfig = {
  experimental: {
    proxyTimeout: 120_000,
  },
  async redirects() {
    return [
      {
        source: "/assistants/00000000-0000-0002-0000-000000000001/knowledge",
        destination: "/assistants/00000000-0000-0000-0002-000000000001/knowledge",
        permanent: false,
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/v1/assistants/00000000-0000-0002-0000-000000000001/:path*",
        destination: `${cleanedOrigin}/v1/assistants/00000000-0000-0000-0002-000000000001/:path*`,
      },
      {
        source: "/v1/:path*",
        destination: `${cleanedOrigin}/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
