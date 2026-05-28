"""URL-safe slug generation for merchant identifiers.

`slugify_base` strips non-ASCII, collapses whitespace and dashes,
lowercases. `make_unique_slug` checks against a caller-supplied
existence predicate and appends a 6-char random suffix on collision.
"""
from __future__ import annotations

import re
import secrets
import string
import unicodedata
from collections.abc import Callable


_ALLOWED = re.compile(r"[^a-z0-9-]")
_RUNS_OF_DASH = re.compile(r"-+")
_RUNS_OF_SPACE = re.compile(r"\s+")


def slugify_base(text: str) -> str:
    """Return a lowercase ASCII slug. Idempotent."""
    # NFKD decomposes accented chars; encode/decode strips non-ASCII.
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    lowered = normalized.lower().strip()
    # Replace whitespace with dashes, drop other disallowed chars.
    dashed = _RUNS_OF_SPACE.sub("-", lowered)
    cleaned = _ALLOWED.sub("", dashed)
    collapsed = _RUNS_OF_DASH.sub("-", cleaned).strip("-")
    return collapsed


def make_unique_slug(text: str, exists: Callable[[str], bool]) -> str:
    """Return slugify_base(text), or append a 6-char suffix until unique."""
    base = slugify_base(text)
    if not exists(base):
        return base
    # Up to 5 attempts; collision after that is astronomically unlikely.
    for _ in range(5):
        suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
        candidate = f"{base}-{suffix}"
        if not exists(candidate):
            return candidate
    raise RuntimeError("could not generate unique slug after 5 attempts")
