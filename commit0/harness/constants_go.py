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
]
