#!/usr/bin/env bash
# Fetch the six scored instances from the GitHub Release.
#
# Usage:
#   scripts/download.sh
#
# The .scp files arrive gzipped and are decompressed into instances/, so every
# path in the README works unchanged. Each file's SHA-256 is checked against
# checksums/scored.sha256 (the same digest is in its .meta.json); a mismatch
# aborts rather than leaving you to debug a truncated download as an
# algorithm bug. Files already present are skipped.

set -euo pipefail

REPO="${RELEASE_REPO:-ythuang0522/set-cover-competition}"
TAG="${RELEASE_TAG:-v1.0}"

cd "$(dirname "$0")/.."
mkdir -p instances

names=(rnd5k uni5k dense2k geo10k blocks8k tail5k)
sums="checksums/scored.sha256"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

echo "Downloading the scored instances from ${REPO} @ ${TAG} ..."
for name in "${names[@]}"; do
  dest="instances/${name}.scp"
  if [[ -f "$dest" ]]; then
    echo "  [skip] ${dest} already present"
    continue
  fi
  url="https://github.com/${REPO}/releases/download/${TAG}/${name}.scp.gz"
  echo "  [get ] ${name}.scp"
  curl -fL --retry 3 -o "${dest}.gz" "$url"
  gunzip -f "${dest}.gz"
  want=$(awk -v f="${name}.scp" '$2 == f {print $1}' "$sums")
  got=$(sha256_of "$dest")
  if [[ "$got" != "$want" ]]; then
    echo "  CHECKSUM MISMATCH for ${dest}: got ${got}, want ${want}" >&2
    rm -f "$dest"
    exit 1
  fi
done
echo "Done. instances/ now holds the scored set; run:"
echo "  python3 grade.py --solver ./solver --instances instances.txt --json result.json"
