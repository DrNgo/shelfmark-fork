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

TAG="v$VERSION"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "error: tag $TAG already exists — a published tag is never rebuilt" >&2
  echo "       (IfNotPresent-cached nodes would keep the old image). Bump N." >&2
  exit 1
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "error: docker buildx not available." >&2
  exit 1
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

git tag -a "$TAG" -m "Fork release $VERSION (built locally)"
git push origin main "$TAG"

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
