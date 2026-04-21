# Multi-Language Integration Plan for commit0

> **Scope**: Integrate Go (and later Java, JavaScript/TypeScript, Rust) into the commit0 pipeline.
> **Approach**: Co-located parallel pipeline — Go-specific `*_go.py` files live alongside existing Python files. Zero modifications to existing Python source files.
> **Status**: Implementation-Ready
> **Date**: 2026-04-20

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Architecture Analysis](#2-current-architecture-analysis)
3. [Feasibility Assessment](#3-feasibility-assessment)
4. [Architecture Decision: Co-Located Parallel Pipeline](#4-architecture-decision-co-located-parallel-pipeline)
5. [Go Integration — Detailed Plan](#5-go-integration--detailed-plan)
6. [Multi-Language Extension Points](#6-multi-language-extension-points)
7. [File Inventory](#7-file-inventory)
8. [Migration Path](#8-migration-path)
9. [Known Risks & Open Questions](#9-known-risks--open-questions)
10. [Appendix A: Reference from commit0_go Fork](#10-appendix-a-reference-from-commit0_go-fork)
11. [Appendix B: Go Eval Script Reference](#11-appendix-b-go-eval-script-reference)
12. [Appendix C: Go Test JSON Format](#12-appendix-c-go-test-json-format)

---

## 1. Executive Summary

The existing commit0 codebase is **deeply Python-centric** — not just in the repos it benchmarks, but in every layer of its pipeline: discovery, validation, stubbing, Docker images, spec generation, test execution, evaluation, linting, and the AI agent. There is no language abstraction anywhere.

**Our approach: a co-located parallel pipeline** — Go-specific `*_go.py` files placed alongside their Python counterparts in the same directories, with zero modifications to existing Python source files. The key design insight is that `Commit0GoSpec` can **directly subclass the existing `Spec` ABC** from `commit0.harness.spec`, overriding only Go-specific behavior while inheriting all shared infrastructure (`repo_image_key`, `repo_image_tag`, `get_container_name`, `platform`).

This eliminates the need for duck typing or any modifications to the existing `Spec` class. The `Spec(ABC, dataclass)` base is sufficiently general — its abstract methods (`make_repo_script_list()`, `make_eval_script_list()`) and overridable properties (`base_image_key`, `base_dockerfile`, `repo_dockerfile`) provide all the extension points needed.

**What we import from existing code** (no modifications needed):
- `build_image()` from `docker_build.py` — language-agnostic buildx + OCI logic
- All of `docker_utils.py` — container operations, image management
- `Docker` backend from `execution_context.py` — takes `spec: Spec` (our subclass satisfies this via pure attribute access, no `isinstance` check)
- Utility functions from `utils.py` — `clone_repo`, `get_hash_string`, `generate_patch_between_commits`, logging, dataset loading
- `Spec` ABC from `spec.py` — base class for `Commit0GoSpec`
- `RepoInstance` from `constants.py` — Pydantic BaseModel for `GoRepoInstance` subclass
- `TestStatus` from `constants.py` — reusable enum (PASSED, FAILED, SKIPPED, ERROR, XFAIL)

**What we reimplement** (Python-coupled, can't reuse):
- `build_base_images()` / `build_repo_images()` — call `make_spec()` which only routes Python
- `save.main()` / `setup.main()` — filter against `SPLIT` dict (Python repos only)
- `evaluate.py` — hardcoded pytest runner
- `get_pytest_ids.py` / `run_pytest_ids.py` — entirely pytest-specific
- `lint.py` — Python-specific linters (ruff, pyright, pre-commit)
- `health_check.py` — checks for pip/Python, not Go toolchain

---

## 2. Current Architecture Analysis

### 2.1 Pipeline Stages (Python-only today)

```
Discovery → Validation → Preparation (stub) → Dataset Creation
    ↓
Setup → Build (Docker) → Test ID Generation
    ↓
3-Stage Pipeline: Draft → Evaluate → Lint → Evaluate → Test → Evaluate
```

### 2.2 Key Files & Their Language Coupling

| File | Language Coupling | Why It Can't Support Go As-Is |
|---|---|---|
| `constants.py` | `RepoInstance` has no `language` field; `SPLIT` only lists Python repos | Go repos can't be registered; no way to distinguish language |
| `spec.py` | `make_spec()` routes by `dataset_type` only; `Commit0Spec` hardcodes pytest, pip | No Go routing; eval scripts are pytest-specific |
| `dockerfiles/__init__.py` | `get_dockerfile_base()` reads `Dockerfile.python*`; `get_dockerfile_repo()` uses pip/pytest | No Go base image; no Go-aware repo dockerfile |
| `evaluate.py` | Imports `run_pytest_ids.main`; parses pytest JSON report | Can't run `go test`; can't parse Go test output |
| `run_pytest_ids.py` | Spec → eval script → pytest command → pytest report | Entire chain is pytest-specific |
| `get_pytest_ids.py` | Reads pytest `--collect-only` output from `.bz2` | Go test IDs have different format (`package/TestName`) |
| `lint.py` | Walks `.py` files; uses pre-commit + ruff + pyright | Go needs `.go` files + goimports + staticcheck + go vet |
| `cli.py` | Validates `repo_split` against `SPLIT`; calls Python harness | Go splits fail validation |
| `docker_build.py` | `build_base_images()` calls `make_spec()` (Python-only) | Can't build Go base images |
| `agent/` | `_find_files_to_edit()` collects `.py` only; stub marker is `"    pass"` | Go files ignored; Go stubs use `"STUB: not implemented"` |

### 2.3 What IS Reusable Without Modification

| Component | Reusability | Notes |
|---|---|---|
| `docker_build.py` — `build_image()` | ✅ High | buildx + OCI tarball + native load — language-agnostic |
| `docker_build.py` — `build_base_images()` / `build_repo_images()` | ❌ Not reusable | Calls `make_spec()` → Python-only routing |
| `docker_utils.py` | ✅ High | All container ops are generic |
| `execution_context.py` — `Docker` | ✅ High | Takes `spec: Spec`; uses only `spec.repo_image_key`, `spec.get_container_name()`, `spec.repo_directory` — all inherited by our subclass. No `isinstance` check — pure attribute access. |
| `execution_context.py` — `E2B` | ⚠️ Partial | Runs `pip install --upgrade pip` unconditionally — harmless for Go but wasteful |
| `health_check.py` | ❌ Not reusable | Checks pip packages and Python version |
| `save.py` | ❌ Not reusable | Filters against `SPLIT` (Python-only) |
| `setup.py` | ❌ Not reusable | Filters against `SPLIT` (Python-only) |
| `utils.py` | ✅ High | `clone_repo`, `get_hash_string`, `generate_patch_between_commits` (takes `git.Repo`, not `str`), `setup_logger`, `close_logger`, `load_dataset_from_config` |
| Dataset loading | ✅ High | Supports HuggingFace and local JSON — schema-agnostic |
| `Spec` ABC | ✅ Critical | **Subclassable** — abstract methods + overridable properties = full extension point |
| `RepoInstance` | ✅ High | Pydantic v2 `BaseModel` — subclassable for `GoRepoInstance` |
| `TestStatus` | ✅ High | Generic enum (PASSED, FAILED, SKIPPED, ERROR, XFAIL) |

---

## 3. Feasibility Assessment

### 3.1 Can Go Be Integrated Without Modifying Existing Files?

**Yes.** The `Spec(ABC, dataclass)` base class provides the necessary extension points:

| Blocker | Solution |
|---|---|
| `RepoInstance` has no `language` field | Subclass: `GoRepoInstance(RepoInstance)` adds `language` field |
| `SPLIT` is Python-only | Separate `GO_SPLIT` dict in `commit0/harness/constants_go.py` |
| `make_spec()` has no Go routing | New `make_go_spec()` factory in `commit0/harness/spec_go.py` |
| `base_image_key` hardcodes Python | Override property in `Commit0GoSpec` to return `"commit0.base.go:latest"` |
| `base_dockerfile` reads `Dockerfile.python*` | Override property to read `Dockerfile.go` |
| `repo_dockerfile` hardcodes pip/pytest | Override property to use Go-specific Dockerfile generator |
| `make_repo_script_list()` is abstract | Implement with Go-specific setup: `go mod download && go build ./...` |
| `make_eval_script_list()` is abstract | Implement with Go-specific eval: `go test -json -count=1 ./...` |
| `evaluate.py` hardcodes pytest | New `commit0/harness/evaluate_go.py` |
| `generate_patch_between_commits` doesn't filter Go files | Wrapper in `commit0/harness/patch_utils_go.py` that post-filters diff to `.go`/`go.mod`/`go.sum` files |
| `lint.py` hardcodes Python linters | New `commit0/harness/lint_go.py` |
| CLI validates against Python SPLIT | New CLI: `python -m commit0 go` or `PYTHONPATH=. python commit0/cli_go.py` |

**What we inherit for free** from `Spec` base (no override needed):
- `repo_image_key` — hash-based `commit0.repo.{repo}.{hash}:v0` ✅
- `repo_image_tag` — `wentingzhao/{repo}:v0` ✅
- `get_container_name()` — `commit0.eval.{repo}` (with optional `run_id`) ✅
- `platform` — multi-arch from `COMMIT0_BUILD_PLATFORMS` env ✅
- `setup_script` / `eval_script` — cached properties built from our abstract method implementations ✅

### 3.2 Verdict

| Approach | Feasibility | Quality | Maintenance |
|---|---|---|---|
| Modify existing files | ❌ Blocked by constraint | ⭐⭐⭐⭐⭐ | Low |
| Co-located parallel pipeline (Spec subclass) | ✅ Feasible | ⭐⭐⭐⭐ | Medium |
| Plugin/extension system | ⚠️ Over-engineered for Phase 1 | ⭐⭐⭐⭐ | Medium |

**Chosen**: Co-located parallel pipeline with `Spec` subclass. Go `*_go.py` files live alongside Python files in the same directories. Section 8 provides migration path to unified architecture.

---

## 4. Architecture Decision: Co-Located Parallel Pipeline

### 4.1 Design Principles

1. **Subclass, don't duplicate** — `Commit0GoSpec(Spec)` inherits shared infrastructure.
2. **Import, don't copy** — Reuse existing functions by importing from `commit0.harness.*`.
3. **Co-locate** — Go files sit beside their Python counterparts with `*_go` suffix naming.
4. **No new root directories** — All files go into existing `commit0/`, `tools/`, `agent/` directories.
5. **Separate config** — `.commit0.go.yaml` alongside `.commit0.yaml`.

### 4.2 Directory Structure

```
commit0/                           # EXISTING root — NO new root dirs created
├── commit0/
│   ├── __init__.py                # EXISTING — untouched
│   ├── __main__.py                # EXISTING — untouched
│   ├── cli.py                     # EXISTING — untouched
│   ├── cli_go.py                  # NEW — Go CLI entry point (typer)
│   ├── configs/
│   │   ├── base.yaml              # EXISTING — untouched
│   │   └── go.yaml                # NEW — Go Hydra config
│   └── harness/
│       ├── constants.py           # EXISTING — untouched
│       ├── constants_go.py        # NEW — GoRepoInstance, GO_SPLIT, Go constants
│       ├── spec.py                # EXISTING — untouched
│       ├── spec_go.py             # NEW — Commit0GoSpec(Spec), make_go_spec()
│       ├── evaluate.py            # EXISTING — untouched
│       ├── evaluate_go.py         # NEW — Go evaluation
│       ├── run_pytest_ids.py      # EXISTING — untouched
│       ├── run_go_tests.py        # NEW — Go test runner
│       ├── get_pytest_ids.py      # EXISTING — untouched
│       ├── get_go_test_ids.py     # NEW — Go test ID loader
│       ├── go_test_parser.py      # NEW — Parse go test -json output
│       ├── lint.py                # EXISTING — untouched
│       ├── lint_go.py             # NEW — Go linting
│       ├── build.py               # EXISTING — untouched
│       ├── build_go.py            # NEW — Go Docker build (imports build_image)
│       ├── setup.py               # EXISTING — untouched
│       ├── setup_go.py            # NEW — Go setup (imports clone_repo)
│       ├── patch_utils_go.py      # NEW — Go-filtered patch generation wrapper
│       ├── health_check.py        # EXISTING — untouched
│       ├── health_check_go.py     # NEW — Go toolchain health check
│       ├── execution_context.py   # EXISTING — untouched
│       ├── docker_build.py        # EXISTING — untouched
│       ├── docker_utils.py        # EXISTING — untouched
│       ├── utils.py               # EXISTING — untouched
│       └── dockerfiles/
│           ├── __init__.py        # EXISTING — untouched
│           ├── Dockerfile.python* # EXISTING — untouched
│           └── Dockerfile.go      # NEW — Go base image template
├── tools/
│   ├── stub.py                    # EXISTING — untouched
│   ├── stub_go.py                 # NEW — Go stubbing orchestrator
│   ├── discover.py                # EXISTING — untouched
│   ├── discover_go.py             # NEW — GitHub discovery for Go repos
│   ├── validate.py                # EXISTING — untouched
│   ├── validate_go.py             # NEW — Go repo validation
│   ├── prepare_repo.py            # EXISTING — untouched
│   ├── prepare_repo_go.py         # NEW — Go repo preparation
│   ├── create_dataset.py          # EXISTING — untouched
│   ├── create_dataset_go.py       # NEW — Go dataset creation
│   ├── generate_test_ids.py       # EXISTING — untouched
│   ├── generate_test_ids_go.py    # NEW — Go test ID generation
│   └── gostubber/                 # NEW — Go binary for AST-based stubbing
│       ├── main.go
│       ├── stubber.go
│       └── go.mod
├── agent/
│   ├── agents.py                  # EXISTING — untouched
│   ├── agents_go.py               # NEW — Go-specific aider config
│   ├── agent_utils.py             # EXISTING — untouched
│   ├── agent_utils_go.py          # NEW — Go file collection, stub detection
│   ├── run_agent.py               # EXISTING — untouched
│   ├── run_agent_go.py            # NEW — Go agent orchestration
│   ├── display.py                 # EXISTING — untouched
│   ├── display_go.py              # NEW — Go progress display (imports from display.py)
│   ├── config_go.py               # NEW — Go agent config
│   └── prompts/
│       └── go_system_prompt.md    # NEW — Go-specific system prompt
├── test_ids/                      # EXISTING — Go .bz2 files go here too
├── pyproject.toml                 # EXISTING — untouched (see Open Question #1)
├── run_pipeline.sh                # EXISTING — untouched
├── run_pipeline_go.sh             # NEW — Go 3-stage pipeline script
├── .commit0.yaml                  # EXISTING — untouched
├── .commit0.go.yaml               # NEW — Go runtime config
└── MULTILANG_INTEGRATION_PLAN.md  # This file
```

### 4.3 Import Reuse Map

```python
# From commit0.harness.docker_build
from commit0.harness.docker_build import build_image  # buildx + OCI + native load
# NOTE: build_base_images() and build_repo_images() NOT reusable — they call
# make_spec() which only routes Python. Go reimplements these wrappers in build_go.py.

# From commit0.harness.docker_utils (all exports reusable)
from commit0.harness.docker_utils import (
    exec_run_with_timeout,
    copy_to_container,
    copy_from_container,
    write_to_container,
    cleanup_container,
    image_exists_locally,
    create_container,
    get_docker_platform,
)

# From commit0.harness.execution_context
from commit0.harness.execution_context import Docker  # takes spec: Spec — our subclass works

# From commit0.harness.spec — THE KEY IMPORT
from commit0.harness.spec import Spec  # ABC base class — we subclass this

# From commit0.harness.constants
from commit0.harness.constants import RepoInstance  # Pydantic v2 BaseModel — we subclass this
from commit0.harness.constants import TestStatus     # Reuse: PASSED, FAILED, SKIPPED, ERROR, XFAIL

# From commit0.harness.utils
from commit0.harness.utils import (
    clone_repo,
    get_hash_string,
    generate_patch_between_commits,  # takes git.Repo (not str) — we wrap it in patch_utils_go.py
    get_active_branch,
    setup_logger,
    close_logger,
    load_dataset_from_config,
)
```

---

## 5. Go Integration — Detailed Plan

### 5.1 Data Model (`commit0/harness/constants_go.py`)

```python
from enum import Enum
from pathlib import Path
from typing import Dict
from commit0.harness.constants import RepoInstance

class Language(str, Enum):
    PYTHON = "python"
    GO = "go"

class GoRepoInstance(RepoInstance):
    """Go-specific repo instance. Subclasses RepoInstance to maintain type
    compatibility with Spec and ExecutionContext.

    By inheriting from RepoInstance, GoRepoInstance satisfies all existing
    type hints while adding Go-specific fields. Existing Python code ignores
    the extra fields.
    """
    src_dir: str = "."           # Go convention: source at root
    language: Language = Language.GO

GO_SPLIT: Dict[str, list[str]] = {
    "conc_go": ["conc"],
    "Zahgon/conc": ["conc"],
    # Add more Go repos as discovered
}

GO_SPLIT_ALL: list[str] = [repo for repos in GO_SPLIT.values() for repo in repos]

# Go-specific constants
GO_VERSION = "1.25.0"            # Covers all 16 candidate repos (colima/excelize require 1.25)
GO_SOURCE_EXT = ".go"
GO_STUB_MARKER = '"STUB: not implemented"'  # String literal in Go source, not a comment
GO_TEST_FILE_SUFFIX = "_test.go"
GO_SKIP_FILENAMES = ("doc.go",)  # Conventional Go doc file — skip during agent editing
RUN_GO_TEST_LOG_DIR = Path("logs/go_test")

# Mapping tables for multi-language support
SOURCE_EXT_MAP = {Language.PYTHON: ".py", Language.GO: ".go"}
STUB_MARKER_MAP = {Language.PYTHON: "    pass", Language.GO: '"STUB: not implemented"'}
```

### 5.2 Spec (`commit0/harness/spec_go.py`)

This is the core of the integration. `Commit0GoSpec` subclasses `Spec` directly — no duck typing, no modifications to existing files.

```python
import hashlib
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from commit0.harness.spec import Spec
from commit0.harness.constants import RepoInstance, ABSOLUTE_REPO_DIR, RELATIVE_REPO_DIR
from commit0.harness.constants_go import GoRepoInstance, GO_VERSION


@dataclass
class Commit0GoSpec(Spec):
    """Go-specific spec — subclasses Spec ABC from commit0.harness.spec.

    Inherits from Spec base (no modification needed):
    - repo_image_key: hash-based "commit0.repo.{repo}.{hash}:v0"
    - repo_image_tag: "wentingzhao/{repo}:v0"
    - get_container_name(run_id): "commit0.eval.{repo}" or "commit0.eval.{repo}.{run_id}"
    - platform: multi-arch from COMMIT0_BUILD_PLATFORMS env
    - setup_script: cached property from make_repo_script_list()
    - eval_script: cached property from make_eval_script_list()

    Overrides (Go-specific):
    - base_image_key: "commit0.base.go:latest"
    - base_dockerfile: reads Dockerfile.go template
    - repo_dockerfile: Go-specific repo Dockerfile (setup.sh pattern)
    - make_repo_script_list(): git clone + go mod download + go build
    - make_eval_script_list(): git apply + goimports + go test -json
    """

    @property
    def base_image_key(self) -> str:
        return "commit0.base.go:latest"

    @property
    def base_dockerfile(self) -> str:
        """Read Go base Dockerfile template."""
        dockerfile_path = Path(__file__).parent / "dockerfiles" / "Dockerfile.go"
        return dockerfile_path.read_text()

    @property
    def repo_dockerfile(self) -> str:
        """Generate Go repo Dockerfile.

        Go dependencies and build happen via setup.sh (from make_repo_script_list),
        not via Dockerfile COPY/RUN. The setup script is copied into the container
        and executed during image build.
        """
        lines = [
            f"FROM {self.base_image_key}",
            "",
            'ARG http_proxy=""',
            'ARG https_proxy=""',
            'ARG HTTP_PROXY=""',
            'ARG HTTPS_PROXY=""',
            'ARG no_proxy="localhost,127.0.0.1,::1"',
            'ARG NO_PROXY="localhost,127.0.0.1,::1"',
            "",
            "COPY ./setup.sh /root/",
            "RUN chmod +x /root/setup.sh && /bin/bash /root/setup.sh",
            "",
            "WORKDIR /testbed/",
            "",
        ]
        return "\n".join(lines)

    def make_repo_script_list(self) -> list[str]:
        """Go repo setup: clone, fetch commits, install Go deps, build."""
        repo = self.instance["repo"]
        env_setup_commit = self.instance["reference_commit"]
        base_commit = self.instance["base_commit"]
        setup = self.instance.get("setup", {}) or {}
        pre_install = setup.get("pre_install")

        setup_commands = [
            f"git clone --depth 1 -o origin https://github.com/{repo} {self.repo_directory}",
            f"chmod -R 777 {self.repo_directory}",
            f"cd {self.repo_directory}",
            f"git fetch --depth 1 origin {env_setup_commit} {base_commit}",
            f"git reset --hard {env_setup_commit}",
            "git submodule update --init --recursive 2>/dev/null || true",
            "git remote remove origin",
        ]

        # Optional apt pre-install
        if pre_install:
            if isinstance(pre_install, list):
                for cmd in pre_install:
                    setup_commands.append(cmd)
            else:
                setup_commands.append(pre_install)

        # Go-specific: download deps and verify build
        setup_commands.extend([
            "go mod download 2>/dev/null || true",
            "go build ./... 2>/dev/null || true",
            f"git reset --hard {base_commit}",
        ])

        return setup_commands

    def make_eval_script_list(self) -> list[str]:
        """Go eval script: apply patch, format, test.

        Steps:
        1. cd to repo and reset to base commit
        2. Apply the agent's patch (no --allow-empty for Go)
        3. Run goimports -w . (critical: unformatted Go code fails compilation)
        4. Run go test with JSON output
        5. Capture exit code
        """
        diff_path = "/patch.diff" if self.absolute else "../patch.diff"
        test_cmd = self.instance["test"].get("test_cmd", "go test -json -count=1 ./...")

        eval_script_list = [
            f"cd {self.repo_directory}",
            f"git reset --hard {self.instance['base_commit']}",
            f"git apply -v {diff_path}",
            "goimports -w .",
            "git status",
            f"{test_cmd} > test_output.json 2> test_stderr.txt",
            "echo $? > go_test_exit_code.txt",
        ]
        return eval_script_list


def make_go_spec(
    instance: Union[GoRepoInstance, dict],
    dataset_type: str = "commit0",
    absolute: bool = True,
) -> Commit0GoSpec:
    """Factory for Go specs.

    Args:
        instance: GoRepoInstance or dict with repo instance data
        dataset_type: always "commit0" for Go (no swebench/simple variants)
        absolute: whether to use absolute paths in container
    """
    if isinstance(instance, dict):
        repo = instance["repo"]
    else:
        repo = instance.repo

    repo_directory = ABSOLUTE_REPO_DIR if absolute else RELATIVE_REPO_DIR

    return Commit0GoSpec(
        absolute=absolute,
        repo=repo,
        repo_directory=repo_directory,
        instance=instance,
    )
```

### 5.3 Patch Utils (`commit0/harness/patch_utils_go.py`)

The existing `generate_patch_between_commits` doesn't filter by file type. Go needs to filter patches to only include Go source files, preventing LLM-generated garbage from contaminating the diff.

```python
import subprocess
import git as gitpython
from commit0.harness.utils import generate_patch_between_commits


def generate_go_patch(repo_path: str, old_commit: str, new_commit: str) -> str:
    """Generate a patch filtered to Go source files only.

    Wraps generate_patch_between_commits and post-filters the diff to only
    include files that:
    1. Existed at the base commit (prevents new garbage files)
    2. End in .go, go.mod, or go.sum

    This prevents LLM-generated non-Go files from contaminating the patch.

    NOTE: generate_patch_between_commits takes git.Repo, not str.
    """
    repo = gitpython.Repo(repo_path)
    # Get full patch
    full_patch = generate_patch_between_commits(repo, old_commit, new_commit)

    if not full_patch:
        return full_patch

    # Get list of files that existed at base commit
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", old_commit],
        capture_output=True, text=True, cwd=repo_path
    )
    base_files = set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()

    # Filter patch hunks to only Go files that existed at base
    go_extensions = (".go", "go.mod", "go.sum")
    filtered_lines = []
    include_hunk = False

    for line in full_patch.split("\n"):
        if line.startswith("diff --git"):
            # Extract file path from "diff --git a/path b/path"
            parts = line.split(" ")
            if len(parts) >= 4:
                file_path = parts[2][2:]  # strip "a/" prefix
                include_hunk = (
                    any(file_path.endswith(ext) for ext in go_extensions)
                    and file_path in base_files
                )
            else:
                include_hunk = False

        if include_hunk:
            filtered_lines.append(line)

    return "\n".join(filtered_lines) if filtered_lines else ""
```

### 5.4 Docker (`commit0/harness/dockerfiles/Dockerfile.go`)

```dockerfile
FROM ubuntu:22.04

ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive
ARG http_proxy=""
ARG https_proxy=""
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG no_proxy="localhost,127.0.0.1,::1"
ARG NO_PROXY="localhost,127.0.0.1,::1"
ARG CA_CERT_PATH="/etc/ssl/certs/ca-certificates.crt"

ARG GO_VERSION=1.25.0

ENV TZ=Etc/UTC \
    LANG=C.UTF-8 \
    http_proxy=${http_proxy} \
    https_proxy=${https_proxy} \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    SSL_CERT_FILE=${CA_CERT_PATH} \
    REQUESTS_CA_BUNDLE=${CA_CERT_PATH} \
    CURL_CA_BUNDLE=${CA_CERT_PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget build-essential jq curl locales locales-all tzdata \
    ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH=$(dpkg --print-architecture) && \
    wget -q "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" -O /tmp/go.tar.gz && \
    tar -C /usr/local -xzf /tmp/go.tar.gz && \
    rm /tmp/go.tar.gz

ENV PATH="/usr/local/go/bin:/root/go/bin:${PATH}" \
    GOPATH="/root/go" \
    GOFLAGS="-count=1" \
    GOTOOLCHAIN=local

RUN go install honnef.co/go/tools/cmd/staticcheck@latest && \
    go install golang.org/x/tools/cmd/goimports@latest

# Cross-distro SSL cert symlinks
RUN mkdir -p /etc/pki/tls/certs /etc/pki/tls /etc/pki/ca-trust/extracted/pem /etc/ssl/certs && \
    ln -sf /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt 2>/dev/null; \
    ln -sf /etc/ssl/certs/ca-certificates.crt /etc/ssl/cert.pem 2>/dev/null; \
    ln -sf /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/cert.pem 2>/dev/null; \
    ln -sf /etc/ssl/certs/ca-certificates.crt /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem 2>/dev/null; \
    ln -sf /etc/ssl/certs /etc/pki/tls/certs 2>/dev/null; \
    true

# MITM CA cert injection via BuildKit secret
RUN --mount=type=secret,id=mitm_ca,required=false \
    if [ -f /run/secrets/mitm_ca ]; then \
        cp /run/secrets/mitm_ca /usr/local/share/ca-certificates/mitm-ca.crt && \
        update-ca-certificates && \
        echo "MITM CA certificate installed successfully"; \
    else \
        echo "No MITM CA certificate found, skipping"; \
    fi
```

**Go version strategy**: Go's backward compatibility promise means a single newer toolchain compiles all older code correctly (unlike Python which needs separate runtimes for 3.10/3.12/3.13). `GO_VERSION=1.25.0` covers all 16 candidate repos. `GOTOOLCHAIN=local` prevents the Go toolchain from attempting to download different versions inside isolated containers. Bump `GO_VERSION` when candidate repos start requiring newer versions.

### 5.5 Test Execution (`commit0/harness/run_go_tests.py`)

Parallel to `run_pytest_ids.py`:
1. Create spec via `make_go_spec(instance, absolute=True)`
2. Generate patch via `generate_go_patch()` (filtered to Go files)
3. Create Docker execution context from `commit0.harness.execution_context.Docker`
4. Copy eval script + patch into container via `copy_to_container`
5. Execute via `exec_run_with_timeout`
6. Collect `test_output.json`, `test_stderr.txt`, `go_test_exit_code.txt`
7. Parse via `go_test_parser.py`

### 5.6 Go Test Parser (`commit0/harness/go_test_parser.py`)

Go's `go test -json` emits newline-delimited JSON events with actions: `run`, `pause`, `cont`, `pass`, `fail`, `skip`, `output`, `bench`.

```python
from typing import Dict, Tuple, List
from commit0.harness.constants import TestStatus  # Reuse: PASSED, FAILED, SKIPPED, ERROR

def parse_go_test_json(raw_output: str) -> Dict[str, TestStatus]:
    """Parse go test -json output into {test_id: TestStatus} map.

    Test IDs are fully-qualified: "package/TestName".
    Handles crashed packages (tests that run but never pass/fail/skip).
    """
    ...

def parse_go_test_json_with_durations(raw_output: str) -> Tuple[Dict[str, TestStatus], Dict[str, float]]:
    """Parse with timing data for performance reporting."""
    ...

def compute_go_pass_rate(results: Dict[str, TestStatus], expected_tests: List[str]) -> float:
    """Compute pass rate against expected test list."""
    ...

def parse_go_test_plain(raw_output: str) -> Dict[str, TestStatus]:
    """Fallback parser for `go test -v` output (non-JSON)."""
    ...
```

### 5.7 Evaluation (`commit0/harness/evaluate_go.py`)

```python
from commit0.harness.docker_utils import cleanup_container
from commit0.harness.run_go_tests import main as run_go_tests
from commit0.harness.go_test_parser import parse_go_test_json_with_durations, compute_go_pass_rate
from commit0.harness.constants_go import GO_SPLIT, RUN_GO_TEST_LOG_DIR

def main(dataset, repo_split, ...):
    """Go evaluation — mirrors commit0.harness.evaluate.main().

    Same ThreadPoolExecutor pattern, but:
    - Uses GO_SPLIT for repo filtering
    - Calls run_go_tests instead of run_pytest_ids
    - Parses Go test JSON instead of pytest JSON report
    """
    ...
```

### 5.8 Linting (`commit0/harness/lint_go.py`)

Go linting runs inside Docker (no pre-commit):
```python
def lint_go(repo_path: str, ...) -> LintResult:
    """Run Go linters: goimports -d, staticcheck ./..., go vet ./..."""
    # 1. goimports -d (formatting check — diff mode, no modifications)
    # 2. staticcheck ./... (static analysis)
    # 3. go vet ./... (Go's built-in vet)
    # Returns unified LintResult with file, line, message, severity
```

### 5.9 CLI (`commit0/cli_go.py`)

```python
import typer
app = typer.Typer()

@app.command()
def setup(repo_split: str = "conc_go", ...):
    """Clone Go repos, checkout branch, write .commit0.go.yaml.
    Reimplements clone+checkout using commit0.harness.utils.clone_repo
    (cannot wrap setup.main — it filters against SPLIT, Python-only)."""

@app.command()
def build(num_workers: int = 8, ...):
    """Build Go base + repo Docker images."""

@app.command()
def test(repo: str, test_ids: str = "", ...):
    """Run Go tests for a specific repo."""

@app.command()
def evaluate(repo_split: str = "conc_go", ...):
    """Evaluate Go repos."""

@app.command()
def lint(repo: str, ...):
    """Lint Go repos (goimports, staticcheck, go vet)."""

@app.command()
def save(repo_or_repo_split: str, ...):
    """Save Go repo changes. Reimplements save logic
    (cannot wrap save.main — it filters against SPLIT, Python-only)."""

@app.command()
def get_tests(repo_or_repo_split: str, ...):
    """Get Go test IDs for a repo."""
```

**Entry point**: `python commit0/cli_go.py` or via PYTHONPATH.

**Packaging note**: The existing `pyproject.toml` has `packages = ["commit0", "agent"]`. Since `cli_go.py` lives inside `commit0/`, it's auto-included in the wheel. The `tools/` directory is NOT in packages (matching existing behavior — tools are scripts, not packaged modules). Files in `agent/` are also auto-included. See Open Question #1 for details.

### 5.10 Stubbing (`tools/stub_go.py` + `tools/gostubber/`)

The `gostubber` is a Go binary that performs AST-based stubbing:
- Parses `.go` files via `go/ast`, identifies exported functions (uppercase first letter)
- Replaces function bodies with zero-value returns + `"STUB: not implemented"` string literal
- **Preserves** unexported functions (Go tests may call them indirectly)
- Uses `go/printer` for syntax-safe output

Key difference from Python stubbing:
- Python: replaces body with `pass`, removes private functions
- Go: replaces with zero-value return, keeps unexported functions, adds `"STUB: not implemented"` string literal

### 5.11 Agent Integration (`agent/*_go.py`)

**Key differences from Python agent:**

| Aspect | Python Agent | Go Agent |
|---|---|---|
| File collection | `.py` files | `.go` files (exclude `_test.go`, `vendor/`) |
| Stub detection | `"    pass"` | `"STUB: not implemented"` string literal |
| Function extraction | `ast.parse` | Go regex or `go doc` subprocess |
| Dependency ordering | Python import graph | Alphabetical (Go has no circular imports) |
| Lint command | `ruff check` + `pyright` | `goimports -d` + `staticcheck ./...` + `go vet ./...` |
| System prompt | Python conventions, pytest | Go conventions, `if err != nil`, `testing.T` |
| Test IDs | `get_pytest_ids` from `.bz2` | `get_go_test_ids` from `.bz2` (Go format) |

**System prompt** (`agent/prompts/go_system_prompt.md`) must include:
- Go idioms: error handling, interfaces, goroutines, channels
- Go testing patterns: table-driven tests, `testing.T`, subtests
- Stub marker: "Functions containing `"STUB: not implemented"` need implementation"
- Package context and exported API surface
- Actual function signatures that need implementation

---

## 6. Multi-Language Extension Points

### 6.1 Language Extension Template

For each new language `{LANG}`, create the following co-located files:

| Component | File | Purpose |
|---|---|---|
| Constants | `commit0/harness/constants_{lang}.py` | `{Lang}RepoInstance`, `{LANG}_SPLIT` |
| Spec | `commit0/harness/spec_{lang}.py` | `Commit0{Lang}Spec(Spec)`, `make_{lang}_spec()` |
| Dockerfile | `commit0/harness/dockerfiles/Dockerfile.{lang}` | Base image template |
| Test runner | `commit0/harness/run_{lang}_tests.py` | Language-specific test execution |
| Test parser | `commit0/harness/{lang}_test_parser.py` | Parse test output |
| Evaluation | `commit0/harness/evaluate_{lang}.py` | Language-specific eval |
| Lint | `commit0/harness/lint_{lang}.py` | Language-specific linters |
| CLI | `commit0/cli_{lang}.py` | Language-specific CLI |
| Agent | `agent/agents_{lang}.py`, `agent/agent_utils_{lang}.py` | Language-aware agent |

### 6.2 Language-Specific Details

#### Java
| Aspect | Details |
|---|---|
| Test framework | JUnit 5 (`mvn test` / `gradle test`) |
| Test output | Surefire XML reports |
| Base image | Ubuntu + OpenJDK + Maven/Gradle |
| Linters | Checkstyle, SpotBugs, ErrorProne |
| Stub marker | `throw new UnsupportedOperationException("STUB")` |
| Test IDs | `package.Class#testMethod` |

#### JavaScript / TypeScript
| Aspect | Details |
|---|---|
| Test framework | Jest, Vitest, Mocha |
| Test output | `--json` / `--reporter=json` |
| Base image | Ubuntu + Node.js (LTS) |
| Linters | ESLint, Prettier, `tsc --noEmit` |
| Stub marker | `throw new Error("STUB")` |
| Test IDs | `describe > it` paths |

#### Rust
| Aspect | Details |
|---|---|
| Test framework | `cargo test` |
| Test output | `cargo test -- --format json` (nightly) |
| Base image | Ubuntu + rustup + stable toolchain |
| Linters | `cargo clippy`, `rustfmt --check` |
| Stub marker | `todo!("STUB")` |
| Test IDs | `module::test_name` |

### 6.3 Shared Abstractions (Future — When Constraint Lifted)

```python
# commit0/harness/lang/registry.py
class LanguageRegistry:
    """Registry pattern for language-specific implementations."""
    _specs: Dict[Language, Type[Spec]] = {}
    _runners: Dict[Language, Callable] = {}

    @classmethod
    def register_spec(cls, lang: Language, spec_class: Type[Spec]):
        cls._specs[lang] = spec_class

    @classmethod
    def get_spec(cls, lang: Language) -> Type[Spec]:
        return cls._specs[lang]

# In commit0/harness/constants_go.py (or an __init__ module)
LanguageRegistry.register_spec(Language.GO, Commit0GoSpec)
```

---

## 7. File Inventory

### 7.1 New Files for Go Integration

| # | File Path | LOC Est. | Imports From Existing |
|---|---|---|---|
| 1 | `commit0/cli_go.py` | ~450 | commit0.harness.utils |
| 2 | `commit0/configs/go.yaml` | ~40 | — |
| 3 | `commit0/harness/constants_go.py` | ~120 | commit0.harness.constants.RepoInstance |
| 4 | `commit0/harness/spec_go.py` | ~200 | commit0.harness.spec.Spec |
| 5 | `commit0/harness/evaluate_go.py` | ~200 | commit0.harness.docker_utils |
| 6 | `commit0/harness/run_go_tests.py` | ~250 | commit0.harness.execution_context, docker_utils |
| 7 | `commit0/harness/get_go_test_ids.py` | ~50 | — |
| 8 | `commit0/harness/go_test_parser.py` | ~175 | commit0.harness.constants.TestStatus |
| 9 | `commit0/harness/lint_go.py` | ~150 | commit0.harness.docker_utils |
| 10 | `commit0/harness/build_go.py` | ~150 | commit0.harness.docker_build.build_image |
| 11 | `commit0/harness/setup_go.py` | ~100 | commit0.harness.utils.clone_repo |
| 12 | `commit0/harness/patch_utils_go.py` | ~60 | commit0.harness.utils.generate_patch_between_commits |
| 13 | `commit0/harness/health_check_go.py` | ~60 | commit0.harness.docker_utils |
| 14 | `commit0/harness/dockerfiles/Dockerfile.go` | ~60 | — |
| 15 | `tools/discover_go.py` | ~200 | — |
| 16 | `tools/validate_go.py` | ~200 | — |
| 17 | `tools/stub_go.py` | ~150 | — |
| 18 | `tools/gostubber/main.go` | ~150 | — |
| 19 | `tools/gostubber/stubber.go` | ~250 | — |
| 20 | `tools/gostubber/go.mod` | 5 | — |
| 21 | `tools/prepare_repo_go.py` | ~200 | — |
| 22 | `tools/create_dataset_go.py` | ~100 | — |
| 23 | `tools/generate_test_ids_go.py` | ~100 | — |
| 24 | `agent/run_agent_go.py` | ~200 | — |
| 25 | `agent/agent_utils_go.py` | ~250 | — |
| 26 | `agent/agents_go.py` | ~150 | — |
| 27 | `agent/display_go.py` | ~50 | agent.display |
| 28 | `agent/config_go.py` | ~80 | — |
| 29 | `agent/prompts/go_system_prompt.md` | ~150 | — |
| 30 | `run_pipeline_go.sh` | ~200 | — |
| 31 | `.commit0.go.yaml` | ~20 | — |

**Total: 31 new files, ~4,170 estimated LOC**

### 7.2 Existing Files — NOT Modified

Every file under `commit0/`, `tools/`, `agent/`, and root scripts remains untouched. No `__init__.py` files need modification — `commit0/harness/__init__.py` does not exist, `agent/__init__.py` does not exist, and `commit0/__init__.py` contains only version metadata with no wildcard imports.

**⚠️ `pyproject.toml` exception**: See Open Question #1.

---

## 8. Migration Path

When the "no modification" constraint is lifted:

### Phase 1: Add Language Abstraction

1. `constants.py`: Add `Language` enum, `language` field to `RepoInstance`, merge `GO_SPLIT` into `SPLIT`
2. `spec.py`: Add language routing to `make_spec()`, register `Commit0GoSpec`
3. `evaluate.py`: Route to language-specific test runner based on `repo.language`
4. `cli.py`: Accept `--language` flag
5. `dockerfiles/__init__.py`: Language-aware Dockerfile generation
6. `lint.py`: Route to language-specific linters

### Phase 2: Extract Shared Abstractions

1. Create `commit0/harness/lang/` with `LanguageRegistry`
2. Each language registers implementations at import time
3. `make_spec()` → `LanguageRegistry.get_spec(language)(instance)`

### Phase 3: Consolidate

1. Move Go implementations into `commit0/harness/` as registered plugins (already co-located — just wire up)
2. Unify `tools/` with language flags
3. Unify CLI: single `commit0` command with `--language` flag
4. Deprecate standalone Go CLI

### Phase 4: Template New Languages

With registry in place, adding Java/JS/TS/Rust becomes:
1. Create implementations (follow Section 6.1 template)
2. Register in `LanguageRegistry`
3. Add constants, dockerfiles, test parser
4. Done — no existing file modifications needed

---

## 9. Known Risks & Open Questions

### 9.1 Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | **Code duplication** — parallel pipeline duplicates ~40% of eval/build logic | High maintenance | Import reusable functions; minimize duplication |
| 2 | **Drift** — Python pipeline evolves, Go falls behind | Silent breakage | CI running both pipelines |
| 3 | **Dataset schema** — Go needs `language` field Python datasets lack | Incompatible datasets | HuggingFace configs for language splits |
| 4 | **gostubber binary** — Requires Go toolchain to build | Build complexity | Docker-based build |
| 5 | **Go module proxy** — `go mod download` may fail without proxy config | Build failures | `GOPROXY` in Dockerfile |
| 6 | **Test ID format** — Go (`package/TestName`) vs pytest (`file::class::test`) | Mapping confusion | Separate test ID storage |

### 9.2 Open Questions

1. **Should `pyproject.toml` be modified?** — New `*_go.py` files inside `commit0/` and `agent/` are auto-included since `packages = ["commit0", "agent"]`. Files in `tools/` are NOT in packages (matching existing behavior — tools are scripts, not packaged modules). `pyproject.toml` line 58 (`include = ["**/commit0", "**/agent", "**/tools"]`) already covers pyright. **Recommendation**: No change needed for basic functionality. If a pip-installable entry point is desired later, add `"commit0_go"` script to `[project.scripts]`.

2. **Where do Go datasets live?** — **Recommendation**: Local JSON with `language: "go"` field in `commit0/data/`. `load_dataset_from_config()` supports local JSON paths.

3. **Go version management** — Go's backward compat means one newer toolchain handles all older code. `GO_VERSION=1.25.0` covers all 16 candidate repos. `GOTOOLCHAIN=local` prevents download attempts. Bump when repos require newer.

4. **gostubber distribution** — The `tools/gostubber/` subdirectory contains Go source that compiles to a binary. **Recommendation**: Docker-based build, or `go build` during setup.

5. **Agent prompts directory** — `agent/prompts/` does not currently exist. Create it with `go_system_prompt.md`. No `__init__.py` needed (not a Python package).

6. **CI integration** — **Recommendation**: Mock dataset with small Go repo (e.g., conc) for integration testing.

7. **Docker image naming** — Go uses `commit0.base.go:latest` (no version in key, unlike Python's `commit0.base.python3.12:latest`) and `commit0.eval.{repo}` (inherited from Spec — no language segment). Repos in both Python and Go splits would have container name collisions. Mitigation: don't overlap splits, or override `get_container_name()` in `Commit0GoSpec` to include a `.go` segment.

---

## 10. Appendix A: Reference from commit0_go Fork

The `commit0_go/` directory in the repository is a **full fork** of commit0 where Go support was added by **modifying existing files in-place** — a different approach from this plan's co-located parallel pipeline.

### Fork's Approach vs. This Plan

| Aspect | Fork (commit0_go/) | This Plan |
|---|---|---|
| Architecture | Modifies existing files | Co-located `*_go.py` files, no modifications |
| Root directories | Separate `commit0_go/` repo clone | No new root dirs — uses existing `commit0/`, `tools/`, `agent/` |
| `Commit0GoSpec` | Inherits from modified `Spec` | Inherits from unmodified `Spec` |
| `RepoInstance` | Adds `language` field directly | Subclasses as `GoRepoInstance` |
| `SPLIT` | Merges Go entries into existing dict | Separate `GO_SPLIT` dict |
| `evaluate.py` | Dual-mode Python/Go routing in one file | Separate `evaluate_go.py` alongside `evaluate.py` |
| `make_spec()` | Modified to route Go | Separate `make_go_spec()` |
| `dockerfiles/` | Modified `__init__.py` for language dispatch | Separate `Dockerfile.go`, `spec_go.py` handles generation |
| `utils.py` | Added `language` kwarg to `generate_patch_between_commits` | Wrapper in `patch_utils_go.py` |
| Pydantic compat | Uses deprecated `.dict()`, `__annotations__` | Uses standard Pydantic v2 methods |
| CLI | Modifies existing | Separate `cli_go.py` |

### Fork Files Modified

- `commit0/harness/constants.py` — Language enum, language field on RepoInstance, Go constants
- `commit0/harness/spec.py` — language field on Spec base, Commit0GoSpec subclass, language-conditional Dockerfiles
- `commit0/harness/evaluate.py` — dual-mode Python/Go test routing
- `commit0/harness/dockerfiles/__init__.py` — language-conditional Dockerfile generation
- `commit0/harness/utils.py` — language kwarg on `generate_patch_between_commits`

### Fork Files Added

- `commit0/harness/run_go_tests.py`
- `commit0/harness/go_test_parser.py`
- `commit0/harness/get_go_test_ids.py`
- `commit0/harness/dockerfiles/Dockerfile.go`

### Key Fork Constants (for reference)

```python
GO_SOURCE_EXT = ".go"
GO_STUB_MARKER = '"STUB: not implemented"'
GO_TEST_FILE_SUFFIX = "_test.go"
GO_SKIP_FILENAMES = ("doc.go",)
GO_VERSION = "1.25.0"  # In Dockerfile.go: ARG GO_VERSION=1.25.0
```

---

## 11. Appendix B: Go Eval Script Reference

The eval script is generated by `Commit0GoSpec.make_eval_script_list()`. Approximate bash equivalent:

```bash
#!/bin/bash
set -uxo pipefail
cd /testbed
git reset --hard {base_commit}
git apply -v /patch.diff
goimports -w .
git status
go test -json -count=1 ./... > test_output.json 2> test_stderr.txt
echo $? > go_test_exit_code.txt
```

Key notes:
- No `--allow-empty` on `git apply` (unlike Python's `Commit0Spec`)
- `goimports -w .` is critical — unformatted Go code fails compilation
- Test command uses `-json` for machine-readable output
- Stderr is captured separately in `test_stderr.txt`
- `{test_cmd}` comes from `instance["test"].get("test_cmd", "go test -json -count=1 ./...")`

---

## 12. Appendix C: Go Test JSON Format

Go's `go test -json` emits newline-delimited JSON events:

```json
{"Time":"2024-01-01T00:00:00Z","Action":"run","Package":"github.com/user/repo/pkg","Test":"TestFoo"}
{"Time":"2024-01-01T00:00:01Z","Action":"output","Package":"github.com/user/repo/pkg","Test":"TestFoo","Output":"--- PASS: TestFoo (0.01s)\n"}
{"Time":"2024-01-01T00:00:01Z","Action":"pass","Package":"github.com/user/repo/pkg","Test":"TestFoo","Elapsed":0.01}
```

Actions: `run`, `pause`, `cont`, `pass`, `fail`, `skip`, `output`, `bench`. Test IDs are fully-qualified: `package/TestName`.
