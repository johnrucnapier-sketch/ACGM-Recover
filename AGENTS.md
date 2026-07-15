# Agent operating contract

Claude Code Recover is an independent, offline, evidence-first Claude Code project continuity recovery tool. Agents working in this repository must preserve these boundaries:

- Verify the repository root and current Git state before editing or installing.
- Clone and run `scripts/bootstrap.py` only after the user explicitly authorizes the named repository and local user installation.
- Treat installation as installation only. It does not authorize `discover`, `inspect`, `build`, reading transcript bodies, or modifying a surviving project.
- Never inspect tokens, cookies, OAuth state, account caches, provider identity, or displayed model names to choose a route.
- A `--route` argument records an explicit CLI input; the agent must still show it to the user and obtain confirmation before recovery work.
- Do not add runtime networking, telemetry, update checks, dependency downloads, or shell-based command construction.
- Keep bootstrap version behavior explicit: the same canonical version is force-reinstalled from the newly verified snapshot, older canonical installs require `--upgrade`, newer installs are never silently downgraded, active virtual environments must not receive `--user`, and an RC1 `acgm-recover` distribution must return `MIGRATION_REQUIRED` before any mutation.
- Outside virtual environments, detect the PEP 668 marker before installation. Use `--break-system-packages` only when the selected pip advertises it and the canonical install command also contains `--user`; otherwise fail closed before mutation. If that override path fails, do not run an automatic uninstall because pip uninstall has no equivalent `--user` scope.
- Use `claude-code-recover` / `python -m claude_code_recover` as canonical RC3 entrypoints. Keep `acgm-recover` / `python -m acgm_recover` working only as documented transition aliases.
- Use only synthetic fixtures in tests. Never commit real transcripts, credentials, account identifiers, or private absolute paths.
- On Windows, only bootstrap, installation, `--version`, `doctor`, and `guide` are currently in scope. Do not run or claim support for core `discover`, `inspect`, `build`, or `verify` until the Windows filesystem and Git safety port is complete.

Validation before handoff:

```text
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/release_check.py --json
python scripts/generate_package_manifest.py --check --json
python scripts/bootstrap.py --dry-run --json
```
