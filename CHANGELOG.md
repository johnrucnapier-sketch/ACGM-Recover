# Changelog

## 0.1.0-rc.2 — Unreleased

- Renamed the product to Claude Code Recover so the recovery target is explicit: Claude Code project continuity, not an ACGM installation.
- Moved the canonical repository contract to `johnrucnapier-sketch/Claude-Code-Recover`.
- Added canonical `claude-code-recover`, `python -m claude_code_recover`, and `bin/claude-code-recover` entrypoints.
- Renamed the distribution to `claude-code-recover`; bootstrap detects RC1 `acgm-recover` metadata and returns a non-executable `MIGRATION_REQUIRED` plan before any mutation.
- Retained `acgm-recover` and `python -m acgm_recover` as documented legacy aliases for one RC cycle.
- Added independent-project and no-Anthropic-affiliation notices without changing evidence, privacy, or recovery-readiness semantics.

## 0.1.0-rc.1 — Development preview

- Added the original cross-platform `python -m acgm_recover` entrypoint, now retained as an RC2 legacy alias.
- Added an offline `guide` command that reports observable capabilities, requires explicit route input, and never inspects account/provider/model identity.
- Added an offline, user-scoped `scripts/bootstrap.py` installer with dry-run, source-manifest validation, post-install verification, and failure cleanup guidance.
- Added Agent-assisted clone/install instructions while keeping evidence discovery and route confirmation as separate authorizations.
- Added an explicit Windows boundary: bootstrap/install/doctor/guide are available, while the secure recovery core remains unsupported and emits no scan/build plan.
- Pinned repository text checkouts to LF so source-manifest verification remains deterministic on Windows Git clients.
- Added offline `doctor`, `discover`, `inspect`, `build`, and `verify` commands.
- Added current Git/worktree inspection with lock, environment, fsmonitor, pager, hook, external-diff, filter-driver, and subprocess-output defenses.
- Added bounded recursive protection for local Git alternate object stores used by shared clones.
- Added bounded Claude main/subagent/session-metadata structural scanning, including strict metadata field types and lengths.
- Added validated multi-level subagent lineage using `toolUseId -> tool_use.id`.
- Added separate structural-project and content-project claims plus human correction annotations.
- Added metadata-only, private-by-default recovery bundles with atomic completion and integrity verification.
- Added physical-inode plus case/Unicode-normalized output isolation, atomic no-replace publish, and extended-ACL removal/verification.
- Added independently derived `STRUCTURAL_ONLY`, `REVIEW_REQUIRED`, and `HANDOFF_READY` gates with exact route/reference/template closure.
- Split private source locators into bounded source, file-path, and metadata-source maps while keeping `share/` self-contained.
- Added three non-detecting continuation route templates: compatible API, new Claude account, and agent-neutral migration.
- Added secret canary, wrong-cwd, worktree, nested subagent, Git config/filter/output, shared-clone alternates, path alias, ACL, readiness-tamper, and privacy regression tests.
