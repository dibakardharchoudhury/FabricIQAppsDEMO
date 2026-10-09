# syntax=docker/dockerfile:1
# Build from the repository root:
# docker build -f HydroOperationsService/Dockerfile .
# Linux amd64 is the wheel-verified packaging target. This is not a hosting rollout.
FROM node:24-bookworm-slim AS source-dependencies
ARG TARGETARCH=amd64
RUN test "$TARGETARCH" = amd64 && test "$(dpkg --print-architecture)" = amd64
WORKDIR /opt/hydro/HydroOperationsApp
COPY HydroOperationsApp/package.json HydroOperationsApp/package-lock.json HydroOperationsApp/.npmrc ./
# The lock marks rayfin-core's required TypeScript peer as dev; install it in this stage.
RUN npm ci --ignore-scripts --no-audit --no-fund \
    && npm cache clean --force
# Keep the existing lock, but retain only bridge dependencies and their required peers.
RUN node --input-type=commonjs -e '\
const fs = require("node:fs"); \
const path = require("node:path"); \
const root = process.cwd(); \
const pending = ["@microsoft/rayfin-client", "@microsoft/rayfin-auth-provider-fabric", "papaparse"] \
  .map(name => path.join(root, "node_modules", name)); \
const copied = new Set(); \
while (pending.length) { \
  const source = pending.pop(); \
  if (copied.has(source)) continue; \
  copied.add(source); \
  const metadata = JSON.parse(fs.readFileSync(path.join(source, "package.json"), "utf8")); \
  fs.cpSync(source, path.join(process.argv[2], path.relative(root, source)), { \
    recursive: true, \
    filter: candidate => !path.relative(source, candidate).split(path.sep).includes("node_modules"), \
  }); \
  for (const name of Object.keys({ ...metadata.dependencies, ...metadata.peerDependencies })) { \
    let parent = source, dependency; \
    while (parent.startsWith(root)) { \
      const candidate = path.join(parent, "node_modules", name); \
      if (fs.existsSync(path.join(candidate, "package.json"))) { dependency = candidate; break; } \
      parent = path.dirname(parent); \
    } \
    if (!dependency && metadata.peerDependenciesMeta?.[name]?.optional) continue; \
    if (!dependency) throw new Error(`Missing runtime dependency ${name}`); \
    pending.push(dependency); \
  } \
} \
' - /opt/bridge-dependencies

FROM python:3.12-slim-bookworm
ARG TARGETARCH=amd64
RUN test "$TARGETARCH" = amd64 \
    && test "$(dpkg --print-architecture)" = amd64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=source-dependencies /usr/local/bin/node /usr/local/bin/node
WORKDIR /opt/hydro
COPY HydroOperationsService/requirements.lock HydroOperationsService/pyproject.toml ./HydroOperationsService/
RUN pip install --no-cache-dir --only-binary=:all: -r HydroOperationsService/requirements.lock
COPY HydroOperationsService/src/hydro_orchestrator/*.py ./HydroOperationsService/src/hydro_orchestrator/
COPY HydroOperationsService/skills/condition-triage/SKILL.md ./HydroOperationsService/skills/condition-triage/
COPY HydroOperationsService/skills/source-reconciliation/SKILL.md ./HydroOperationsService/skills/source-reconciliation/
COPY HydroOperationsService/skills/root-cause-evidence/SKILL.md ./HydroOperationsService/skills/root-cause-evidence/
COPY HydroOperationsService/skills/maintenance-planning/SKILL.md ./HydroOperationsService/skills/maintenance-planning/
COPY HydroOperationsService/skills/work-order-review/SKILL.md ./HydroOperationsService/skills/work-order-review/
COPY HydroOperationsService/skills/grounded-presentation/SKILL.md ./HydroOperationsService/skills/grounded-presentation/
# Editable installation retains the sibling layout used by NodeSourceBridge.
RUN pip install --no-cache-dir --no-deps -e ./HydroOperationsService
COPY --from=source-dependencies /opt/bridge-dependencies/node_modules ./HydroOperationsApp/node_modules
COPY HydroOperationsApp/package.json ./HydroOperationsApp/
COPY HydroOperationsApp/scripts/local-fabric-sources.mjs ./HydroOperationsApp/scripts/
COPY HydroOperationsApp/src/services/kustoResult.ts ./HydroOperationsApp/src/services/
COPY HydroOperationsApp/src/services/copilot/query.ts HydroOperationsApp/src/services/copilot/catalog.ts HydroOperationsApp/src/services/copilot/rcaEvidence.ts HydroOperationsApp/src/services/copilot/agentDefinitions.ts HydroOperationsApp/src/services/copilot/answerPresentation.ts HydroOperationsApp/src/services/copilot/orchestration.ts ./HydroOperationsApp/src/services/copilot/
RUN groupadd --gid 10001 hydro \
    && useradd --uid 10001 --gid hydro --no-create-home hydro \
    && mkdir -p /var/lib/hydro /home/session/hydro \
    && chown hydro:hydro /var/lib/hydro /home/session /home/session/hydro \
    && chmod 700 /var/lib/hydro /home/session /home/session/hydro
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/session \
    HYDRO_LOCAL_NODE=/usr/local/bin/node \
    HYDRO_RUNTIME_HOST=0.0.0.0 \
    HYDRO_RUNTIME_PORT=8088 \
    HYDRO_RUNTIME_USER=hydro \
    HYDRO_RUNTIME_STATE_DIR=/var/lib/hydro
# State is local filesystem only. Mounts must remain writable by UID/GID 10001.
# Inject both runtime JSON configurations at startup; never pass source tokens at build time.
USER 10001:10001
RUN python -c "import asyncio, os; from pathlib import Path; from tempfile import TemporaryDirectory; from filelock import FileLock; from hydro_orchestrator.service import RuntimeConfiguration; from hydro_orchestrator.live_sources import NodeSourceBridge; assert os.getuid() == 10001; probe = TemporaryDirectory(dir=Path(os.environ['HOME']) / 'hydro'); lock = FileLock(str(Path(probe.name) / 'state.lock')); lock.acquire(timeout=0); lock.release(); probe.cleanup(); assert asyncio.run(NodeSourceBridge().call({'action': 'agent_contracts'}))['names']; print('Nonroot state locking, backend imports and packaged Node contracts passed; no live source access.')"
# Initialize root-owned session volumes, then drop privileges before source clients or HTTP startup.
USER 0:0
RUN python -c "import os; from pathlib import Path; from tempfile import TemporaryDirectory; from filelock import FileLock; from hydro_orchestrator.__main__ import prepare_runtime_user; home = Path('/home/session'); state = home / 'hydro'; os.chown(home, 0, 0); os.chown(state, 0, 0); prepare_runtime_user(state); assert os.getuid() == os.geteuid() == 10001; assert os.getgid() == os.getegid() == 10001; assert not os.getgroups(); probe = TemporaryDirectory(dir=state); lock = FileLock(str(Path(probe.name) / 'state.lock')); lock.acquire(timeout=0); lock.release(); probe.cleanup(); print('Root-owned session initialization, permanent privilege drop and nonroot locking passed.')"
EXPOSE 8088
# /health is liveness only, not source verification or agent execution readiness.
CMD ["python", "-m", "hydro_orchestrator", "--serve-delegated"]
