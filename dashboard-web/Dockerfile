# Dashboard image.
#
# Three stages so a code change does not reinstall node_modules: dependencies, then
# build, then a runtime image carrying only what the standalone server needs.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Baked in at build time, so the browser is pointed at the API by build argument
# rather than at runtime: NEXT_PUBLIC_* is inlined by Next, not read from the
# container's environment when the request is served.
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8787
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# The standalone output omits these two by design; without them the server starts
# and then 404s every asset it references.
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]
