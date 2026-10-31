/** @type {import('next').NextConfig} */
const nextConfig = {
  // Next would otherwise generate frontend/AGENTS.md and frontend/CLAUDE.md on
  // every `next dev`, which shadows the conventions in the repo-root CLAUDE.md.
  agentRules: false,
  experimental: {
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
};

export default nextConfig;
