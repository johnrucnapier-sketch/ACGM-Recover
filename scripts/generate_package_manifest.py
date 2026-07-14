#!/usr/bin/env python3
"""Generate or verify the deterministic source package manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "PACKAGE_MANIFEST.json"
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", "build", "dist"}
EXCLUDED_NAMES = {".DS_Store", "PACKAGE_MANIFEST.json"}


def included_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise RuntimeError(f"symlink_not_allowed:{relative.as_posix()}")
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def license_for(relative: str) -> str:
    docs = {
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "README.en.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "RELEASING.md",
        "LICENSING.md",
        "LICENSE-DOCS",
    }
    if relative in docs or relative.startswith("docs/"):
        return "CC-BY-4.0"
    return "MIT"


def manifest() -> dict:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    rows = []
    for path in included_files():
        relative = path.relative_to(ROOT).as_posix()
        payload = path.read_bytes()
        rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "mode": format(stat.S_IMODE(path.stat(follow_symlinks=False).st_mode), "04o"),
                "license": license_for(relative),
            }
        )
    return {
        "schema_version": "1.0",
        "package": "acgm-recover",
        "version": version,
        "file_count": len(rows),
        "files": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    expected = manifest()
    if args.write:
        OUTPUT.write_text(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.chmod(OUTPUT, 0o644)
        result = {"ok": True, "action": "written", "file_count": expected["file_count"]}
        code = 0
    else:
        try:
            current = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = None
        ok = current == expected
        result = {"ok": ok, "action": "checked", "file_count": expected["file_count"]}
        code = 0 if ok else 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("ok" if result["ok"] else "stale")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
