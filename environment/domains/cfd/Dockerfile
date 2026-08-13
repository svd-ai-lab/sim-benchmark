# sim-benchmark-cfd-fullstack
# CFD environment for the public OpenFOAM tasks.
# OS root must be the OpenFOAM image (OpenFOAM is not apt-installable).
# Toolchain in tools.sh; shared harness in environment/_common/install_harness.sh.
# Build from repo root: environment/domains/build.sh cfd
ARG BASE_REGISTRY=docker.io
# Pinned by DIGEST for the same reason the Debian bases are: a tag is mutable.
# `python:3.12-slim-bookworm` demonstrably moved — one host resolved it to
# Debian 12, another to a trixie-based image the same day, under a tag that
# names bookworm. A vendor release tag like `2412` is less likely to move, but
# "less likely" is not a property a calibrated ground truth can rest on.
#
# This is the digest every current build and every calibrated oracle used.
# Bumping it is an environment-layer change: rebuild, re-run cfd's oracles,
# update VERSIONS.md.
FROM ${BASE_REGISTRY}/opencfd/openfoam-default:2412@sha256:1ba02114b1c025c370f2e269a07677c16c9bea8d990fcd75ac8378aff9d41b50

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
COPY environment/domains/cfd/tools.sh /tmp/tools.sh
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

USER agent
WORKDIR /tmp/agent
# OpenFOAM image sets ENTRYPOINT=/openfoam/run which intercepts headless
# stdio (claude -p produces no output). Reset to plain shell; the OpenFOAM
# env is still available via /etc/profile.d sourcing in login shells.
ENTRYPOINT []
