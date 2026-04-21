# Docker Image Build Flow: Python vs Go

Technical reference documenting how commit0 builds Docker images for Python and Go repositories, why the flows differ, and the architectural rationale.

---

## Overview

commit0 builds two Docker images per repository:

1. **Base image** — Language toolchain (Python runtime or Go compiler + tools)
2. **Repo image** — Repository code with dependencies pre-installed, ready for evaluation

The repo image build involves cloning the repository, installing dependencies, and landing on the "stubbed" code (function bodies replaced with stubs for the LLM agent to implement).

The build flow **differs between Python and Go** due to a fundamental difference in how each language handles stubbed code.

---

## Python Flow

### Architecture: Git in setup.sh, deps in Dockerfile layers

```
┌─────────────────────────────────────────────────────┐
│ Dockerfile (repo image)                              │
│                                                      │
│ FROM commit0.base.python3.12:latest                  │
│                                                      │
│ ┌─────────────────────────────────────────────────┐  │
│ │ Layer 1: COPY + RUN setup.sh                    │  │
│ │                                                 │  │
│ │  git clone --depth 1 (shallow, fast)            │  │
│ │  git fetch --depth 1 origin {ref} {base}        │  │
│ │  git reset --hard {reference_commit}            │  │
│ │  git submodule update                           │  │
│ │  git remote remove origin                       │  │
│ │  git reset --hard {base_commit}  ← STUBBED CODE │  │
│ └─────────────────────────────────────────────────┘  │
│                                                      │
│ ┌─────────────────────────────────────────────────┐  │
│ │ Layer 2: apt-get install (system deps)          │  │
│ │ Layer 3: pip install dep1 dep2 (pip_packages)   │  │
│ │ Layer 4: pip install -e "." (install_cmd)       │  │
│ │ Layer 5: pip install pytest pytest-cov          │  │
│ │ Layer 6: pip freeze > .dep-manifest.txt         │  │
│ └─────────────────────────────────────────────────┘  │
│                                                      │
│ WORKDIR /testbed/                                    │
└─────────────────────────────────────────────────────┘
```

### Key detail: pip install runs on STUBBED code

After `setup.sh` completes, the working directory contains stubbed code (`base_commit`). The subsequent Dockerfile layers run `pip install -e "."` against this stubbed code.

**This works because Python stubs preserve package metadata.** When Python functions are stubbed (bodies replaced with `pass`), the following remain intact:
- `pyproject.toml` / `setup.py` / `setup.cfg` — package metadata, dependencies
- `__init__.py` files — package structure
- Import statements — module relationships
- Class definitions, type hints — only function bodies change

So `pip install -e "."` reads the dependency list from metadata files and installs everything correctly, even though function bodies are `pass`.

### setup.sh contents (Python)

```bash
#!/bin/bash
set -euxo pipefail
git clone --depth 1 -o origin https://github.com/{repo} /testbed
chmod -R 777 /testbed
cd /testbed
git fetch --depth 1 origin {reference_commit} {base_commit}
git reset --hard {reference_commit}
git submodule update --init --recursive 2>/dev/null || true
git remote remove origin
git reset --hard {base_commit}
```

Source: `commit0/harness/spec.py`, `Commit0Spec.make_repo_script_list()` (lines 146-156)

### Dockerfile generation (Python)

Source: `commit0/harness/dockerfiles/__init__.py`, `get_dockerfile_repo()` (lines 115-205)

Generates separate `RUN` layers for each dependency installation step. Docker layer caching means rebuilding the image only re-runs layers that changed.

---

## Go Flow

### Architecture: Git AND deps in setup.sh (single layer)

```
┌─────────────────────────────────────────────────────┐
│ Dockerfile (repo image)                              │
│                                                      │
│ FROM commit0.base.go:latest                          │
│                                                      │
│ ┌─────────────────────────────────────────────────┐  │
│ │ Layer 1: COPY + RUN setup.sh                    │  │
│ │                                                 │  │
│ │  git clone (full clone of fork)                 │  │
│ │  git fetch origin commit0_all                   │  │
│ │  git checkout {reference_commit} ← WORKING CODE │  │
│ │  git submodule update                           │  │
│ │  git remote remove origin                       │  │
│ │  [pre_install: apt-get for CGO deps]            │  │
│ │  go mod download          ← DEPS FROM WORKING   │  │
│ │  go build ./...           ← BUILD CACHE WARM     │  │
│ │  git reset --hard {base_commit}  ← STUBBED CODE │  │
│ └─────────────────────────────────────────────────┘  │
│                                                      │
│ WORKDIR /testbed/                                    │
└─────────────────────────────────────────────────────┘
```

### Key detail: go mod download runs on WORKING code, BEFORE reset to stubbed

Go stubs **break compilation**. When Go functions are stubbed:
- Function bodies are replaced with zero-value returns + `"STUB: not implemented"` string literal
- The stubbed code **may not compile** (`go build ./...` fails) because:
  - Return types may be complex structs that need proper initialization
  - Interface satisfaction may be broken
  - Unused imports may cause compilation errors

Therefore, Go **must install dependencies while the working code is checked out**, then reset to stubbed code afterward. The Go module cache (`$GOPATH/pkg/mod/`) and build cache (`$HOME/.cache/go-build/`) survive the `git reset` because they're stored outside the working tree.

### setup.sh contents (Go)

```bash
#!/bin/bash
set -euxo pipefail
git clone -o origin https://github.com/{repo} /testbed
chmod -R 777 /testbed
cd /testbed
git fetch origin commit0_all
git checkout {reference_commit}
git submodule update --init --recursive 2>/dev/null || true
git remote remove origin
go mod download 2>/dev/null || true
go build ./... 2>/dev/null || true
git reset --hard {base_commit}
```

