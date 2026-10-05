#!/usr/bin/env bash
# Fetches the pull request's base branch into the repository that holds the checked directory, with
# enough history for HEAD's merge-base with it, so that the check can compare the spec with where
# the pull request forked.
#
#   fetch-base.sh <directory> <base-branch>
#
# actions/checkout fetches one commit by default. The script fetches origin/<base-branch> and HEAD
# with 50 commits of history, then 500, and then all of it, and stops once the merge-base resolves.
# It always exits with 0: when no merge-base resolves, it says why, and the check, which runs with
# --require-base, fails naming what is missing.
set -uo pipefail

dir=$1
branch=$2
target="refs/remotes/origin/$branch"
git_() { git -C "$dir" "$@"; }
found() { git_ merge-base HEAD "$target" > /dev/null 2>&1; }

if ! head=$(git_ rev-parse --verify -q HEAD); then
  echo "$dir is not in a git repository with a commit, so the base comparison cannot run." >&2
  exit 0
fi
if found; then exit 0; fi

refspec="+refs/heads/$branch:$target"
if [ "$(git_ rev-parse --is-shallow-repository)" != true ]; then
  git_ fetch --no-tags origin "$refspec" || true
else
  for depth in 50 500; do
    # Naming HEAD's commit fetches its history too. A server that does not serve a commit by its
    # ID, or a HEAD that exists only here, falls back to deepening the base branch alone.
    git_ fetch --no-tags --depth="$depth" origin "$refspec" "$head" ||
      git_ fetch --no-tags --depth="$depth" origin "$refspec" || true
    if found; then exit 0; fi
  done
  # --unshallow also fetches the history behind every shallow commit, HEAD's included.
  git_ fetch --no-tags --unshallow origin "$refspec" || true
fi
if ! found; then
  echo "HEAD has no merge-base with origin/$branch after fetching it, so the base comparison cannot run." >&2
fi
exit 0
