#!/bin/sh
set -eu

VERSION="0.7.0"
CORRECTIVE_VERSION="0.7.0"
PREDECESSOR_VERSION="0.6.0"
REPOSITORY="genichin/proofline"
WHEEL="proofline-${VERSION}-py3-none-any.whl"
BASE_URL="https://github.com/${REPOSITORY}/releases/download/v${VERSION}"
FORCE="false"
CORRECTIVE="false"

usage() {
    printf 'Usage: install.sh [--force | --corrective-transition]\n' >&2
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
        elif [ "$1" = "--corrective-transition" ]; then
            CORRECTIVE="true"
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

if [ "$CORRECTIVE" = "true" ]; then
    VERSION="$CORRECTIVE_VERSION"
    WHEEL="proofline-${VERSION}-py3-none-any.whl"
    BASE_URL="https://github.com/${REPOSITORY}/releases/download/v${VERSION}"
fi

for command in curl sha256sum uv mktemp cmp; do
    command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done

TOOL_DIR="$(uv tool dir)" || fail "cannot resolve uv tool directory"
if [ "$CORRECTIVE" = "false" ] && [ "$FORCE" = "false" ] && [ -d "${TOOL_DIR}/proofline" ]; then
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
    sha256sum "$WHEEL" > SHA256SUMS.expected
    cmp -s SHA256SUMS SHA256SUMS.expected
) || fail "wheel checksum verification failed"

if [ "$CORRECTIVE" = "true" ]; then
    PREDECESSOR_WHEEL="proofline-${PREDECESSOR_VERSION}-py3-none-any.whl"
    PREDECESSOR_URL="https://github.com/${REPOSITORY}/releases/download/v${PREDECESSOR_VERSION}"
    mkdir "${TEMP_DIR}/predecessor"
    curl -fsSL --retry 3 "${PREDECESSOR_URL}/${PREDECESSOR_WHEEL}" -o "${TEMP_DIR}/predecessor/${PREDECESSOR_WHEEL}"
    curl -fsSL --retry 3 "${PREDECESSOR_URL}/SHA256SUMS" -o "${TEMP_DIR}/predecessor/SHA256SUMS"
    (
        cd "${TEMP_DIR}/predecessor"
        sha256sum --check --strict SHA256SUMS
        sha256sum "$PREDECESSOR_WHEEL" > SHA256SUMS.expected
        cmp -s SHA256SUMS SHA256SUMS.expected
    ) || fail "predecessor wheel checksum verification failed"
    uv venv --no-config "${TEMP_DIR}/target-stage" || fail "cannot create target staging environment"
    uv pip install --no-config --python "${TEMP_DIR}/target-stage/bin/python" "${TEMP_DIR}/${WHEEL}" || fail "cannot install target staging package"
    "${TEMP_DIR}/target-stage/bin/python" -I -m proofline.installer_transition \
        --target-wheel "${TEMP_DIR}/${WHEEL}" \
        --predecessor-wheel "${TEMP_DIR}/predecessor/${PREDECESSOR_WHEEL}" \
        --home "${HOME:?HOME is required}" --uv "$(command -v uv)" || fail "corrective transition failed"
    exit 0
fi

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
