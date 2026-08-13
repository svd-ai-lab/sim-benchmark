#!/usr/bin/env bash
# install_harness.sh — the COMMON substrate every per-domain fullstack
# image shares. Sourced (RUN bash) by every environment/domains/<d>/Dockerfile
# AFTER that domain's tools.sh has installed the domain toolchain.
#
# WHAT THIS INSTALLS (domain-agnostic):
#   - OS deps (curl/git/python/build-essential/...)
#   - sim-benchmark-verifier  (the grader library, from pre-COPY'd /opt)
#   - Node 20 + claude-code + claude-code-router (the agent harness)
#   - openai_usage_proxy + ccr-plugins (token-recovery proxy, from /opt)
#   - the non-root `agent` user (UID 1200) Claude Code requires for
#     --permission-mode bypassPermissions
#
# WHAT THIS DOES NOT DO (domain-specific — lives in tools.sh):
#   - the Cantera, PyBaMM or OpenFOAM toolchain
#
# CONTRACT WITH THE CALLING Dockerfile (must run BEFORE this script):
#   COPY lib/sim_benchmark_verifier /opt/sim-benchmark-verifier/
#   COPY tools/openai_usage_proxy.py /opt/openai_usage_proxy.py
#   COPY tools/ccr-plugins /opt/ccr-plugins/
#   (build context = repo root)
#
# TUNABLES (env vars; CN-friendly defaults):
#   PIP_INDEX_URL     pip mirror                          (default: TUNA)
#   NPM_REGISTRY      npm mirror                          (default: npmmirror)
#   NODE_TARBALL_URL  node 20 tarball                     (default: npmmirror)
#   GH_PROXY          github proxy prefix for git+https   (default: gh-proxy.com)
#   AGENT_UID         non-root agent uid                  (default: 1200)
#
# Idempotency: safe to re-run; apt/pip/npm skip already-installed items.

set -euo pipefail

PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
NODE_TARBALL_URL="${NODE_TARBALL_URL:-https://npmmirror.com/mirrors/node/v20.19.0/node-v20.19.0-linux-x64.tar.gz}"
GH_PROXY="${GH_PROXY:-https://gh-proxy.com/}"
AGENT_UID="${AGENT_UID:-1200}"

echo "=== install_harness: OS deps ==="
export DEBIAN_FRONTEND=noninteractive
# Sourced HERE, before the first apt call, and once for the whole script. Put
# further down (next to the ripgrep install, the other apt user) it covered the
# second `apt-get update` and not the first, so half this layer still went to
# upstream and the build crawled exactly as before. A scoped rewrite is only
# worth anything if it is scoped around ALL of the apt in its script.
. /tmp/apt_mirror.sh
apt-get update
apt-get install -y --no-install-recommends \
    curl ca-certificates tar gzip git jq \
    python3 python3-pip python3-venv \
    tmux procps build-essential
rm -rf /var/lib/apt/lists/*

echo "=== install_harness: pip mirror + upgrade ==="
pip3 config set global.index-url "${PIP_INDEX_URL}" || true
pip3 config set global.trusted-host "${PIP_TRUSTED_HOST}" || true
# Upgrade pip PLAINLY first: ubuntu:22.04 ships pip 22.x which predates the
# --break-system-packages flag (PEP 668, pip 23+). After the upgrade we
# detect flag support so the same script works on 22.04 (no flag needed,
# not externally-managed) and Debian 12 / 24.04 (flag required).
python3 -m pip install --no-cache-dir --upgrade pip || true
PIP_BSP=""
python3 -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages' \
    && PIP_BSP="--break-system-packages"
PIP="python3 -m pip install --no-cache-dir ${PIP_BSP}"
echo "pip flags: ${PIP_BSP:-<none>}"

echo "=== install_harness: sim-benchmark-verifier (grader) ==="
# Pre-COPY'd by the calling Dockerfile. Editable so dev iteration on the
# verifier doesn't require a registry round-trip.
if [ -d /opt/sim-benchmark-verifier ]; then
    ${PIP} -e /opt/sim-benchmark-verifier
else
    echo "WARN: /opt/sim-benchmark-verifier not COPY'd — grader missing" >&2
fi

echo "=== install_harness: Node 20 + agent CLIs ==="
curl -fkSL "${NODE_TARBALL_URL}" | tar -xz -C /usr/local --strip-components=1
npm config set registry "${NPM_REGISTRY}"
# Every agent CLI the benchmark drives is baked HERE, not installed per trial.
#
# Harbor's installed-agent adapters will `npm install` (and for codex,
# `apt-get install ripgrep`) inside the container at agent-setup time unless
# their `_INSTALL_CHECK_COMMAND` already finds the binary. Leaving that to run
# time makes every trial depend on the host's outbound network and on the apt
# sources baked into the image — and that is not hypothetical: codex's setup
# failed on a second host with `Could not resolve 'mirrors.ivolces.com'` /
# `Unable to locate package ripgrep`, because the image had been built with an
# internal mirror that only resolves inside one cloud. claude-code was already
# baked, which is exactly why the other three model rows never hit this.
#
# Baking it also makes the CLI version part of the image — i.e. part of the
# reproducibility coordinate — instead of whatever npm served that morning.
npm install -g @anthropic-ai/claude-code @musistudio/claude-code-router @openai/codex
claude --version
codex --version

# codex shells out to ripgrep; its adapter apt-installs it at setup time when
# missing, which is the other half of the same runtime dependency.
# (The mirror window is already open — sourced at the top of this script.)
apt-get update && apt-get install -y --no-install-recommends ripgrep \
  && rm -rf /var/lib/apt/lists/*
rg --version | head -1

echo "=== install_harness: token-recovery proxy deps ==="
${PIP} aiohttp || true

echo "=== install_harness: agent user (UID ${AGENT_UID}) ==="
# Claude Code refuses --permission-mode bypassPermissions under root.
# UID 1200 avoids the OpenFOAM base image's openfoam=1100 NSS collision.
if ! id "${AGENT_UID}" >/dev/null 2>&1; then
    groupadd -g "${AGENT_UID}" agent
    useradd -m -u "${AGENT_UID}" -g "${AGENT_UID}" -s /bin/bash agent
fi
mkdir -p /tmp/agent /logs/agent /logs/verifier
chown -R "${AGENT_UID}:${AGENT_UID}" /tmp/agent /logs

echo "=== install_harness: done ==="
