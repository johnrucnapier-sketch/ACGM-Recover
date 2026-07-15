# Security model / 安全模型

## Protected properties

- Integrity of the original project, Git repository, and worktrees.
- Confidentiality of transcript content, credentials, account data, and local paths.
- Explainability of every recovery claim.
- Integrity of the completed bundle.
- Resistance to prompt injection contained in historical evidence.

## Threats handled by the RC

- Accidental project pollution from an overlapping output path.
- Existing-output overwrite.
- Symlink, hardlink, special-file, path traversal, and case/Unicode collision inside a bundle.
- Malformed, truncated, oversized, or changing JSON/JSONL sources.
- Git config that attempts to run fsmonitor, pager, hooks, external diff, or clean/process filters during observation.
- Unbounded Git subprocess output and oversized filter-driver configuration.
- Shared-clone/local alternate object stores omitted from the protected source boundary.
- Case-insensitive and Unicode-normalization path aliases that point back into a source tree.
- Inherited macOS/Linux ACLs that would make a nominal `0700`/`0600` bundle more widely readable.
- Credentials embedded in remote URLs, metadata fields, filenames, model labels, or other untrusted structural values.
- Historical prompts or tool output being copied into a new agent startup prompt.
- Display-label-based model/provider inference.
- A checksum being misrepresented as proof of source authenticity.

Git reads clear repository-selection/config/trace environment variables; set `GIT_OPTIONAL_LOCKS=0`; disable fsmonitor, hooks, pager, color, external diff, submodule status, terminal prompting, and every bounded effective clean/process filter driver. Git stdout is streamed under byte and time limits. Local `objects/info/alternates` chains are read with strict size/count/type/stability limits; unresolved chains fail closed. Recover does not invoke `claude` and never executes a command found in evidence.

## Installation and consent boundary

`scripts/bootstrap.py` is an offline, user-scoped installer. It captures a byte snapshot verified against `PACKAGE_MANIFEST.json`, builds the temporary wheel only from that captured allowlist, invokes pip with `--user --no-deps --no-build-isolation --no-index` (omitting `--user` inside an active virtual environment), and verifies the canonical installed module through `--version`, `doctor --no-default-sources`, and `guide --no-default-sources`. It uses argument arrays with `shell=False` and suppresses pip index/config behavior. Same-version canonical reruns force-reinstall the newly verified snapshot, canonical upgrades require `--upgrade`, and downgrades are refused. If installed-distribution metadata cannot be read exactly, bootstrap stops before mutation instead of treating the state as empty. If RC1 `acgm-recover` metadata exists, bootstrap returns `MIGRATION_REQUIRED` with a non-executable, unauthorized plan before any mutation; cross-distribution uninstall requires separate user authorization. A fresh RC2 installation provides the legacy module and CLI aliases for one RC cycle. The manifest detects mismatch against a trusted manifest; it is not a signature and cannot authenticate a maliciously replaced repository.

Installing Claude Code Recover does not authorize evidence discovery, transcript access, route selection, account inspection, or changes to a surviving project. An Agent may clone and install in one task only when the user's explicit authorization names the repository and covers both actions. The no-route guide stops at `selection_required`.

Native Windows currently supports only bootstrap, installation, the module entrypoint, `--version`, `doctor`, and `guide`. The secure recovery core is intentionally reported as unsupported, and direct `discover`, `inspect`, `build`, or `verify` calls fail before source access: Windows pipe-selector behavior, DACL and reparse-point boundaries, POSIX mode equivalents, and atomic no-replace publication do not yet have a reviewed implementation. Skipping those checks would weaken the protected properties and is not an acceptable compatibility workaround.

## Source and output rules

- Source roots must be existing directories and are read without following symlinks.
- JSONL lines, total bytes, and record counts are bounded.
- Only regular JSON/JSONL sources are parsed.
- Tool-result payloads, attachments, task text, memory prose, and project source contents are not copied.
- Output must not exist and must not overlap any source.
- Output overlap uses both physical filesystem identity and conservative case/Unicode-normalized path checks. Git common directories, worktrees, resolved local alternate object stores, and the containing Claude vendor-data boundary are protected.
- A private staging directory is completed, checksummed, verified, fsynced, and published with an atomic no-replace rename. Platforms without a no-replace primitive fail closed.
- Directories are `0700`; files are `0600`; inherited extended ACLs are removed and rejected by verification on supported macOS/Linux filesystems.

The implementation targets accidental misuse and common single-user local attacks. It does not claim to defeat a privileged attacker or a same-user adversary continuously swapping the output parent or scanner inputs between every filesystem operation. The final publish cannot replace a competing destination, but the full build is not a capability-secure, dirfd-only transaction.

## Transcript privacy

The RC is metadata-only. It parses enough of each JSON record to observe event type, identifiers, cwd/worktree relationship, lineage, counts, and sensitivity flags, then discards message text and tool payload values.

There is deliberately no “automatic redacted transcript export” flag in this RC. Regex redaction cannot guarantee zero leakage, and transcript attachments/tool results are a major credential and prompt-injection surface. A future private text layer would require a separate explicit risk acknowledgement and fail-closed review workflow.

## Share boundary

Only `share/` is generated from allowlisted facts and templates. It never consumes arbitrary transcript prose. Human-written ownership, decision, or continuation text enters it only when the field passes both `human_reviewed: true` and `share_approved: true`; even then it remains untrusted historical data. `private/SOURCE_MAP.json`, `private/FILE_PATHS.jsonl`, and `private/METADATA_SOURCE_MAP.jsonl` may reveal local structure and are not shareable by default.

## Secrets

Untrusted structural fields are length-bounded and sanitized for common API keys, OAuth/Bearer values, cookies, JWTs, private-key headers, credential URLs, secret assignments, and token-like values. The final generated tree is scanned again for known secret patterns before atomic completion.

No secret scanner can prove that arbitrary data is safe. This is why the RC does not copy transcript prose at all.

## Integrity limits

`CHECKSUMS.json` detects accidental corruption or tampering when the manifest is trusted. It is not a signature. Anyone who can rewrite both a file and the checksum manifest can create a self-consistent altered bundle. Digital signing is deferred.

## Out of scope

- Restoring an Anthropic account or inaccessible vendor-side Session.
- Recovering data that has no surviving local or external copy.
- Proving that historical statements were truthful.
- Running recovered commands or automatically continuing project work.
- Identifying, evaluating, or ranking a compatible third-party model.
- Root-level or fully compromised local-user attackers.

Security reports should contain a minimal synthetic reproduction. Never attach a real transcript or credential.
