"""Persist the latest session only when the user has enabled history."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from malhash.core import ALGORITHMS, METADATA_FIELDS, FileResult, FileTarget, HashValue


def load_history(path):
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if data["version"] != 1:
        raise ValueError("Unsupported history version")
    algorithms = tuple(name for name in data["algorithms"] if name in ALGORITHMS)
    metadata = tuple(name for name in data["metadata"] if name in METADATA_FIELDS)
    rows = []
    for item in data["results"]:
        target = FileTarget(path=Path(item["path"]), relative_path=Path(item["relative_path"]))
        if not target.path.is_absolute():
            raise ValueError("Invalid history path")
        rows.append(
            FileResult(
                target=target,
                hashes={name: HashValue(**entry) for name, entry in item["hashes"].items()},
                metadata=item["metadata"],
                status=item["status"],
                issue=item["issue"],
            )
        )
    return (data["inputs"], algorithms, metadata, rows)


def save_history(path, inputs, algorithms, metadata, results):
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = {
        "version": 1,
        "inputs": inputs,
        "algorithms": algorithms,
        "metadata": metadata,
        "results": [
            {
                "path": str(result.target.path),
                "relative_path": str(result.target.relative_path),
                "hashes": {
                    name: {"value": digest.value, "status": digest.status}
                    for name, digest in result.hashes.items()
                },
                "metadata": result.metadata,
                "status": result.status,
                "issue": result.issue,
            }
            for result in results
        ],
    }
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(contents, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
