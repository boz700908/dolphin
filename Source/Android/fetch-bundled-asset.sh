#!/bin/sh
# Copyright 2026 Dolphin Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This fork is a launcher for a single game and bundles that game's image in
# the APK. The image is far too large for the Git history, so it is published
# as a release asset and fetched into the assets folder by this script. Run it
# once before a local build; the Android workflow runs it too.
#
# Usage: ./fetch-bundled-asset.sh [--force]

set -eu

ASSET_NAME="rhythm_heaven_fever_sc.wbfs"
ASSET_SIZE=486539264
ASSET_SHA256=00b237def3d703697d7fdd224f18ed9d20b823c0a8b3b1cb255d2bb7d97fdc19
ASSET_URL="https://github.com/boz700908/dolphin/releases/download/assets-v1.0/${ASSET_NAME}"

force=false
for arg in "$@"; do
    case "$arg" in
        --force) force=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dest_dir="${script_dir}/app/src/main/assets"
dest="${dest_dir}/${ASSET_NAME}"
part="${dest}.part"

sha256_of() {
    if command -v sha256sum > /dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

size_of() {
    wc -c < "$1" | tr -d ' '
}

verify() {
    actual_size=$(size_of "$1")
    if [ "$actual_size" != "$ASSET_SIZE" ]; then
        echo "Wrong size: $1 is ${actual_size} bytes, expected ${ASSET_SIZE}." >&2
        return 1
    fi

    actual_sha=$(sha256_of "$1")
    if [ "$actual_sha" != "$ASSET_SHA256" ]; then
        echo "Wrong checksum: $1 is ${actual_sha}, expected ${ASSET_SHA256}." >&2
        return 1
    fi

    return 0
}

if [ "$force" = false ] && [ -f "$dest" ] && verify "$dest" 2> /dev/null; then
    echo "Already present: $dest"
    exit 0
fi

mkdir -p "$dest_dir"
rm -f "$part"

echo "Downloading ${ASSET_NAME} (${ASSET_SIZE} bytes)…"
if command -v curl > /dev/null 2>&1; then
    curl -fL --retry 3 --retry-delay 5 -o "$part" "$ASSET_URL"
else
    wget -O "$part" "$ASSET_URL"
fi

if ! verify "$part"; then
    rm -f "$part"
    exit 1
fi

mv -f "$part" "$dest"
echo "Fetched $dest"
