# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# rubai onboarding web — container build.
#
# Build:  docker build -t rubai-onboarding-web:local apps/web
# Run:    docker run --rm -p 80:3001 --env-file apps/web/.env.local rubai-onboarding-web:local
#
# The app must answer on http://localhost (port 80) because VK ID only allows
# localhost apps on the default ports. Inside the container it listens on 3001
# and the host maps 80 -> 3001.
#
# Secrets (SESSION_SECRET, VK_CLIENT_ID, …) are NOT baked into the image. They
# are injected at runtime from the local, uncommitted .env.local.
# ---------------------------------------------------------------------------

# --- deps: install exactly what package-lock.json pins ----------------------
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- build: optimized production build --------------------------------------
FROM node:24-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# Rewrites are compiled into the build; compose passes the API service origin.
ARG API_ORIGIN=http://127.0.0.1:8080
ENV API_ORIGIN=${API_ORIGIN}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# --- runner: minimal image with the standalone output -----------------------
FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3001
# Standalone output carries the server plus its traced dependencies.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3001
USER node
CMD ["node", "server.js"]
