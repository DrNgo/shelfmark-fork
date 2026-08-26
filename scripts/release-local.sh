#!/usr/bin/env bash
# release-local.sh — build the fork image natively and push it to ghcr.
#
# The GitHub workflow cross-compiles the arm64 half under QEMU, which is the
# slow part of a CI release; the cluster nodes are arm64 and this Mac builds
# that natively. This wraps the whole local release: tag, build, push, verify.
#
#   scripts/release-local.sh              # auto-increment: latest fork.N -> fork.N+1
#   scripts/release-local.sh 1.3.5-fork.7 # explicit version
#   scripts/release-local.sh --amd64      # also build linux/amd64 (QEMU, slow)
#   scripts/release-local.sh --dry-run    # print what would happen, do nothing
#   scripts/release-local.sh -y           # skip the confirmation prompt
#
# After the push, bump the image tag in
#   fleet-infra/clusters/my-cluster/media/media.shelfmark.yaml
# (drift will ask for a signature refresh), commit, push, and flux reconcile.
#
# NEVER re-push an existing tag: the manifest pins semver with
# imagePullPolicy: IfNotPresent, so a node that already cached the tag keeps
# the old bytes silently. This script refuses reused versions for that reason.

set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/drngo/shelfmark}"
TARGET="${TARGET:-shelfmark}"
PLATFORMS="linux/arm64"
DRY_RUN=0
ASSUME_YES=0
VERSION=""

for arg in "$@"; do
  case "$arg" in
    --amd64) PLATFORMS="linux/arm64,linux/amd64" ;;
    --dry-run) DRY_RUN=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "error: unknown flag $arg" >&2; exit 1 ;;
    *) VERSION="$arg" ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"

# --- preflight ---------------------------------------------------------------

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is dirty — commit or stash first." >&2
  exit 1
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "error: on branch '$branch' — releases cut from main." >&2
  exit 1
fi

git fetch origin --tags --quiet

if [[ -z "$VERSION" ]]; then
  latest="$(git tag --list 'v*-fork.*' | sort -V | tail -1)"
  if [[ -z "$latest" ]]; then
    echo "error: no v*-fork.* tag found to increment; pass a version explicitly." >&2
    exit 1
  fi
  VERSION="${latest#v}"
  VERSION="${VERSION%.*}.$(( ${latest##*.} + 1 ))"
fi

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+-fork\.[0-9]+$ ]]; then
  echo "error: version '$VERSION' does not match X.Y.Z-fork.N" >&2
  exit 1
fi

# The X.Y.Z half names the UPSTREAM release this fork is built on, and the
# manifest's pinned tag is the only record of that. Auto-increment above only
# bumps N, so the first release after merging a newer upstream would silently
# keep the stale base -- claiming an image is built on 1.3.5 when it contains
# 1.3.12. Derive the real base from the last non-fork tag reachable from HEAD
# and refuse the mismatch; a new upstream base restarts the counter at fork.1.
upstream_base="$(git describe --tags --abbrev=0 --match='v[0-9]*' --exclude='*-fork.*' HEAD 2>/dev/null || true)"
upstream_base="${upstream_base#v}"
requested_base="${VERSION%-fork.*}"
if [[ -z "$upstream_base" ]]; then
  echo "warning: no upstream release tag reachable from HEAD -- cannot verify the" >&2
  echo "         '$requested_base' base. Run: git fetch upstream --tags" >&2
elif [[ "$upstream_base" != "$requested_base" ]]; then
  echo "error: version '$VERSION' claims upstream $requested_base, but HEAD contains" >&2
  echo "       upstream $upstream_base. After merging a new upstream release the fork" >&2
  echo "       counter restarts: $upstream_base-fork.1" >&2
  echo "       Set ALLOW_UPSTREAM_MISMATCH=1 to override." >&2
  [[ "${ALLOW_UPSTREAM_MISMATCH:-0}" == 1 ]] || exit 1
  echo "note: ALLOW_UPSTREAM_MISMATCH=1 -- continuing with the mismatched base." >&2
fi

TAG="v$VERSION"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  # A tag whose IMAGE was published is never rebuilt (IfNotPresent-cached
  # nodes would keep the old bytes). But a tag whose build FAILED may be
  # resumed: same tag, image not on ghcr yet.
  if docker manifest inspect "$IMAGE:$VERSION" >/dev/null 2>&1; then
    echo "error: $IMAGE:$VERSION is already published — a released image is" >&2
    echo "       never rebuilt (IfNotPresent-cached nodes keep old bytes). Bump N." >&2
    exit 1
  fi
  if [[ "$(git rev-parse "$TAG^{commit}")" != "$(git rev-parse HEAD)" ]]; then
    echo "error: tag $TAG exists but points at a different commit than HEAD." >&2
    exit 1
  fi
  echo "note: tag $TAG exists with no published image — resuming its build."
  RESUME=1
else
  RESUME=0
fi

if ! docker info >/dev/null 2>&1; then
  echo "error: docker daemon not running (start OrbStack/Docker Desktop first)." >&2
  exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
  echo "error: docker buildx not available." >&2
  exit 1
fi
# docker login succeeds with any valid token, but pushing needs write:packages.
# One-time setup:  gh auth refresh -s write:packages
#            then: gh auth token | docker login ghcr.io -u DrNgo --password-stdin
if ! gh auth status 2>&1 | grep -q "write:packages"; then
  echo "warning: gh token lacks write:packages — the push will likely 403." >&2
  echo "         run: gh auth refresh -s write:packages  (one-time, interactive)" >&2
fi

BUILD_VERSION="$(date +%Y-%m-%d)-$(git rev-parse HEAD)"

echo "release : $IMAGE:$VERSION  (tag $TAG at $(git rev-parse --short HEAD))"
echo "platforms: $PLATFORMS"
echo "target  : $TARGET"

if [[ "$DRY_RUN" == 1 ]]; then
  echo "dry run — would: git tag $TAG && git push origin main $TAG"
  echo "dry run — would: docker buildx build --platform $PLATFORMS --target $TARGET --push ."
  exit 0
fi

if [[ "$ASSUME_YES" != 1 ]]; then
  read -r -p "proceed? [y/N] " reply
  [[ "$reply" == "y" || "$reply" == "Y" ]] || { echo "aborted."; exit 1; }
fi

# --- tag, build, push --------------------------------------------------------

if [[ "$RESUME" != 1 ]]; then
  git tag -a "$TAG" -m "Fork release $VERSION (built locally)"
  git push origin main "$TAG"
fi

# On auth failure: gh auth refresh -s write:packages, then
#   gh auth token | docker login ghcr.io -u DrNgo --password-stdin
docker buildx build \
  --platform "$PLATFORMS" \
  --target "$TARGET" \
  --build-arg BUILD_VERSION="$BUILD_VERSION" \
  --build-arg RELEASE_VERSION="$TAG" \
  -t "$IMAGE:$VERSION" \
  --push .

# --- verify ------------------------------------------------------------------

echo
echo "verifying pushed manifest…"
docker manifest inspect "$IMAGE:$VERSION" >/dev/null
echo "ok: $IMAGE:$VERSION is live"
echo
echo "next: bump fleet-infra clusters/my-cluster/media/media.shelfmark.yaml to"
echo "      $IMAGE:$VERSION, refresh the drift signature, commit, push,"
echo "      then: flux reconcile source git flux-system &&"
echo "            flux reconcile kustomization flux-system &&"
echo "            kubectl -n media rollout status deploy/shelfmark"
