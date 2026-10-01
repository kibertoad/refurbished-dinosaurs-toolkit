# Releasing the packages

Every package is published by a GitHub Actions workflow on `main` that authenticates to its
registry through OIDC trusted publishing. No registry token is stored in the repository. Which
package is released, and at what version, depends on the ecosystem.

## npm: Changesets

`@scientific-method/executable-reader` and `@scientific-method/standard-checker`.

1. A pull request that changes one of them adds a changeset with `pnpm changeset`, choosing the
   package and the bump, and describing the change for the changelog.
2. When it merges, `release-npm.yml` runs `changesets/action`, which opens or updates the
   "Release Packages" pull request with the new versions and `CHANGELOG.md` entries. The workflow
   merges that pull request and dispatches itself.
3. The dispatched run finds no pending changesets, builds the packages, and `changeset publish`
   publishes each version not yet on npm, with provenance, and pushes its
   `<package>@<version>` tag.

The workflow runs only when a push changes `.changeset/`, an npm package or the workspace files.

## PyPI and NuGet: release labels

`scientific-method-engine` (PyPI), and `ScientificMethod.Core` with `ScientificMethod.LegacyFormats`
(NuGet, one shared version).

1. A pull request that changes `packages/scientific-method-engine/`, `packages/dotnet/` or
   `global.json` carries exactly one of the labels `release:major`, `release:minor`,
   `release:patch` or `release:skip`. `release-label.yml` fails the pull request otherwise. The
   paths are listed in `tools/release/plan.ts`.
2. When it merges, `release-python.yml` or `release-dotnet.yml` starts because its paths changed.
   Its plan job lists every pull request merged into the package's paths since the package's
   latest tag (`scientific-method-engine@X.Y.Z` or `scientific-method-dotnet@X.Y.Z`; all of history when
   there is none) and reads their labels. When all of them carry `release:skip`, the run ends.
3. Otherwise the next version is the latest tag's version (`0.0.0` when there is none) bumped by
   the largest of those labels. The publish job writes that version into the build, tests,
   publishes, then creates the tag and a GitHub release with the built files attached.

Only one release run per workflow goes at a time, and GitHub drops a waiting run when another
queues behind it. Nothing is lost that way: the run that goes ahead plans from all the pull
requests merged since the latest tag, including the dropped run's. For the same reason a
`release:skip` merge can publish a release that an earlier pull request asked for, if that earlier
release did not happen.

If a merged pull request has no release label (or more than one), the plan job fails and names
it. Fix the labels on the merged pull request and re-run the workflow.

The committed versions (`0.0.0` in `pyproject.toml` and `Directory.Build.props`) are placeholders.

If the publish job fails after uploading, rerun the failed job: the plan's version is reused,
the upload skips the existing version, and the tag and release are created.

## One-time setup

### Repository

- Labels: create `release:major`, `release:minor`, `release:patch` and `release:skip`.
- Settings > Actions > General: allow GitHub Actions to create and approve pull requests, so
  Changesets can open the release pull request.
- The auto-merge step uses `gh pr merge --admin`, so branch protection on `main` must allow the
  workflow's token to merge (as in `kibertoad/opinionated-machine`).
- Environments: create `pypi` and `nuget`. Add protection rules there if releases should wait for
  approval.
- Variables: set `NUGET_USER` to the nuget.org account that owns the packages.

### npm

- The `@scientific-method` organization exists. For each package, under Settings > Trusted
  publishing on npmjs.com, add GitHub Actions with repository
  `kibertoad/refurbished-dinosaurs-toolkit` and workflow `release-npm.yml`.
- npm only accepts a trusted publisher on a package that exists. Publish the first version of each
  package once by hand (`pnpm --filter <package> publish --access public` after `pnpm run build`),
  then add the trusted publisher and, under the package's publishing access, disallow tokens.

### PyPI

- Add a pending trusted publisher for the project `scientific-method-engine` with owner
  `kibertoad`, repository `refurbished-dinosaurs-toolkit`, workflow `release-python.yml` and
  environment `pypi`. The first run creates the project.

### NuGet

- On nuget.org, under Trusted Publishing, add a policy for repository owner `kibertoad`,
  repository `refurbished-dinosaurs-toolkit` and workflow file `release-dotnet.yml`, owned by the
  `NUGET_USER` account.
- The package IDs `ScientificMethod.Core` and `ScientificMethod.LegacyFormats` are created by the
  first push.
