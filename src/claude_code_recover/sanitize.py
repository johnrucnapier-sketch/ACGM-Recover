"""Allowlist-oriented sanitization for untrusted local evidence."""

from __future__ import annotations

import math
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|cookie|authorization|oauth|access[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|session[_-]?token|id[_-]?token|password|passwd)"
)

SECRET_PATTERNS = (
    re.compile(r"(?i)sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)sk-(?:proj-)?[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)xox[baprs]-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)gh[pousr]_[A-Za-z0-9_]{12,}"),
    re.compile(r"(?i)ya29\.[A-Za-z0-9_.-]+"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9_.~+/-]+=*"),
    re.compile(r"(?i)Authorization\s*:\s*(?:Basic|Digest|Token|ApiKey)\s+[^\r\n,;]+"),
    re.compile(r"(?i)(?:Cookie|Set-Cookie)\s*:[^\r\n]*"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"),
    re.compile(r"(?i)(?:password|passwd|api[_-]?key|token|secret)\s*[=:]\s*[^\s,;]+"),
    re.compile(r"(?i)https?://[^\s/@:]+:[^\s/@]+@"),
)

UUIDISH_RE = re.compile(
    r"(?i)^(?:agent-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"(?:\.(?:jsonl|json|meta\.json))?$"
)


def contains_specific_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    size = len(value)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def looks_like_high_entropy_secret(value: str) -> bool:
    candidate = value.strip()
    if len(candidate) < 32 or len(candidate) > 512:
        return False
    if re.fullmatch(r"[0-9a-fA-F]{40,128}", candidate):
        return False
    if UUIDISH_RE.fullmatch(candidate):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_./+=~-]+", candidate):
        return False
    if not any(char.isdigit() for char in candidate):
        return False
    return _entropy(candidate) >= 4.1


def sanitize_untrusted(value: object, *, limit: int = 240, home: Path | None = None) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "")
    if home is not None:
        home_text = str(home.expanduser())
        if home_text:
            text = text.replace(home_text, "$HOME")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    words = re.split(r"([\s,;:]+)", text)
    words = ["[REDACTED_HIGH_ENTROPY]" if looks_like_high_entropy_secret(word) else word for word in words]
    text = "".join(words)
    text = "".join(char if char.isprintable() or char in "\t\n" else "�" for char in text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def sanitize_path(value: object, *, home: Path | None = None, limit: int = 360) -> str:
    text = "" if value is None else str(value)
    parts = re.split(r"([/\\])", text)
    sanitized = "".join(
        part if part in {"/", "\\"} else sanitize_untrusted(part, limit=max(80, limit), home=None)
        for part in parts
    )
    if home is not None:
        home_text = str(home.expanduser())
        if home_text:
            sanitized = sanitized.replace(home_text, "$HOME")
    if len(sanitized) > limit:
        sanitized = sanitized[:limit] + "…"
    return sanitized


def sanitize_remote_url(value: object) -> str:
    raw = "" if value is None else str(value).strip()
    if raw.startswith("git@") and ":" in raw:
        host, path = raw.split(":", 1)
        return f"{sanitize_untrusted(host.split('@')[-1], limit=180)}:{sanitize_untrusted(path, limit=300)}"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "[REDACTED_REMOTE]"
    if not parts.scheme or not parts.netloc:
        return "[LOCAL_REMOTE_REDACTED]"
    if parts.scheme not in {"http", "https", "ssh", "git"}:
        return "[REDACTED_REMOTE]"
    try:
        host = sanitize_untrusted(parts.hostname or "", limit=240)
        parsed_port = parts.port
    except ValueError:
        return "[REDACTED_REMOTE]"
    port = f":{parsed_port}" if parsed_port else ""
    path = sanitize_untrusted(parts.path, limit=300)
    return urlunsplit((parts.scheme, host + port, path, "", ""))


def sensitive_keys_present(value: object) -> bool:
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 100_000 or depth > 64:
            return True
        if isinstance(current, dict):
            for key, child in current.items():
                if SENSITIVE_KEY_RE.search(str(key)):
                    return True
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, str) and contains_specific_secret(current):
            return True
    return False
