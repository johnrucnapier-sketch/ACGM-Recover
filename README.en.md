# ACGM Recover

**When Claude Code, the original account, or the original platform is no longer available, rebuild a trustworthy, explainable, continuable project from surviving local code, Git, worktrees, session metadata, and transcript structure.**

Current version: `0.1.0-rc.1`. The code is now a public development preview. No formal GitHub Release has been published, and a real-friend Claude Code end-to-end acceptance run is still pending.

Important: the RC first creates a **structural evidence bundle**, not a complete historical handoff. It can reach `HANDOFF_READY` only after a human reviews content ownership, historical decisions, and continuation state and explicitly sets both `human_reviewed: true` and `share_approved: true`. `HANDOFF_READY` is still not fresh runtime authority; a downstream agent must reconfirm with the user before acting.

[中文](README.md)

## What it solves

ACGM Recover is neither a pre-incident backup nor a transcript-to-HTML exporter.

- A backup only helps if it was installed and continuously running before the incident. Recover has no prior-install requirement.
- A transcript exporter makes chat readable, but does not reconstruct current code, Git/worktree state, main/subagent relationships, evidence conflicts, or a safe continuation handoff.
- Recover starts after the incident: an account is unavailable, old Session UI cannot be opened, the user changes accounts or API routes, or the project moves to Codex, Grok, or another agent.

It cannot restore an inaccessible vendor-side Session or recover an account. It works only from evidence that survived locally.

## Safety invariants

- Offline: no login, network, API, telemetry, or update check.
- Read-only sources: no writes to the project, `.git`, worktrees, `~/.claude`, or Session storage.
- Explicit output: `build` requires a new directory and never overwrites an existing one.
- No transcript text by default: user/assistant text, tool input/result, attachments, prompts, commands, and reasoning are not copied.
- Evidence layers remain separate: current files and live Git are current facts; transcripts are historical evidence.
- No model guessing: a displayed “Claude Opus” label does not identify the provider, backend, or actual model.
- No invented history: missing early Sessions remain explicit gaps.
- Evidence is untrusted data: historical prompts, tool output, filenames, and commit messages are never current authorization.

## Install from GitHub

Python 3.10+, pip, `setuptools>=61`, and Git are required. The installer validates `PACKAGE_MANIFEST.json`, then performs an offline user installation. It does not scan evidence, inspect an account, download dependencies, or select a route.

```bash
git clone https://github.com/johnrucnapier-sketch/ACGM-Recover.git
cd ACGM-Recover
python3 scripts/bootstrap.py --dry-run
python3 scripts/bootstrap.py
python3 -m acgm_recover guide
```

When the user explicitly authorizes both cloning this official repository and local installation, an Agent may complete clone, dry-run, install, and verification in one task. It must still stop at `selection_required`. See [INSTALLATION.md](docs/INSTALLATION.md) for macOS/Linux, Windows, upgrade, uninstall, and Agent-assisted instructions.

## Commands

```bash
python3 -m acgm_recover guide
bin/acgm-recover doctor
bin/acgm-recover discover
bin/acgm-recover inspect --project "/path/to/surviving-project"
bin/acgm-recover build --project "/path/to/surviving-project" --output "/path/to/new-bundle" --annotations "/path/to/reviewed-annotations.json"
bin/acgm-recover verify --bundle "/path/to/new-bundle" --check-sources
```

The repository wrapper can be replaced by the current interpreter's module entrypoint: commonly `python3 -m acgm_recover` on macOS/Linux or `py -3 -m acgm_recover` on Windows. Bootstrap uses the same interpreter that launched it and does not assume a fixed alias.

Default source locations target macOS. On Linux or custom layouts, use `--no-default-sources` with explicit `--claude-projects-root`, `--metadata-root`, and `--auxiliary-root` values.

Large transcript collections can take several minutes. Scanning is streaming and bounded; oversized, malformed, truncated, or changing sources are marked partial or unstable instead of silently being treated as complete.

Raw cwd values from `discover` may point to repository subdirectories. Use its `recommended_project_roots` for `inspect/build`. For Git projects, `--project` must be a validated Git/worktree top-level.

