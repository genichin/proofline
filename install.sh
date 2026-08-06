#!/bin/sh
set -eu

VERSION="0.6.1"
REPOSITORY="genichin/proofline"
WHEEL="proofline-${VERSION}-py3-none-any.whl"
BASE_URL="https://github.com/${REPOSITORY}/releases/download/v${VERSION}"
FORCE="false"

usage() {
    printf 'Usage: install.sh [--force]\n' >&2
}

fail() {
    printf 'ProofLine installer: %s\n' "$1" >&2
    exit 1
}

case "$#" in
    0) ;;
    1)
        if [ "$1" = "--force" ]; then
            FORCE="true"
        else
            usage
            exit 2
        fi
        ;;
    *)
        usage
        exit 2
        ;;
esac

for command in curl sha256sum uv mktemp; do
    command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done

TOOL_DIR="$(uv tool dir)" || fail "cannot resolve uv tool directory"
if [ "$FORCE" = "false" ] && [ -d "${TOOL_DIR}/proofline" ]; then
    fail "ProofLine is already installed; rerun with --force to replace it explicitly"
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/proofline-install.XXXXXX")"
cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

curl -fsSL --retry 3 "${BASE_URL}/${WHEEL}" -o "${TEMP_DIR}/${WHEEL}"
curl -fsSL --retry 3 "${BASE_URL}/SHA256SUMS" -o "${TEMP_DIR}/SHA256SUMS"

(
    cd "$TEMP_DIR"
    sha256sum --check --strict SHA256SUMS
) || fail "wheel checksum verification failed"

if [ "$FORCE" = "true" ]; then
    uv tool install --force --no-config "${TEMP_DIR}/${WHEEL}"
else
    uv tool install --no-config "${TEMP_DIR}/${WHEEL}"
fi

BIN_DIR="$(uv tool dir --bin)"
PROOFLINE="${BIN_DIR}/proofline"
[ -x "$PROOFLINE" ] || fail "installed executable not found: $PROOFLINE"
ACTUAL_VERSION="$($PROOFLINE --version)"
[ "$ACTUAL_VERSION" = "proofline ${VERSION}" ] || fail "post-install version verification failed"

printf 'ProofLine %s installed: %s\n' "$VERSION" "$PROOFLINE"
