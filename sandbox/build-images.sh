#!/bin/sh
# Builds the test runner images. Existing images are kept, so a restart needs no network.
# Set FORCE=1 to rebuild them from fresh base images.
set -eu
cd "$(dirname "$0")"

build() {
  tag="legacylens-sandbox-$1:$2"
  if [ "${FORCE:-0}" != "1" ] && docker image inspect "$tag" >/dev/null 2>&1; then
    echo "$tag already exists."
  else
    docker build --pull -t "$tag" "$1"
  fi
}

# Raise a version here and in the backend settings when an image changes.
build python 1
build node 2
build php 1
echo "Sandbox images are ready."
