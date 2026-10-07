#!/usr/bin/env bash
# Installs the Kaitai Struct compiler release named by KSC_VERSION under RUNNER_TEMP, checks it
# against the SHA-256 pinned below, and exports KSC, which standard-checker reads.
# The releases are not signed, so the pinned hash is the only integrity check. A version without
# a hash here is refused.
set -euo pipefail

case "${KSC_VERSION:-}" in
  0.11) sha256=ff89389d9dc9e770d78a24af328763cb1f8e7b31ce7766c9edf10669a060f2a2 ;;
  *) echo "::error::Kaitai Struct compiler version '${KSC_VERSION:-}' has no pinned SHA-256 in setup-kaitai." >&2; exit 1 ;;
esac

tmp="$RUNNER_TEMP"
if [ "$RUNNER_OS" = Windows ]; then tmp="$(cygpath -u "$tmp")"; fi
zip="$tmp/kaitai-struct-compiler-$KSC_VERSION.zip"
# The action may have restored the zip from its cache. It is checked against the hash either way.
if [ ! -f "$zip" ]; then
  curl -fsSL --retry 3 -o "$zip" \
    "https://github.com/kaitai-io/kaitai_struct_compiler/releases/download/$KSC_VERSION/kaitai-struct-compiler-$KSC_VERSION.zip"
fi
# The hash tools differ by host: GNU sha256sum on Linux and Git Bash, a BSD sha256sum without
# GNU's --check options on current macOS images, and only shasum on older ones. Each prints the
# hash as the first field of `<tool> <file>`, so the script compares that field with the pin
# instead of relying on any tool's check mode.
if command -v sha256sum > /dev/null 2>&1; then
  actual="$(sha256sum "$zip" | awk '{print $1}')"
else
  actual="$(shasum -a 256 "$zip" | awk '{print $1}')"
fi
if [ "$actual" != "$sha256" ]; then
  echo "::error::$zip has SHA-256 $actual, expected $sha256." >&2
  exit 1
fi
unzip -q -o "$zip" -d "$tmp"
bin="$tmp/kaitai-struct-compiler-$KSC_VERSION/bin/kaitai-struct-compiler"
if [ "$RUNNER_OS" = Windows ]; then bin="$bin.bat"; fi
# Also confirms that Java is present.
"$bin" --version
if [ "$RUNNER_OS" = Windows ]; then bin="$(cygpath -w "$bin")"; fi
echo "KSC=$bin" >> "$GITHUB_ENV"
if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "path=$bin" >> "$GITHUB_OUTPUT"; fi
