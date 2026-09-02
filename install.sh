#!/bin/sh
set -eu

VERSION="0.10.0"
REPOSITORY="genichin/proofline"
WHEEL="proofline-${VERSION}-py3-none-any.whl"
BASE_URL="https://github.com/${REPOSITORY}/releases/download/v${VERSION}"

fail() { printf 'ProofLine installer: %s\n' "$1" >&2; exit 1; }
[ "$#" -le 1 ] || fail "Usage: install.sh [--force]"
[ "$#" -eq 0 ] || [ "$1" = "--force" ] || fail "Usage: install.sh [--force]"
for command in curl sha256sum uv mktemp cmp; do
    command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done
TOOL_DIR="$(uv tool dir)" || fail "cannot resolve uv tool directory"
if [ "${1:-}" != "--force" ] && [ -d "${TOOL_DIR}/proofline" ]; then
    fail "ProofLine is already installed; rerun with --force to replace it explicitly"
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/proofline-install.XXXXXX")"
trap 'rm -rf "$TEMP_DIR"' 0
trap 'exit 1' HUP INT TERM
curl -fsSL --retry 3 "${BASE_URL}/${WHEEL}" -o "${TEMP_DIR}/${WHEEL}"
curl -fsSL --retry 3 "${BASE_URL}/SHA256SUMS" -o "${TEMP_DIR}/SHA256SUMS"
(
    cd "$TEMP_DIR"
    sha256sum --check --strict SHA256SUMS
    sha256sum "$WHEEL" > SHA256SUMS.expected
    cmp -s SHA256SUMS SHA256SUMS.expected
) || fail "wheel checksum verification failed"

uv venv --no-config "${TEMP_DIR}/stage" || fail "cannot create staging environment"
uv pip install --no-config --python "${TEMP_DIR}/stage/bin/python" "${TEMP_DIR}/${WHEEL}" || fail "cannot stage target package"
"${TEMP_DIR}/stage/bin/python" -I -c "from importlib.metadata import version; import proofline; assert version('proofline') == '${VERSION}'" || fail "staged package verification failed"

if [ "${1:-}" = "--force" ]; then
    uv tool install --force --no-config "${TEMP_DIR}/${WHEEL}" || fail "uv tool install failed"
else
    uv tool install --no-config "${TEMP_DIR}/${WHEEL}" || fail "uv tool install failed"
fi
BIN_DIR="$(uv tool dir --bin)"
PROOFLINE="${BIN_DIR}/proofline"
[ -x "$PROOFLINE" ] || fail "installed executable not found: $PROOFLINE"
[ "$($PROOFLINE --version)" = "proofline ${VERSION}" ] || fail "post-install version verification failed"
"${TOOL_DIR}/proofline/bin/python" -I -c "from importlib.metadata import version; from pathlib import Path; import proofline; p=Path(proofline.__file__).resolve(); assert version('proofline') == '${VERSION}' and 'site-packages' in p.parts" || fail "installed distribution provenance verification failed"
printf 'ProofLine %s installed: %s\n' "$VERSION" "$PROOFLINE"