Source: `commit0/harness/spec_go.py`, `Commit0GoSpec.make_repo_script_list()` (lines 47-79)

### Dockerfile generation (Go)

Source: `commit0/harness/spec_go.py`, `Commit0GoSpec.repo_dockerfile` property (lines 27-45)

The Go Dockerfile is minimal — only `COPY setup.sh` + `RUN setup.sh` + `WORKDIR`. All dependency installation happens inside `setup.sh`, not in separate Dockerfile layers. This is because Go deps are managed by the Go toolchain (`go mod download`), not by a system package manager.

---

## Why the Flows Differ

| Aspect | Python | Go |
|--------|--------|-----|
| Stub impact on deps | Stubs preserve `pyproject.toml` — `pip install` works on stubbed code | Stubs break compilation — `go build` fails on stubbed code |
| When deps install | AFTER reset to stubbed code (Dockerfile layers) | BEFORE reset to stubbed code (inside setup.sh) |
| Dep installation method | `pip install -e "."` reads metadata files | `go mod download` reads `go.mod` + `go.sum` |
| Where deps are cached | Python venv / site-packages (inside working tree) | `$GOPATH/pkg/mod/` + `$HOME/.cache/go-build/` (outside working tree) |
| Dep cache survives git reset? | Yes — venv is in working tree but not tracked by git | Yes — Go caches are outside working tree entirely |
| Clone strategy | `--depth 1` (shallow) — commits are on default branch | Full clone + `fetch origin commit0_all` — commits are on a non-default branch |
| Dockerfile complexity | Multi-layer (apt + pip + install_cmd + pytest + freeze) | Single layer (setup.sh does everything) |

### The fundamental difference

**Python**: Stubbed code is still installable → deps can be installed after stubbing → separation of concerns (git in setup.sh, deps in Dockerfile layers)

**Go**: Stubbed code is not compilable → deps must be installed before stubbing → everything in setup.sh (git + deps + reset)

This is not a bug or an oversight — it's an inherent language difference. Go's compilation model requires all code to be valid for `go build` to succeed, while Python's interpreted nature allows `pip install` to work even with `pass`-stubbed function bodies.

---

## Evaluation Flow (identical structure, different test runners)

Both languages follow the same eval pattern:

```
1. Reset to stubbed code (base_commit)
2. Apply the agent's patch (git apply)
3. Format code (Go only: goimports -w .)
4. Run tests
5. Capture exit code
```

### Python eval script
```bash
cd /testbed
git reset --hard {base_commit}
git apply --allow-empty -v /patch.diff
git status
pytest --json-report ... > test_output.txt 2>&1
echo $? > pytest_exit_code.txt
```

### Go eval script
```bash
cd /testbed
git reset --hard {base_commit}
git apply -v /patch.diff
goimports -w .
git status
go test -json -count=1 ./... > test_output.json 2> test_stderr.txt
echo $? > go_test_exit_code.txt
```

### Differences in eval

| Aspect | Python | Go |
|--------|--------|-----|
| Patch apply | `--allow-empty` (patches may have no changes) | No `--allow-empty` (Go patches should always have content) |
| Code formatting | Not needed (Python doesn't enforce formatting for compilation) | `goimports -w .` required (unformatted Go code fails compilation) |
| Test runner | `pytest --json-report` | `go test -json -count=1 ./...` |
| Test output | `report.json` (pytest JSON report) | `test_output.json` (go test JSON stream) |
| Exit code file | `pytest_exit_code.txt` | `go_test_exit_code.txt` |
| Stderr | Mixed with stdout (`2>&1`) | Separate file (`2> test_stderr.txt`) |
| shlex.quote on test_cmd | Yes | No |

---

## Docker Image Naming

| Image | Python | Go |
|-------|--------|-----|
| Base | `commit0.base.python{version}:latest` (e.g., `commit0.base.python3.12:latest`) | `commit0.base.go:latest` (single image, no version in name) |
| Repo | `commit0.repo.{repo}.{hash}:v0` | Same — inherited from `Spec` base class |
| Container | `commit0.eval.{repo}` | Same — inherited from `Spec` base class |

**Why Python needs multiple base images but Go doesn't:**
- Python has no backward compatibility guarantee across minor versions (3.10 vs 3.12 can break)
- Go has the Go 1 Compatibility Promise — a Go 1.25 toolchain compiles code targeting Go 1.18 correctly, with semantic gating via the `go` directive in `go.mod`

---

## Branch Convention

Both pipelines use the `commit0_all` branch on the forked repository:

```
main (default branch)
│
├── Original working code (reference_commit)
│
└── commit0_all (branch)
    │
    ├── Stubbed code (Commit 0)
    │
    └── [Optional] Spec PDF (spec.pdf.bz2)
         ↑ This becomes base_commit if spec exists
```

`reference_commit` = HEAD of default branch (original working code)
`base_commit` = tip of `commit0_all` (stubbed code, or spec PDF commit if scraped)

The `commit0_all` branch always has `reference_commit` as an ancestor, guaranteeing that a `git clone --branch commit0_all --single-branch` can reach both SHAs.

---

## Summary

The Docker build flow for Python and Go follows the same three-phase pattern — clone, install deps, land on stubbed code — but the ordering of phases 2 and 3 is reversed due to Go's compilation requirements. This is an intentional and correct architectural decision, not a divergence that needs unification.
