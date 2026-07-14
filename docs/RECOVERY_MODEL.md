# Recovery model / 恢复模型

## 1. Claim-specific evidence order

ACGM Recover does not use one global “most important source” list. Evidence priority depends on the claim.

### Current-state claim / 当前状态

1. Current files, configuration, filesystem, and live Git observation.
2. Git objects, commits, tags, and tracked history.
3. Historical transcript statements.
4. Metadata, title, path, or naming heuristics.

### Historical-decision claim / 历史决策

1. An explicit user decision in the main transcript.
2. Git/current artifact corroboration that the decision was implemented.
3. A main-agent statement.
4. A subagent local conclusion.
5. Metadata/title/path clues.

Git can show that something entered version control. It cannot, by itself, prove design intent. A discussed plan is not an implemented feature.

### Session-to-project claim / Session 项目归属

1. Explicit human correction.
2. Internal transcript `sessionId`, `cwd`, branch, and event evidence.
3. Git common directory and worktree structure.
4. Metadata `cwd`, `originCwd`, `sessionId`, and time.
5. Titles and path heuristics.

## 2. Confidence and conflict are independent

Confidence:

- `verified`: direct evidence proves only the stated narrow scope.
- `corroborated`: two independent evidence types agree.
- `probable`: one strong source with no known conflict.
- `candidate`: heuristic lead requiring review.
- `unresolved`: insufficient evidence.

Conflict state:

- `none`
- `temporal_difference`: historical and current state differ at different times.
- `superseded`: later correction or stronger evidence replaces an older conclusion without deleting it.
- `contradictory`: sources disagree about the same claim and time; no automatic winner.

Copies with the same SHA-256 or duplicate scan rows are not independent corroboration.

## 3. Project family and worktrees

Git common directory plus `git worktree list --porcelain` is strong evidence that paths belong to one project family. Every worktree remains a separate development line with its own path, branch, HEAD, and dirty state. States are never merged.

A matching remote URL can suggest related clones, but does not prove a worktree relationship.

Local Git object storage is also a protected source boundary. The RC follows bounded, regular `objects/info/alternates` chains (for example a `git clone --shared` donor) and prevents bundle output inside any resolved local object store. Malformed, symlinked, unreadable, changing, or over-budget alternates fail closed.

## 4. Main and subagent lineage

Observed valid main transcript constraints:

- filename equals stable `sessionId`;
- `isSidechain == false`;
- no `agentId`;
- one file may contain several cwd and branch values.

Observed valid subagent constraints:

- path is under a parent main Session's `subagents` tree;
- `isSidechain == true`;
- stable `sessionId` equals the parent main Session;
- stable `agentId` matches the filename;
- optional `.meta.json` sidecar can corroborate `toolUseId` and `spawnDepth`.

Lineage is a graph. A `toolUseId` can point to a tool call in the main transcript or another subagent. Recover does not assume every subagent is a direct child of main.

## 5. Ordering and compaction

Raw timestamps can be out of order. Logical reconstruction must prefer original line order plus `parentUuid`/`logicalParentUuid` graphs, using timestamps only as secondary evidence.

Compact summaries are derived historical evidence. They do not override surviving original messages or current project state.

Duplicate UUID records cannot be silently overwritten; metadata differences remain a conflict or duplicate observation.

The RC does not yet reconstruct the complete message graph or recover transcript prose. It records bounded structural identifiers and lineage evidence only. The ordering rules above therefore define the evidence model for a future private text-review layer; they are not a claim that full conversational chronology has already been rebuilt.

## 6. Structural and content ownership

`structural_project` and `content_project` are separate claims.

```text
session/transcript
├── structural_project  <- cwd, storage, Git/worktree, lineage
└── content_project     <- human or evidence-backed semantic review
```

Wrong-cwd, mixed-project, and pollution-recovery Sessions are first-class cases. A structural match cannot be promoted into semantic ownership without additional evidence.

## 7. Model/provider evidence

Claude and Claude-3p storage locations can be recorded as an observed runtime storage route. A metadata or message `model` field is only an observed display label. It never establishes the actual provider, backend, or model.

Recover does not rank models or emit model-specific governance configuration.

## 8. What absence means

“Not found” means absent from the scanned surviving sources, not proof that the event never existed. Deleted cloud-only history cannot be reconstructed. Known gaps remain explicit in `gaps.jsonl` and are not filled with inferred narrative.

## 9. Recovery readiness

- `STRUCTURAL_ONLY`: no usable main decision line was structurally linked to the selected project.
- `REVIEW_REQUIRED`: relevant historical evidence exists, but ownership, decision, continuation, source, lineage, Git, or inventory gates remain incomplete.
- `HANDOFF_READY`: relevant main ownership was human-reviewed; decision and continuation fields passed both human review and share approval; decision claims have transcript or current-artifact evidence; and critical source/current-state checks passed.

The verifier re-derives readiness from the bundle contents. Rewriting the readiness label, route files, summaries, and checksums cannot legitimately promote a bundle. Even a valid `HANDOFF_READY` bundle is historical handoff data, not fresh authorization to modify the recovered project.

## 10. Current Git means observation time

`project/git_state.json` captures the live repository/worktree snapshot observed when the bundle was built. It is not the Git state from the last historical Session unless separate evidence proves that narrow claim. `verify --check-sources` can later report drift; normal continued development may make drift legitimate.