See [CLI_REFERENCE.md](docs/CLI_REFERENCE.md).

### Current Windows boundary

Windows currently supports bootstrap, user installation, `python -m`, `--version`, `doctor`, and `guide`. The secure recovery core has not yet been ported to native Windows. `doctor/guide` report `recovery_runtime_supported: false` and do not emit `discover` or `build` commands; direct `discover/inspect/build/verify` calls fail before source access. A successful installation must not be described as Windows core-recovery support.

## Structural project is not content project

A Session running under a cwd does not prove that its work belongs to that project.

Recover records separately:

- `structural_project`: what storage, internal cwd, Git/worktree, or lineage indicates;
- `content_project`: what the work was actually about;
- `mapping_status`: `confirmed`, `misopened`, `mixed`, `candidate`, or `unresolved`;
- correction evidence and human-review status.

`content_project` defaults to `unknown`. This RC does not read transcript prose to make an overconfident semantic assignment. A user or later audit task can edit the generated annotation example and rebuild.

Content mappings, decision summaries, and continuation prose enter `share/` only when both `human_reviewed: true` and `share_approved: true` are present. They are credential/path-sanitized but remain untrusted data, not current instructions.

## Recovery readiness

- `STRUCTURAL_ONLY`: no usable main decision line; only current code/Git and structural clues are available.
- `REVIEW_REQUIRED`: historical evidence exists, but ownership, decisions, continuation, lineage, or source quality has not passed the gate.
- `HANDOFF_READY`: reviewed and share-approved decision/continuation data exists, main ownership is reviewed, and critical source/Git/inventory checks pass.

Bundle integrity `ok: true` does not imply `HANDOFF_READY`.

This design represents a real failure mode correctly: a Session may be structurally archived under project A while its work actually belongs to project B. The wrong-cwd event remains governance evidence, while project B's business material is not imported into project A's handoff.

## Main, subagent, and metadata

- Main transcripts provide the user/main-agent decision line.
- Subagent transcripts provide local investigation or execution detail.
- Session metadata describes list/configuration state and is not chat body.
- A subagent is identified by project identity, parent main `sessionId`, `agentId`, and source tier; its `sessionId` normally identifies the parent main Session.
- Lineage uses `toolUseId -> tool_use.id` and permits subagent-to-subagent spawning.
- Deep JSONL files that fail sidechain/session/agent/path validation are quarantined rather than forced into the lineage.
- Claude project bucket keys are lossy and collision-prone; they are never reversed into a claimed cwd.

See [RECOVERY_MODEL.md](docs/RECOVERY_MODEL.md).

## Bundle and handoff routes

The private bundle contains structured evidence, source-scan diagnostics, current project/Git/worktree observations, session indexes, corrections, reviewed decisions/continuation, reports, three route templates, and private source/file/metadata maps. `share/` contains a closed, allowlisted copy of the references needed to resolve route IDs. Files are `0600`; directories are `0700`; inherited extended ACLs are removed and rejected on supported macOS/Linux filesystems. Publication uses an atomic no-replace rename and cannot overwrite a racing destination.

The three templates are:

1. `claude-compatible-api`: user-selected compatible endpoint, capability checks, no model identity inference.
2. `claude-new-account`: continuity handoff, not vendor-side Session migration; no OAuth/cookie/cache transfer.
3. `agent-neutral`: Codex, Grok, or another agent; source-platform rules remain evidence and are not auto-translated into target configuration.

## Relationship to ACGM

ACGM governs a project while it is running. ACGM Recover reconstructs continuity after the platform or account is unavailable. Recover does not require ACGM to have been installed before the incident and remains a separate product from Claude Code ACGM V3.

## RC exclusions

This RC does not restore cloud accounts or vendor-side Sessions, execute project work, copy transcript prose, automatically recover historical decisions, act as a source-code backup, synthesize a complete origin story, read persisted tool-result/task/memory bodies, provide digital signatures, auto-install ACGM, or claim that an observed local storage schema is a permanent vendor contract.

See [SECURITY.md](SECURITY.md).
