from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UsageImportStats:
    imported: int
    files: int


def read_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Live logs can end with a partially written event; retain physical line IDs.
                continue
            if isinstance(event, dict):
                yield line_number, event


def stable_id(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:32]


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return value
    return int(value) if math.isfinite(value) else None


def parse_timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def import_files(files: Iterable[str | Path], importer: Callable[[Path], int]) -> UsageImportStats:
    paths = [Path(path).expanduser() for path in files]
    return UsageImportStats(imported=sum(importer(path) for path in paths), files=len(paths))


def find_jsonl(root: Path, pattern: str, latest: int | None = None) -> list[Path]:
    files = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[:latest] if latest else files


def token_count(value: Any) -> int:
    return max(int_or_none(value) or 0, 0)
