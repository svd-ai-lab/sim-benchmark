# sim-benchmark-combustion-fullstack
# Chemical kinetics / combustion (Cantera).
# OS root is plain Debian slim: Cantera is a pip-installable library, so
# unlike the CFD/FEM domains there is no vendor image to inherit from.
# Toolchain in tools.sh; shared harness in environment/_common/install_harness.sh.
# Build from repo root: environment/domains/build.sh combustion
ARG BASE_REGISTRY=docker.io
# Pinned by DIGEST, not by tag. `python:3.12-slim-bookworm` is mutable and it
# moved: on one host it resolved to Debian 12 (bookworm), on another, the same
# day, to a trixie-based image — a different OS major under a tag that names
# bookworm. Two hosts building "the same" recipe then produce two different
# environments, and the ground truth calibrated in one does not hold in the
# other. The digest below is the image every current domain build and every
# calibrated oracle used.
#
# Bumping it is an environment-layer change: rebuild, re-run that domain's
# oracles, update VERSIONS.md.
FROM ${BASE_REGISTRY}/python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

USER root
ENV DEBIAN_FRONTEND=noninteractive

# === DOMAIN TOOLCHAIN (edit tools.sh to add/remove tools) ===
ARG GH_PROXY=https://gh-proxy.com/
# The package index the DOMAIN toolchain installs from. Declared here as well as
# beside the harness below, because pip in THIS step was not covered by it: the
# ARG existed but only install_harness.sh ever received it, so `--intl` (and any
# other override) silently did nothing for the half of the image that installs
# the solver. On a builder that cannot reach pypi.org fast that half then fails
# with "Could not find a version that satisfies the requirement numpy" -- which
# reads as a dependency-resolution problem and is actually the network.
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ARG APT_MIRROR=
COPY environment/_common/apt_mirror.sh /tmp/apt_mirror.sh
COPY environment/domains/combustion/tools.sh /tmp/tools.sh
RUN APT_MIRROR=${APT_MIRROR} GH_PROXY=${GH_PROXY} \
    PIP_INDEX_URL=${PIP_INDEX_URL} PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    bash /tmp/tools.sh

# === COMMON HARNESS (shared; see environment/_common/install_harness.sh) ===
COPY lib/sim_benchmark_verifier /opt/sim-benchmark-verifier/
COPY tools/openai_usage_proxy.py /opt/openai_usage_proxy.py
COPY tools/ccr-plugins /opt/ccr-plugins/
COPY environment/_common/install_harness.sh /tmp/install_harness.sh
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG NODE_TARBALL_URL=https://npmmirror.com/mirrors/node/v20.19.0/node-v20.19.0-linux-x64.tar.gz
RUN APT_MIRROR=${APT_MIRROR} \
    PIP_INDEX_URL=${PIP_INDEX_URL} PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    NPM_REGISTRY=${NPM_REGISTRY} NODE_TARBALL_URL=${NODE_TARBALL_URL} \
    GH_PROXY=${GH_PROXY} \
    bash /tmp/install_harness.sh

ENV MPLBACKEND=Agg

USER agent
WORKDIR /tmp/agent
