#!/usr/bin/env bash
# build.sh — build a per-domain fullstack image (or all of them).
#
#   environment/domains/build.sh <domain> [flags]
#   environment/domains/build.sh all     [flags]
#
# Must run from the repo root (build context = repo root so the
# COPY lib/... / COPY tools/... paths in the common harness resolve).
#
# Flags:
#   --intl                 use international mirrors (npmjs/pypi.org, no gh-proxy)
#   --apt-mirror <url>     apt mirror, BUILD-TIME ONLY (restored before the layer ends)
#   --pip-index <url>      PyPI index (default: the Dockerfile's ARG)
#   --registry <reg>       BASE_REGISTRY for the FROM line (default docker.io)
#   --no-cache             docker build --no-cache
#
# Image tag: sim-benchmark-<domain>-fullstack:latest
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
KNOWN=(cfd combustion battery)

usage() { echo "usage: build.sh <${KNOWN[*]}|all> [--intl] [--apt-mirror URL] [--pip-index URL] [--registry REG] [--no-cache]"; exit 1; }
[ $# -ge 1 ] || usage
TARGET="$1"; shift

BUILD_ARGS=()
DOCKER_FLAGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --intl)
      BUILD_ARGS+=(--build-arg "GH_PROXY=" \
                   --build-arg "PIP_INDEX_URL=https://pypi.org/simple" \
                   --build-arg "PIP_TRUSTED_HOST=pypi.org" \
                   --build-arg "NPM_REGISTRY=https://registry.npmjs.org" \
                   --build-arg "NODE_TARBALL_URL=https://nodejs.org/dist/v20.19.0/node-v20.19.0-linux-x64.tar.gz")
      shift;;
    # Where apt fetches during the BUILD only. `environment/_common/apt_mirror.sh`
    # puts the upstream sources back before each layer ends, so the mirror never
    # reaches the image — a lasting rewrite made three images resolve only inside
    # one cloud, and the symptom was an agent that could not be set up on any
    # other host. Measure the fastest source from this builder first, rather
    # than hardcoding one.
    #   --apt-mirror http://mirrors.example.com
    --apt-mirror)     BUILD_ARGS+=(--build-arg "APT_MIRROR=$2"); shift 2;;
    # Same idea for pip. The Dockerfiles already carry a PIP_INDEX_URL ARG, but
    # nothing could set it except --intl, which switches every source at once.
    # The index that is fastest from a given builder is a property of that
    # builder's network, not of the recipe -- measure it and pass the winner.
    #
    # FASTEST IS NOT ENOUGH: every version here is pinned with `==`, and a mirror
    # that lags upstream cannot serve a pin it has not synced yet. One measured
    # 3x faster than the default and still failed the build outright, with
    # "No matching distribution found for numpy==2.5.1" against an index whose
    # newest was 2.5.0. Check the pins are actually there before adopting an
    # index -- and never "fix" that by loosening a pin.
    --pip-index)      BUILD_ARGS+=(--build-arg "PIP_INDEX_URL=$2" \
                                   --build-arg "PIP_TRUSTED_HOST=$(printf '%s' "$2" | sed -E 's#^https?://##; s#/.*##')")
                      shift 2;;
    --registry)       BUILD_ARGS+=(--build-arg "BASE_REGISTRY=$2"); shift 2;;
    --no-cache)       DOCKER_FLAGS+=(--no-cache); shift;;
    *) usage;;
  esac
done

build_one() {
  local d="$1"
  local dockerfile tag
  dockerfile="${HERE}/${d}/Dockerfile"
  [ -f "${dockerfile}" ] || { echo "no Dockerfile for '${d}'" >&2; exit 2; }
  tag="sim-benchmark-${d}-fullstack:latest"
  echo "=== building ${tag} (${dockerfile#${HERE}/}) ==="
  docker build "${DOCKER_FLAGS[@]}" "${BUILD_ARGS[@]}" \
    -f "${dockerfile}" \
    -t "${tag}" \
    "${REPO_ROOT}"
}

if [ "${TARGET}" = "all" ]; then
  for d in "${KNOWN[@]}"; do build_one "${d}"; done
else
  [[ " ${KNOWN[*]} " == *" ${TARGET} "* ]] || usage
  build_one "${TARGET}"
fi
echo "=== done ==="
