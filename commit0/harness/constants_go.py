"""Go-specific constants and data models for commit0 Go integration."""

from enum import Enum
from pathlib import Path
from typing import Dict

from commit0.harness.constants import RepoInstance


class Language(str, Enum):
    PYTHON = "python"
    GO = "go"


class GoRepoInstance(RepoInstance):
    src_dir: str = "."
    language: Language = Language.GO


GO_SPLIT: Dict[str, list[str]] = {
    "conc_go": ["conc"],
    "Zahgon/conc": ["conc"],
}

GO_SPLIT_ALL: list[str] = [repo for repos in GO_SPLIT.values() for repo in repos]


def resolve_go_split(
    dataset_name: str,
    dataset_split: str = "test",
) -> Dict[str, list[str]]:
    """Union the hardcoded ``GO_SPLIT`` with aliases auto-derived from the dataset.

    Every dataset entry contributes up to four valid split-name aliases, each
    mapping to ``[repo_basename]``:

    * ``instance_id`` (e.g. ``"go-version_go"``)
    * ``repo`` (the canonical fork path, e.g. ``"Zahgon/go-version"``)
    * ``original_repo`` if present (e.g. ``"hashicorp/go-version"``)
    * the bare repo basename (e.g. ``"go-version"``) — required so that
      ``run_pipeline_go.sh``'s ``resolve_dataset()`` (which strips ``_dataset``
      and ``_go`` suffixes) produces a key that passes ``check_valid``.

    This is what eliminates the need to hand-edit ``GO_SPLIT`` for every new
    Go dataset JSON. Callers that still want to register curated subsets
    (``GO_SPLIT["gostandard"] = [...]``) can do so in this module and they
    will always win on key collision.

    Parameters
    ----------
    dataset_name : str
        Either a filesystem path to a dataset JSON or a HuggingFace dataset
        identifier. HuggingFace identifiers are not auto-derived here; only
        local JSON files (anything ending in ``.json`` or existing on disk)
        are parsed. For HF datasets the hardcoded ``GO_SPLIT`` is returned
        unchanged — those sets must be registered by hand.
    dataset_split : str
        Accepted for signature symmetry with ``load_dataset_from_config``;
        currently unused because only local JSON is parsed here.

    Returns
    -------
    dict[str, list[str]]
        A fresh mapping (hardcoded ``GO_SPLIT`` copy unioned with derived
        entries). Hardcoded entries take precedence over derived entries on
        key collision.

    Notes
    -----
    This intentionally avoids importing ``commit0.harness.utils`` to prevent
    a circular-import cycle (``utils`` already pulls from other parts of the
    harness). It parses JSON directly via ``json.load``.
    """
    import json
    import os
    from pathlib import Path

    del dataset_split  # accepted for future HF support; unused today

    merged: Dict[str, list[str]] = {}

    # Only parse local JSON — HF identifiers stay hardcoded.
    is_local = dataset_name.endswith(".json") or (
        os.sep in dataset_name and Path(dataset_name).exists()
    )
    if is_local:
        try:
            with open(Path(dataset_name).resolve(), "r") as f:
                data = json.load(f)
        except (OSError, ValueError):
            # Fall through to hardcoded GO_SPLIT only — caller will get a
            # BadParameter from check_valid if the user's split is unknown.
            data = None

        if isinstance(data, dict) and "data" in data:
            entries = data["data"]
        elif isinstance(data, list):
            entries = data
        else:
            entries = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            repo = entry.get("repo") or ""
            if not repo:
                continue
            basename = repo.split("/")[-1]
            aliases = {
                entry.get("instance_id") or "",
                repo,
                entry.get("original_repo") or "",
                basename,
            }
            aliases.discard("")
            for alias in aliases:
                # Hardcoded GO_SPLIT always wins; derived entries only fill gaps.
                merged.setdefault(alias, [basename])

    # Overlay hardcoded GO_SPLIT so manually-curated groups take precedence.
    for key, value in GO_SPLIT.items():
        merged[key] = value

    return merged


def resolve_go_split_all(
    dataset_name: str,
    dataset_split: str = "test",
) -> list[str]:
    """Return the flat list of repo basenames corresponding to ``resolve_go_split``."""
    merged = resolve_go_split(dataset_name, dataset_split)
    seen: set[str] = set()
    flat: list[str] = []
    for repos in merged.values():
        for repo in repos:
            if repo not in seen:
                seen.add(repo)
                flat.append(repo)
    return flat

GO_VERSION = "1.25.0"
GO_SOURCE_EXT = ".go"
GO_STUB_MARKER = '"STUB: not implemented"'
GO_TEST_FILE_SUFFIX = "_test.go"
GO_SKIP_FILENAMES = ("doc.go",)
RUN_GO_TEST_LOG_DIR = Path("logs/go_test")

SOURCE_EXT_MAP = {Language.PYTHON: ".py", Language.GO: ".go"}
STUB_MARKER_MAP = {Language.PYTHON: "    pass", Language.GO: '"STUB: not implemented"'}


__all__ = [
    "Language",
    "GoRepoInstance",
    "GO_SPLIT",
    "GO_SPLIT_ALL",
    "GO_VERSION",
    "GO_SOURCE_EXT",
    "GO_STUB_MARKER",
    "GO_TEST_FILE_SUFFIX",
    "GO_SKIP_FILENAMES",
    "RUN_GO_TEST_LOG_DIR",
    "SOURCE_EXT_MAP",
    "STUB_MARKER_MAP",
    "resolve_go_split",
    "resolve_go_split_all",
]
