# Dashboard image.
#
# Three stages so a code change does not reinstall node_modules: dependencies, then
# build, then a runtime image carrying only what the standalone server needs.
# Node is pinned: a floating tag would change the runtime under a built image.
FROM node:22.12.0-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22.12.0-alpine AS build
WORKDIR /app
COPY . .
# Host node_modules (possibly built for macOS) must not leak into the image.
COPY --from=deps /app/node_modules ./node_modules
# Baked in at build time, so the browser is pointed at the API by build argument
# rather than at runtime: NEXT_PUBLIC_* is inlined by Next, not read from the
# container's environment when the request is served. The default is the prod
# profile's same-origin path (Caddy serves the API under /api).
ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:22.12.0-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# next.config.ts reads NEXT_PUBLIC_API_URL at server start to build the CSP connect-src,
# so the runtime needs the same value the build used.
ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# The standalone output omits these two by design; without them the server starts
# and then 404s every asset it references.
COPY --chown=node:node --from=build /app/public ./public
COPY --chown=node:node --from=build /app/.next/standalone ./
COPY --chown=node:node --from=build /app/.next/static ./.next/static

USER node

EXPOSE 3000

# Runs the standalone server rather than `next start`: next.config.ts sets
# output: "standalone", and `next start` does not serve that output.
CMD ["node", "server.js"]
