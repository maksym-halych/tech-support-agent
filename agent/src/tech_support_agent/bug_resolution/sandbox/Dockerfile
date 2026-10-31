FROM node:24.11.0-bookworm-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
RUN npm install --global pnpm@10.20.0
WORKDIR /opt/runner
COPY sandbox/package.json sandbox/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts && pnpm exec playwright install --with-deps chromium
WORKDIR /opt/deps
COPY demo_template/package.json demo_template/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts && pnpm rebuild esbuild
COPY demo_template/ /opt/harness/
COPY sandbox/*.mjs /opt/runner/
RUN groupadd --gid 10001 sandbox && useradd --uid 10001 --gid 10001 --no-create-home sandbox \
    && chmod -R a+rX /opt/runner /opt/deps /opt/harness /opt/browsers
USER 10001:10001
WORKDIR /work
EXPOSE 5173
ENTRYPOINT ["/usr/local/bin/node", "/opt/runner/runner.mjs"]
