# Contributing

ACGM Recover handles private local history. A change that improves convenience but weakens evidence boundaries or privacy is not acceptable.

Before proposing a change:

1. Use only synthetic fixtures.
2. Do not attach or commit a real transcript, Session metadata file, account file, token, cookie, OAuth value, or private project path.
3. Preserve offline and source-read-only behavior.
4. Keep current facts, historical evidence, reconstructions, conflicts, and gaps separate.
5. Never infer actual model identity from a displayed label.
6. Add regression coverage for parser, privacy, path, atomicity, and integrity changes.

Run:

```bash
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -v
python3 scripts/release_check.py --json
python3 scripts/generate_package_manifest.py --check
```

Real-environment acceptance should report only aggregate counts and error codes. Do not publish paths or transcript content in issues.
