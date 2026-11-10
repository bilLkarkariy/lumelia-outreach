FROM node:22.22-bookworm-slim

# The coding-agent CLI is a deployment choice: pass its pinned npm package at build time,
# e.g. --build-arg AGENT_CLI_PACKAGE=<package>@<version>. See docs/AGENT.md.
ARG AGENT_CLI_PACKAGE
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && test -n "$AGENT_CLI_PACKAGE" \
    && npm install -g "$AGENT_CLI_PACKAGE" \
    && useradd --uid 10001 --create-home agent

WORKDIR /app
COPY agent /app/agent
ENV PYTHONUNBUFFERED=1 LUMELIA_AGENT_RUNTIME=/tmp/lumelia-agent
CMD ["python3", "/app/agent/boot.py"]
