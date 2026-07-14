# Releasing

No stable release is authorized by this RC repository state.

A release candidate may be published only after:

1. Unit and integration tests pass on macOS and Linux with Python 3.10–3.12.
2. A real Claude Code user validates `doctor`, `discover`, `inspect`, `build`, and `verify --check-sources` against a non-sensitive disposable project.
3. The original project and Claude source trees are confirmed unchanged.
4. The generated bundle is manually checked for private-path and content leakage.
5. All three continuation routes are tested without model identity inference.
6. `PACKAGE_MANIFEST.json` is current.
7. The result remains honest about `STRUCTURAL_ONLY` / `REVIEW_REQUIRED` / `HANDOFF_READY`; checksum success is not presented as handoff readiness.
8. `scripts/bootstrap.py --dry-run --json` and a clean user installation pass without network access or evidence scanning.
9. The module entrypoint, no-route `guide`, and all three explicit route arguments are tested.
10. Windows onboarding tests confirm installation support while also confirming `recovery_runtime_supported: false` and the absence of generated `discover/build` commands.
11. The worktree is clean and the intended release commit, tag, and artifact are reviewed.

Do not describe an RC as stable. Do not publish from a dirty worktree. Do not reuse ACGM V3 tags or overwrite the original ACGM repository. Do not describe Windows bootstrap success as Windows core recovery support.
