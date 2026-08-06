# apps/frontend/Dockerfile
#
# Production image for the Next.js frontend. The target architecture hosts
# the frontend SEPARATELY (e.g. Vercel, or this container behind a CDN); this
# Dockerfile is for the container path. Multi-stage build produces a minimal
# standalone server (requires `output: "standalone"` in next.config.mjs).
#
# NEXT_PUBLIC_API_URL must point at the deployed API and is baked in at BUILD
# time (Next inlines NEXT_PUBLIC_* into the client bundle). Pass it as a build
# arg. It is a URL, not a secret.

# ---- deps ----
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# Use a clean, reproducible install. If there's no lockfile yet, fall back to
# a plain install (CI should commit a lockfile for reproducibility).
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# ---- builder ----
FROM node:22-slim AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- runner ----
FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

# Run as the built-in non-root `node` user.
USER node

# The standalone output contains a minimal server + only the node_modules it
# actually needs.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
