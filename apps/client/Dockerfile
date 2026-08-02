FROM oven/bun:1 AS base
WORKDIR /repo

COPY package.json bun.lock ./
COPY apps/client/package.json apps/client/package.json
COPY apps/server/package.json apps/server/package.json
COPY packages/shared-types/package.json packages/shared-types/package.json
RUN bun install --frozen-lockfile

COPY apps/client apps/client
COPY packages/shared-types packages/shared-types

WORKDIR /repo/apps/client
EXPOSE 3000
CMD ["bun", "run", "dev"]
