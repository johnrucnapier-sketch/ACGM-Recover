"""Small, dependency-free safety and serialization helpers."""

from __future__ import annotations

import hashlib
import ctypes
import errno
import json
import os
import stat
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


class RecoverError(RuntimeError):
    """A user-facing recovery failure that is safe to print."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def stat_snapshot(path: Path) -> dict[str, int]:
    value = path.stat(follow_symlinks=False)
    return {
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "size": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
        "mode": stat.S_IMODE(value.st_mode),
    }


def is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)
    except OSError:
        return False


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _normalized_path_parts(path: Path) -> tuple[str, ...]:
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in path.parts)


def _normalized_path_is_within(path: Path, parent: Path) -> bool:
    """Conservative lexical check for case/Unicode-insensitive filesystems."""

    path_parts = _normalized_path_parts(path)
    parent_parts = _normalized_path_parts(parent)
    return len(path_parts) >= len(parent_parts) and path_parts[: len(parent_parts)] == parent_parts


def _existing_ancestor_has_identity(path: Path, ancestor: Path) -> bool:
    """Compare physical directory ancestry using device/inode identity."""

    try:
        wanted = ancestor.stat()
        current = path
        while True:
            observed = current.stat()
            if (observed.st_dev, observed.st_ino) == (wanted.st_dev, wanted.st_ino):
                return True
            parent = current.parent
            if parent == current:
                return False
            current = parent
    except OSError:
        return False


def unique_existing_dirs(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        expanded = raw.expanduser()
        if expanded.is_symlink():
            continue
        path = expanded.resolve(strict=False)
        if path in seen or not path.is_dir():
            continue
        seen.add(path)
        result.append(path)
    return sorted(result, key=lambda item: str(item))


def iter_regular_files(root: Path, suffixes: tuple[str, ...]) -> Iterator[Path]:
    """Yield regular files without following directory or file symlinks."""

    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name, reverse=True)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                    continue
                if entry.is_file(follow_symlinks=False) and entry.name.endswith(suffixes):
                    yield Path(entry.path)
            except OSError:
                continue


def ensure_new_output_path(output: Path, source_roots: Iterable[Path]) -> Path:
    raw = output.expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    if raw.exists() or raw.is_symlink():
        raise RecoverError("output_exists")
    try:
        parent = raw.parent.resolve(strict=True)
    except OSError as exc:
        raise RecoverError("output_parent_invalid") from exc
    if not parent.is_dir() or parent.is_symlink():
        raise RecoverError("output_parent_invalid")
    resolved = parent / raw.name
    for source in source_roots:
        try:
            source_resolved = source.expanduser().resolve(strict=True)
        except OSError as exc:
            raise RecoverError("source_root_invalid") from exc
        if (
            _existing_ancestor_has_identity(parent, source_resolved)
            or path_is_within(resolved, source_resolved)
            or path_is_within(source_resolved, resolved)
            or _normalized_path_is_within(resolved, source_resolved)
            or _normalized_path_is_within(source_resolved, resolved)
        ):
            raise RecoverError("source_output_overlap")
    return resolved


def _darwin_acl() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.acl_get_file.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    libc.acl_get_entry.restype = ctypes.c_int
    libc.acl_init.argtypes = [ctypes.c_int]
    libc.acl_init.restype = ctypes.c_void_p
    libc.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    libc.acl_set_file.restype = ctypes.c_int
    libc.acl_free.argtypes = [ctypes.c_void_p]
    libc.acl_free.restype = ctypes.c_int
    return libc


def clear_extra_acl(path: Path) -> None:
    """Remove inherited extended ACLs from a generated bundle entry."""

    if sys.platform == "darwin":
        libc = _darwin_acl()
        acl = libc.acl_init(0)
        if not acl:
            raise RecoverError("acl_control_failed")
        try:
            if libc.acl_set_file(os.fsencode(path), 0x00000100, acl) != 0:
                raise RecoverError("acl_control_failed")
        finally:
            libc.acl_free(acl)
        return
    if sys.platform.startswith("linux"):
        absent_or_unsupported = {
            errno.ENODATA,
            getattr(errno, "ENOATTR", errno.ENODATA),
            errno.ENOTSUP,
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        }
        for name in ("system.posix_acl_access", "system.posix_acl_default"):
            try:
                os.removexattr(path, name, follow_symlinks=False)
            except OSError as exc:
                if exc.errno not in absent_or_unsupported:
                    raise RecoverError("acl_control_failed") from exc


def has_extra_acl(path: Path) -> bool:
    """Return whether an entry has an extended access/default ACL."""

    if sys.platform == "darwin":
        libc = _darwin_acl()
        ctypes.set_errno(0)
        acl = libc.acl_get_file(os.fsencode(path), 0x00000100)
        if not acl:
            error = ctypes.get_errno()
            if error in {0, errno.ENOENT}:
                return False
            raise RecoverError("acl_check_failed")
        try:
            entry = ctypes.c_void_p()
            result = libc.acl_get_entry(acl, 0, ctypes.byref(entry))
            if result == 0:
                return True
            if result == 1:
                return False
            raise RecoverError("acl_check_failed")
        finally:
            libc.acl_free(acl)
    if sys.platform.startswith("linux"):
        try:
            names = os.listxattr(path, follow_symlinks=False)
        except OSError as exc:
            if exc.errno in {errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
                return False
            raise RecoverError("acl_check_failed") from exc
        return any(name in {"system.posix_acl_access", "system.posix_acl_default"} for name in names)
    return False


def chmod_entry(path: Path, mode: int, *, directory: bool) -> None:
    """Change mode through a no-follow descriptor for Linux/macOS portability."""

    flags = os.O_RDONLY
    if directory and hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        observed = os.fstat(descriptor)
        expected = stat.S_ISDIR(observed.st_mode) if directory else stat.S_ISREG(observed.st_mode)
        if not expected:
            raise RecoverError("bundle_entry_invalid")
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)
    clear_extra_acl(path)


def safe_relative_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or not value or "\x00" in value:
        raise RecoverError("unsafe_bundle_path")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise RecoverError("unsafe_bundle_path")
    return candidate


def ensure_private_parent_dirs(root: Path, relative_parent: Path) -> Path:
    current = root
    for part in relative_parent.parts:
        if part in {"", "."}:
            continue
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if current.is_symlink() or not current.is_dir():
            raise RecoverError("bundle_parent_invalid")
        chmod_entry(current, 0o700, directory=True)
    return current


def write_exclusive(root: Path, relative: str, data: bytes, mode: int = 0o600) -> Path:
    rel = safe_relative_path(relative)
    destination = root / rel
    ensure_private_parent_dirs(root, rel.parent)
    if destination.parent.resolve(strict=True) != root.resolve(strict=True) and not path_is_within(
        destination.parent, root
    ):
        raise RecoverError("bundle_path_escape")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        clear_extra_acl(destination)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return destination


def write_json_exclusive(root: Path, relative: str, value: Any) -> Path:
    return write_exclusive(root, relative, pretty_json(value).encode("utf-8"))


def write_jsonl_exclusive(root: Path, relative: str, rows: Iterable[Any]) -> Path:
    rel = safe_relative_path(relative)
    destination = root / rel
    ensure_private_parent_dirs(root, rel.parent)
    if destination.parent.resolve(strict=True) != root.resolve(strict=True) and not path_is_within(
        destination.parent, root
    ):
        raise RecoverError("bundle_path_escape")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        clear_extra_acl(destination)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            for row in rows:
                handle.write((canonical_json(row) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return destination


def mode_string(path: Path) -> str:
    return format(stat.S_IMODE(path.stat(follow_symlinks=False).st_mode), "04o")


def atomic_rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing an existing path."""

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(-100, source_bytes, -100, destination_bytes, 0x00000001)
    else:
        raise RecoverError("atomic_noreplace_unavailable")
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise RecoverError("output_race_detected")
    raise RecoverError("atomic_noreplace_failed")
