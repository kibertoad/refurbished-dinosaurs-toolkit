#!/usr/bin/env bash
# Runs the standard checker with --check and the action's inputs, which arrive as DOC_* variables.
#
# On a pull request (GITHUB_BASE_REF set) with no base input, it first fetches the base branch with
# enough history for HEAD's merge-base (fetch-base.sh), and passes --require-base, so a comparison
# with the base branch that cannot run fails the check. Elsewhere, such as on a push, a fork point
# that does not resolve leaves the comparison out and the result line names it as skipped.
set -euo pipefail

here=$(dirname "${BASH_SOURCE[0]}")
args=(--root "$DOC_ROOT" --check --code "$DOC_CODE" --references "$DOC_REFERENCES")
if [ "$DOC_REQUIRE_KSC" = "true" ]; then args+=(--require-ksc); fi
if [ -n "$DOC_IMAGES" ]; then args+=(--images "$DOC_IMAGES"); fi
if [ -n "$DOC_MAX_RANGE" ]; then args+=(--max-range "$DOC_MAX_RANGE"); fi
if [ -n "$DOC_DATA_DIRS" ]; then args+=(--data-dirs "$DOC_DATA_DIRS"); fi
# Set but empty names no rebuild directory, which turns the check of spec paths off.
if [ -n "${DOC_REBUILD+set}" ]; then args+=(--rebuild "$DOC_REBUILD"); fi
if [ "${DOC_SCHEDULED_GENERATION:-false}" = "true" ]; then args+=(--scheduled-generation); fi
if [ -n "$DOC_BASE" ]; then
  args+=(--base "$DOC_BASE")
elif [ -n "${GITHUB_BASE_REF:-}" ]; then
  bash "$here/fetch-base.sh" "$DOC_ROOT" "$GITHUB_BASE_REF"
  args+=(--require-base)
fi
# Node runs the TypeScript source directly; it imports only Node built-ins.
node "$here/../../packages/standard-checker/src/standard-checker.ts" "${args[@]}"
