"""The version of the eval datasets: a short hash of the files the evals read.

Every results file records it, so a number can be traced to the plans and meals it was measured
on. The hash covers evals/datasets/plans/*.json and evals/datasets/meals.jsonl: each file's path,
length and bytes, in sorted path order. The READMEs are left out, because rewording a description
changes no result.

After changing a dataset file, write the new version to evals/datasets/VERSION:

    uv run python evals/dataset_version.py
"""

import hashlib
from pathlib import Path

DATASETS = Path(__file__).resolve().parent / "datasets"
VERSION_FILE = DATASETS / "VERSION"
# Hex digits kept: enough to tell versions apart, short enough to read in a results table.
LENGTH = 12


def dataset_files(root: Path = DATASETS) -> list[Path]:
    """The files the version covers, in sorted path order."""
    files = [*(root / "plans").glob("*.json"), root / "meals.jsonl"]
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def dataset_version(root: Path = DATASETS) -> str:
    """The first LENGTH hex digits of a SHA-256 over the dataset files under `root`."""
    digest = hashlib.sha256()
    for path in dataset_files(root):
        data = path.read_bytes()
        # The path and the length go in before the bytes, so moving bytes from one file to the
        # next, or renaming a file, gives a different hash.
        digest.update(f"{path.relative_to(root).as_posix()}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()[:LENGTH]


def main() -> None:
    version = dataset_version()
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    print(f"{VERSION_FILE.relative_to(DATASETS.parent.parent)}: {version}")


if __name__ == "__main__":
    main()
