#!/usr/bin/env python3
"""Build a deterministic release archive from a clean reviewed worktree."""

from __future__ import annotations

import gzip
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_clean() -> bool:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat"},
    )
    return result.returncode == 0 and not result.stdout.strip()


def main() -> int:
    if not git_clean():
        print("refusing_dirty_worktree")
        return 2
    manifest = json.loads((ROOT / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    paths = [row["path"] for row in manifest["files"]] + ["PACKAGE_MANIFEST.json"]
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / f"claude-code-recover-{version}.tar.gz"
    prefix = f"claude-code-recover-{version}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for relative in sorted(paths):
            source = ROOT / relative
            info = archive.gettarinfo(str(source), arcname=f"{prefix}/{relative}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            with source.open("rb") as handle:
                archive.addfile(info, handle)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(buffer.getvalue())
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
