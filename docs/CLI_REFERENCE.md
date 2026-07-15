# CLI reference

All commands are available through the installed cross-platform module entrypoint:

```text
PYTHON -m claude_code_recover COMMAND
```

The repository-local `bin/claude-code-recover` wrapper is available on macOS/Linux. For RC2 only, `bin/acgm-recover`, the installed `acgm-recover` command, and `PYTHON -m acgm_recover` remain legacy compatibility aliases. Installation and upgrade instructions are in [INSTALLATION.md](INSTALLATION.md).

## `guide [--route ROUTE]`

Reports only observable operating-system, Python, Git, Claude CLI, Codex CLI, and aggregate source-location presence. It performs no network request, evidence scan, account inspection, credential read, or model/provider inference.

With no route, the result is `selection_required`. `--route` accepts exactly one of:

- `claude-compatible-api`
- `claude-new-account`
- `agent-neutral`

An argument produces `explicit_cli_argument`, not `user_confirmed`; the operating Agent must still display the route and obtain confirmation before scanning. Finding `claude` or `codex` on `PATH` never selects a route.

`next_commands_argv` and the gated future-command list are templates, not authorized executable commands. They use `PYTHON`, `PROJECT`, `NEW_BUNDLE`, `ROUTE`, and `SOURCE_OPTIONS` placeholders instead of a shell string. The adjacent `command_template_contract.placeholders` object defines every placeholder; `SOURCE_OPTIONS` expands to zero or more reviewed default/custom source arguments. An Agent must resolve them only after the required user confirmation. This avoids quoting bugs, prevents silently switching evidence roots, and does not leak local absolute paths.

`guide` accepts the same source-root options as scan commands, but only reports aggregate visible-directory counts. Installation does not authorize the next command.

On native Windows this RC reports `installation_ready: true` when Python is suitable but `recovery_runtime_supported: false`; it emits no `discover` or `build` command. Direct `discover`, `inspect`, `build`, and `verify` calls return `recovery_runtime_not_supported_on_platform` before source access. This is an explicit safety boundary, not an installation failure.

## `doctor`

Checks Python, Git, secure-runtime platform support, and selected source-root availability. It performs no network request and writes nothing. `installation_ready` is separate from `recovery_runtime_supported`, so a package that installs on Windows cannot be mistaken for a working Windows recovery core.

## `discover`

Builds candidate project families from surviving internal cwd values, metadata cwd/originCwd values, Git top-level/common-directory relationships, and optionally only the `projects` keys of `~/.claude.json`.

Candidate paths prove structural clues, not content ownership. The lossy Claude bucket key is never reversed.

The output includes `recommended_project_roots`. Raw observed cwd values can point to a repository subdirectory; use a recommended Git/worktree top-level for `inspect` and `build`.

## `inspect --project PATH`

Performs the full structural scan but emits only aggregate, path-free project state, conflicts, and gaps. It does not create a bundle.

For a Git project, `PATH` must be the validated worktree top-level. `--annotations` may point to a reviewed schema-1.0 annotation file. Annotation text is not copied into the share layer unless both `human_reviewed: true` and `share_approved: true` pass validation.

## `build --project PATH --output NEW_PATH`

Builds a private recovery bundle through a `0700` staging directory and atomically renames it to the requested new path after validation.

Refusals include:

- existing output;
- output under a source project, worktree, Git common directory, local alternate object store, or Claude evidence boundary;
- case-insensitive or Unicode-normalization aliases of those source paths;
- source under the future output;
- output lock collision;
- generated secret canary;
- staging verification failure.

The completed bundle reports one of `STRUCTURAL_ONLY`, `REVIEW_REQUIRED`, or `HANDOFF_READY`. Bundle integrity and checksum validity do not promote readiness. `HANDOFF_READY` is derived again by the verifier from reviewed ownership, evidenced decisions, reviewed continuation state, current Git/inventory quality, and reference closure.

Repeat `--route` to generate a subset of route templates. When omitted, all three templates are generated. Template generation is not runtime identity detection.

## `verify --bundle PATH [--check-sources]`

Checks schema, exact allowlisted entries, missing/extra files, symlinks, hardlinks, case/Unicode path collision, POSIX mode, extended ACLs on supported platforms, size, SHA-256, route/template canonicality, reference closure, and independently derived readiness.

`--check-sources` additionally compares surviving transcript/sidecar hashes and a live Git snapshot where the private source map still resolves. Drift is a warning, because living projects can legitimately change after bundle creation.

Checksums detect damage relative to the bundle manifest. They do not authenticate the original source or defend against an attacker who can rewrite both content and manifest.

## Source options

All scan commands accept repeatable:

- `--claude-projects-root`
- `--metadata-root`
- `--auxiliary-root`
- `--no-default-sources`

Defaults target locally observed macOS Claude/Claude-3p layouts and are explicitly versioned implementation observations, not a permanent vendor API.

Metadata and JSONL parsing use per-field, per-line, per-file, record-count, source-count, and global byte budgets. Git subprocess output is also streamed with a hard byte/time limit. An exceeded budget becomes an explicit partial/unverifiable result; it is never silently treated as complete evidence.
