# Packaging and release

Windows packages use self-contained `win-x64` publication and Inno Setup 7. Pin the
exact compiler version, verify the downloaded release asset and Authenticode signer,
and reject a compiler reporting another version. The installer contains the game and
importer but no original assets.

The interactive installer should discover plausible sources, allow manual selection,
explain ownership requirements, display importer progress, offer retry after failure,
and allow an explicit assetless install. Silent mode should accept an original-source
path and an explicit no-import switch. Store imported content either beside the game
when appropriate or below per-user application data; preserve it deliberately on
upgrade/uninstall only when the product policy says so.

Release workflows should be manually triggered, validate a strict semantic version,
test the exact commit, build all selected installers, smoke-test installed layouts,
and create the tag/release only after all artifacts succeed. Pin GitHub Actions by full
commit SHA, minimize permissions, disable persisted checkout credentials, and audit
workflows with zizmor.
