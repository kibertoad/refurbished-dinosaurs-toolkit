---
"@scientific-method/standard-checker": minor
---

A run without `--base` whose fork point with `origin/$GITHUB_BASE_REF` (or `origin/main`) does not resolve now names the comparison with the base branch as a skipped step, so its result line reads `spec check passed with skipped steps:` and says to fetch the branch or pass `--base`. Before, the comparison was left out without a word. The new `--require-base` option fails the run in that case instead. The `check-documentation` action fetches the base branch on a pull request with enough history for the fork point and passes `--require-base`, unless its `base` input is set.
