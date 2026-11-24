"""Public types used by the hashing core."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import stat_result
from pathlib import Path
from typing import Literal

HashStatus = Literal["ok", "not_applicable", "unavailable", "error"]
FileStatus = Literal["ok", "skipped", "cancelled", "error"]
MetadataValue = str | int | float


@dataclass(frozen=True, slots=True)
class FileTarget:
    path: Path
    relative_path: Path
    _snapshot: stat_result | None = field(default=None, repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)


@dataclass(frozen=True, slots=True)
class HashValue:
    value: str | None
    status: HashStatus = "ok"


@dataclass(frozen=True, slots=True)
class FileResult:
    target: FileTarget
    hashes: dict[str, HashValue] = field(default_factory=dict)
    metadata: dict[str, MetadataValue] = field(default_factory=dict)
    status: FileStatus = "ok"
    issue: str | None = None
