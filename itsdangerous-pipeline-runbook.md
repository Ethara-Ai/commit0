# Commit0 Pipeline Runbook

Production guide for preparing custom Python repositories, building Docker environments, and running the 3-stage AI coding pipeline. Covers both the automated batch workflow and the manual step-by-step method.

All commands assume you're in the project root and using `.venv/bin/python`.

---

## Quick Start

Pick the method that fits your situation:

**Got a CSV of repos?** Use the batch method. One command handles forking, stubbing, dataset creation, Docker builds, and test ID generation for every repo in the file.

```bash
# CSV columns: library_name, Github url, Organization Name
.venv/bin/python -m tools.batch_prepare dataset/batch.csv \
    --output batch_dataset.json \
    --removal-mode all

# Run the 3-stage pipeline
bash run_pipeline.sh \
    --model kimi \
    --dataset ./batch_dataset.json \
    --repo-split all \
    --max-iteration 3
```

**Just one repo, or need to debug?** Use the manual method. Each step runs independently so you can inspect intermediate outputs.

```bash
# Prepare
.venv/bin/python tools/prepare_repo.py --repo pallets/itsdangerous \
    --clone-dir ./repos_staging --output entries.json --removal-mode all --org Ethara-Ai

# Create dataset (verify entries.json first!)
.venv/bin/python tools/create_dataset.py entries.json --output dataset.json

# Setup + Build + Test IDs
.venv/bin/commit0 setup all --dataset-name ./dataset.json --dataset-split train
.venv/bin/commit0 build
.venv/bin/python tools/generate_test_ids.py dataset.json --docker --install

# Run pipeline
bash run_pipeline.sh --model kimi --dataset ./dataset.json --repo-split all --max-iteration 3
```

Both methods produce a dataset JSON file. Everything downstream (setup, build, evaluate, pipeline) consumes that file identically.

---

## Pipeline Architecture

```
                        COMMIT0 CUSTOM DATASET PIPELINE
                        ================================

    DISCOVERY (optional)                    PREPARATION
    ====================                    ===========

    +---------------------+
    | tools/discover.py   |  Search GitHub by stars, filter by Python %,
    | (optional)          |  check for pytest, docs, release tags
    +----------+----------+
               |
               | candidates.json
               v
    +----------+----------+
    | tools/validate.py   |  Clone, analyze structure, detect src/test dirs,
    | (optional)          |  optionally run pytest in Docker
    +----------+----------+
               |
               | validated_candidates.json
               v
    +----------+----------+     +---------------------+
    | Manual CSV file     | OR  | batch_prepare.py    |---+
    | (Github url, org,   |     | reads CSV, calls    |   |
    |  library_name)      |     | everything below    |   |
    +----------+----------+     +----------+----------+   |
               |                           |              |
               v                           |              |
    +----------+-----------+               |              |
    | tools/prepare_repo.py|  <--- called -+              |
    |   Fork to org        |                              |
    |   Clone locally      |                              |
    |   AST-stub (stub.py) |                              |
    |   Scrape spec PDF    |                              |
    |   (if --specs-dir)   |                              |
    |   Push branches      |                              |
    |   Generate entries   |                              |
    +----------+-----------+                              |
               |                                          |
               | entries.json                             |
               v                                          |
    +----------+-----------+                              |
    | tools/create_dataset |  <--- called ----------------+
    |   Validate entries   |                              |
    |   Output dataset.json|                              |
    +----------+-----------+                              |
               |                                          |
               | dataset.json                             |
               v                                          |
    +----------+-----------+                              |
    | commit0 setup        |  <--- called ----------------+
    |   Clone fork to repos|                              |
    |   Checkout branch    |                              |
    |   Write .commit0.yaml|                              |
    +----------+-----------+                              |
               |                                          |
               v                                          |
    +----------+-----------+                              |
    | commit0 build        |  <--- called ----------------+
    |   Build base image   |                              |
    |   Build repo images  |                              |
    |   (docker_build.py)  |                              |
    +----------+-----------+                              |
               |                                          |
               v                                          |
    +----------+-----------+                              |
    | generate_test_ids.py |  <--- called ----------------+
    |   pytest --collect   |
    |   Save .bz2 files    |
    |   Install to commit0 |
    +----------+-----------+
               |
               v

    PIPELINE EXECUTION
    ==================

    +-------------------------------+
    | run_pipeline.sh               |  3-stage orchestrator
    |                               |
    |  +-------------------------+  |
    |  | STAGE 1: Draft          |  |  Agent drafts implementations
    |  | run_tests=false         |  |  Test names visible, no results
    |  | use_unit_tests_info=true|  |  Modules in topological order
    |  | use_spec_info=true      |  |  Spec PDF context (if available)
    |  +------------+------------+  |
    |               |               |
    |               v               |
    |       [ commit0 evaluate ]    |  Runs pytest in Docker
    |               |               |
    |               v               |
    |  +-------------------------+  |
    |  | STAGE 2: Lint Refine    |  |  Agent fixes ruff lint/format
    |  | use_lint_info=true      |  |  Iterates per file
    |  | run_tests=false         |  |
    |  +------------+------------+  |
    |               |               |
    |               v               |
    |       [ commit0 evaluate ]    |
    |               |               |
    |               v               |
    |  +-------------------------+  |
    |  | STAGE 3: Test Refine    |  |  Agent iterates on pytest failures
    |  | run_tests=true          |  |  Most impactful stage
    |  | use_lint_info=true      |  |  Docker-based test execution
    |  +------------+------------+  |
    |               |               |
    |               v               |
    |       [ commit0 evaluate ]    |  Final pass rate
    +-------------------------------+
               |
               v
    +-----------+-----------+
    | Results                |  output/<repo>/<model>/results.json
    |   Per-stage pass rates |  Per-module agent logs
    |   Costs, timings       |  Docker image tarballs
    +------------------------+
```

---

## Method A: Automated (batch_prepare.py)

The batch method reads a CSV file and runs the full pipeline for each repo: fork, clone, stub, dataset creation, commit0 setup, Docker build, and test ID generation. One command, start to finish.

### 1. Create the CSV

The CSV needs three columns: `library_name`, `Github url`, `Organization Name`.

```csv
library_name,Github url,Organization Name
itsdangerous,https://github.com/pallets/itsdangerous,Ethara-Ai
click,https://github.com/pallets/click,Ethara-Ai
markupsafe,https://github.com/pallets/markupsafe,Ethara-Ai
```

`library_name` is a human-readable label. The actual repo owner/name gets parsed from the GitHub URL. `Organization Name` is the GitHub org where forks land.

### 2. Run batch_prepare

```bash
.venv/bin/python -m tools.batch_prepare dataset/batch.csv \
    --output batch_dataset.json \
    --removal-mode all \
    --clone-dir ./repos_staging
```

What this does, per repo:
- Calls `prepare_single_repo()`: fork, clone, detect src dir, AST-stub, push branches
- Scrapes specification PDF from the `specification` URL (if set), saves as `spec.pdf.bz2` in the repo
- Creates dataset entries and writes `batch_dataset.json`
- Calls `run_commit0_setup()`: runs `commit0 setup all --dataset-split train`
- Calls `run_commit0_build()`: builds Docker images (reads from .commit0.yaml)
- Calls `generate_and_install_test_ids()`: collects test IDs in Docker, saves .bz2, installs to commit0/data/test_ids/
- Calls `add_gitignore_entries()`: adds .aider* and logs/ to each repo's .gitignore

### 3. Run the pipeline

```bash
bash run_pipeline.sh \
    --model kimi \
    --dataset ./batch_dataset.json \
    --repo-split all \
    --max-iteration 3 2>&1 | tee logs/batch_run.log
```

### When to use batch vs manual

Use batch when:
- Processing multiple repos from a shared CSV
- You trust the automated detection (src_dir, test deps, python version)
- You want hands-off execution

Use manual when:
- Working with a single repo
- The repo has unusual structure (monorepo, non-standard test layout)
- You need to inspect and fix entries JSON before proceeding
- Debugging a specific pipeline step

---

## Method B: Manual (Step-by-Step)

### Step 1: Prepare the repo

```bash
.venv/bin/python tools/prepare_repo.py \
    --repo <OWNER>/<REPO> \
    --clone-dir ./repos_staging \
    --output <repo>_entries.json \
    --removal-mode all \
    --specs-dir ./specs \
    --org Ethara-Ai
```

This forks the repo to your org, clones it locally, runs AST stubbing on the source directory, and pushes a `commit0_all` branch with:
- `base_commit`: the stubbed code (function bodies replaced with `pass`)
- `reference_commit`: the original working code

The `--specs-dir ./specs` flag enables spec PDF scraping. If the `specification` URL is set in the dataset entry, `prepare_repo.py` scrapes the documentation into a PDF using Playwright, compresses it, and commits `spec.pdf.bz2` into the branch. The pipeline later injects this PDF as context for the agent.

Output: `<repo>_entries.json` with commit SHAs, detected paths, and metadata.

### Step 2: Verify and fix entries JSON

**Always inspect the entries before continuing.** Common issues:

| Field | What to check |
|-------|---------------|
| `src_dir` | Must match actual source directory. Watch for `src/` layouts where code lives in `src/<package>/`. An empty `src_dir` means the agent gets zero files. |
| `pip_packages` | Must include ALL test dependencies. Check `pyproject.toml` under `[dependency-groups]`, `[project.optional-dependencies]`, and any `requirements*.txt`. |
| `python` | Should match or exceed `requires-python` from pyproject.toml. Prefer 3.12 when the repo allows it. |
| `test_dir` | Confirm it points to the actual test directory (could be `tests/`, `test/`, or nested). |
| `install` | Verify the install command. If the repo uses extras like `pip install -e ".[tests]"`, make sure that's reflected. |

```bash
# Quick sanity checks
cat <repo>_entries.json | python -m json.tool | grep -E '"src_dir"|"pip_packages"|"python"|"test_dir"'

# Check what test deps exist in the repo
grep -A 20 'tests\|test' repos_staging/<owner>__<repo>/pyproject.toml
```

### Step 3: Create the dataset

```bash
.venv/bin/python tools/create_dataset.py <repo>_entries.json --output <repo>_dataset.json
```

Validates all entries (required fields, types, commit SHA format) and writes the dataset JSON consumed by all downstream tools.

### Step 4: Setup

```bash
.venv/bin/commit0 setup all --dataset-name ./<repo>_dataset.json --dataset-split train
```

This clones the fork from your org into `repos/<repo>/`, checks out the `commit0_all` branch, creates a local `commit0` branch, adds .gitignore entries (.aider*, logs/), and writes `.commit0.yaml`.

### Step 5: Build Docker images

```bash
.venv/bin/commit0 build
```

Builds two images:
- `commit0.base:latest` (if it doesn't exist): Ubuntu 22.04 base with Python
- `commit0.repo.<repo>.<hash>:v0`: clones the repo inside the container, installs dependencies at `reference_commit` (working code), then resets to `base_commit` (stubbed code)

The build uses a 2-step process: first creates an OCI tarball via buildx, then loads it natively with `docker load`.

### Step 6: Generate and install test IDs

```bash
.venv/bin/python tools/generate_test_ids.py <repo>_dataset.json --docker --install
```

Runs `pytest --collect-only` at the `reference_commit` inside the Docker container to discover all test node IDs. Saves them as `commit0/data/test_ids/<repo>.bz2` and installs them into the commit0 data directory.

### Step 7: Validate stubbed code

```bash
.venv/bin/python tools/generate_test_ids.py <repo>_dataset.json --docker --validate-base
```

Or manually:

```bash
docker run --rm \
    "commit0.repo.<repo>.<hash>:v0" \
    bash -c 'cd /testbed && source .venv/bin/activate && pytest --collect-only tests/'
```

The test count from this validation must match the count from Step 6. If it shows 0 tests collected, the stubs broke something the test collection depends on, and the repo won't work with this pipeline.

### Step 8: Run the 3-stage pipeline

```bash
bash run_pipeline.sh \
    --model kimi \
    --dataset ./<repo>_dataset.json \
    --repo-split all \
    --max-iteration 3 2>&1 | tee logs/<repo>_run.log
```

This runs: Stage 1 (Draft) -> Evaluate -> Stage 2 (Lint) -> Evaluate -> Stage 3 (Test) -> Evaluate.

### Step 9: Check results

```bash
cat output/<owner>_<repo>/<model>/results.json | python -m json.tool
```

Results include per-stage pass rates, costs, and timing breakdowns.

---

## Dataset JSON Schema

The dataset file is a JSON array of `RepoInstance` objects. Every downstream tool (setup, build, evaluate, pipeline) reads this format.

```json
[
  {
    "instance_id": "commit-0/itsdangerous",
    "repo": "Ethara-Ai/itsdangerous",
    "original_repo": "pallets/itsdangerous",
    "base_commit": "ce6186db1113cb6b9cf8cbf834560a7fe8f8011d",
    "reference_commit": "672971d66a2ef9f85151e53283113f33d642dabd",
    "setup": {
      "install": "pip install -e \".[tests]\"",
      "packages": "",
      "pip_packages": ["pytest", "pytest-json-report", "freezegun"],
      "pre_install": [],
      "python": "3.12",
      "specification": "https://itsdangerous.palletsprojects.com/"
    },
    "test": {
      "test_cmd": "pytest",
      "test_dir": "tests"
    },
    "src_dir": "src/itsdangerous"
  }
]
```

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `instance_id` | string | Unique ID, format: `commit-0/<repo_name>` |
| `repo` | string | Fork location: `<org>/<repo_name>` |
| `original_repo` | string | Upstream repo: `<owner>/<repo_name>` |
| `base_commit` | string | SHA of the stubbed commit (function bodies replaced with `pass`) |
| `reference_commit` | string | SHA of the original working code |
| `setup.install` | string | Install command run inside Docker (pip, uv, or arbitrary) |
| `setup.packages` | string | System packages to apt-get install (space-separated) |
| `setup.pip_packages` | list[str] | Additional pip packages (test deps, pytest-json-report) |
| `setup.pre_install` | list[str] | Shell commands to run before install (e.g., apt-get calls) |
| `setup.python` | string | Python version for the Docker image (e.g., "3.12") |
| `setup.specification` | string | URL to the library's documentation. Used by `prepare_repo.py` and `ensure_spec_docs()` to scrape spec PDFs for agent context. |
| `test.test_cmd` | string | Test runner command (usually "pytest") |
| `test.test_dir` | string | Relative path to test directory |
| `src_dir` | string | Relative path to source directory (what the agent implements) |

---

## File Reference

### tools/discover.py (558 lines)

Discovers candidate Python repos from GitHub. Searches by star count, filters by Python percentage, checks for pytest and documentation.

| Function | What it does |
|----------|-------------|
| `search_python_repos(min_stars, max_results, token)` | GitHub API search with star-range pagination to bypass the 1000-result API limit |
| `enrich_candidates(repos, token, check_pytest, min_python_pct)` | Adds language breakdown, pytest detection, docs check, and release tags to raw search results |
| `get_language_breakdown(full_name, token)` | Fetches GitHub languages API for a repo |
| `compute_python_percentage(languages)` | Calculates Python's share of total bytes |
| `check_has_pytest(full_name, default_branch, token)` | Checks pyproject.toml and setup.cfg for pytest references |
| `get_latest_release_tag(full_name, token)` | Finds the latest git tag for pinning a specific version |

### tools/validate.py (877 lines)

Validates candidate repos by cloning them, analyzing their structure, and optionally running pytest in Docker.

| Function | What it does |
|----------|-------------|
| `validate_candidates(candidates, clone_dir, run_tests, max_repos)` | Main orchestrator: iterates candidates, calls analyze/test for each |
| `analyze_repo(repo_dir, full_name)` | Full structural analysis: src_dir, test_dir, file counts, python version, install method, test deps, docs |
| `find_src_dir(repo_dir, repo_name)` | Detects source directory using src/ layout, flat layout, and heuristic fallbacks |
| `find_test_dir(repo_dir)` | Detects test directory (tests/, test/, or nested variants) |
| `detect_python_version(repo_dir)` | Parses `requires-python` from pyproject.toml or setup.cfg |
| `detect_install_method(repo_dir)` | Determines install command: `pip install -e .`, extras, requirements files |
| `detect_test_deps(repo_dir)` | Extracts test dependencies from pyproject.toml, setup.cfg, requirements files |
| `run_tests_in_docker(repo_dir, full_name, python_version, timeout)` | Docker-based test execution with coverage reporting |
| `_get_docker_platform()` | Auto-detects arm64 vs amd64 for Docker platform flag |

### tools/prepare_repo.py (1011 lines)

Prepares repos for the commit0 dataset: forks to your org, clones, AST-stubs the source code, pushes branches, and generates dataset entries.

| Function | What it does |
|----------|-------------|
| `prepare_repos(candidates, clone_dir, org, ...)` | Main orchestrator for batch processing multiple repos |
| `fork_repo(full_name, org, token)` | Forks via `gh` CLI to the target organization |
| `full_clone(full_name, clone_dir, branch, tag)` | Deep clone with optional tag checkout |
| `detect_src_dir(repo_dir, full_name)` | 5-heuristic source directory detection with case-exact matching |
| `create_stubbed_branch(repo_dir, full_name, src_dir, ...)` | AST-stubs code, commits result as base_commit |
| `extract_test_dependencies(repo_dir)` | Parses pyproject.toml optional-deps, dependency-groups, and requirements*.txt |
| `generate_setup_dict(repo_dir, full_name)` | Generates install/packages/pip_packages/pre_install/python config |
| `generate_test_dict(repo_dir, test_dir)` | Generates test_cmd and test_dir |
| `create_dataset_entry(...)` | Creates a RepoInstance-compatible entry dict |
| `push_to_fork(repo_dir, fork_name, branch, ...)` | Pushes to GitHub fork |
| `resolve_commits_from_remote(fork_name, branch)` | Falls back to `gh` API to resolve commit SHAs when push fails |

### tools/stub.py (902 lines)

AST-based Python code stubber. Replaces function bodies with `pass` while preserving imports, class structure, decorators, and type annotations.

| Function / Class | What it does |
|-----------------|-------------|
| `collect_import_time_names(source_dir)` | Scans for functions called at import time: module-level calls, decorators, class body expressions, metaclass args, `__init_subclass__` bodies |
| `StubTransformer` (class) | Line-based AST transformer with 3 modes: `all` (stub everything), `docstring` (remove docstrings only), `combined` (both) |
| `stub_file(source_path, output_path, ...)` | Stubs a single file, validates the output parses with `ast.parse` |
| `stub_directory(source_dir, output_dir, ...)` | Stubs an entire directory tree, skips test files and `__init__.py` |

### tools/create_dataset.py (309 lines)

Creates a HuggingFace-compatible dataset JSON from prepared entries.

| Function | What it does |
|----------|-------------|
| `validate_entry(entry, index)` | Schema validation: checks required fields and types |
| `validate_dataset(entries)` | Validates all entries in the dataset |
| `create_hf_dataset_dict(entries)` | Converts entries to HuggingFace dataset format |
| `upload_to_huggingface(entries, repo_id, token)` | Optional upload to HuggingFace Hub |
| `generate_split_constants(entries, split_name)` | Generates SPLIT dict for constants.py |

### tools/generate_test_ids.py (620 lines)

Generates pytest test ID `.bz2` files using the Docker SDK.

| Function | What it does |
|----------|-------------|
| `_get_docker_platform()` | Auto-detects arm64 vs amd64 for Docker |
| `_parse_collect_output(stdout)` | Parses both verbose and quiet `pytest --collect-only` output formats |
| `collect_test_ids_local(repo_dir, test_dir, ...)` | Local pytest collection without Docker |
| `collect_test_ids_docker(repo_name, test_dir, image_name, reference_commit, ...)` | Docker SDK-based collection with platform auto-detection |
| `_find_docker_image(repo_name)` | Finds a Docker image by partial tag match |
| `validate_base_commit_docker(repo_name, test_dir, image_name, ...)` | Validates that stubbed code still collects the same tests |
| `save_test_ids(test_ids, name, output_dir)` | Saves test IDs as compressed `.bz2` |
| `install_test_ids(source_dir, repo_names)` | Copies `.bz2` files to `commit0/data/test_ids/` |
| `generate_for_dataset(dataset_path, output_dir, ...)` | Main orchestrator: iterates dataset entries and collects test IDs |

### tools/batch_prepare.py (574 lines)

Batch orchestrator. Reads a CSV, runs the full pipeline for each repo: fork, stub, dataset creation, setup, build, test IDs.

| Function | What it does |
|----------|-------------|
| `parse_csv(csv_path)` | Parses assignments CSV with columns: library_name, Github url, Organization Name |
| `prepare_single_repo(full_name, clone_dir, org, removal_mode, ...)` | Full preparation for one repo (fork, clone, stub, push, entry) |
| `run_commit0_setup(dataset_path)` | Calls `commit0 setup all --dataset-split train` |
| `run_commit0_build(dataset_path)` | Calls `commit0 build` (reads dataset from .commit0.yaml) |
| `generate_and_install_test_ids(dataset_path, output_dir, validate_base)` | Test ID generation + installation |
| `add_gitignore_entries(repos_dir, repo_name)` | Adds `.aider*` and `logs/` to the repo's .gitignore |

### commit0/harness/setup.py (89 lines)

Clones fork repos into `repos/` directory, checks out the branch, creates a `commit0` branch, and auto-adds .gitignore entries.

| Function | What it does |
|----------|-------------|
| `main(dataset_name, dataset_split, repo_split, base_dir)` | Clones all repos in the dataset split, sets up branches and config |

### commit0/harness/build.py (59 lines)

Entry point for Docker image building. Delegates actual work to docker_build.py.

| Function | What it does |
|----------|-------------|
| `main(dataset_name, dataset_split, split, num_workers, verbose)` | Reads .commit0.yaml, calls build functions |

### commit0/harness/docker_build.py (451 lines)

Docker image building with buildx OCI tarball export and native load.

| Function | What it does |
|----------|-------------|
| `_safe_builder_args()` | Detects Docker builder driver, avoids docker-container builders that break OCI export |
| `build_image(image_name, setup_scripts, dockerfile, platform, ...)` | 2-step build: OCI tarball (Step 1) then native `docker load` (Step 2) |
| `build_base_images(client, dataset, ...)` | Builds `commit0.base:latest` if it doesn't exist |
| `build_repo_images(client, dataset, ...)` | Builds per-repo images with ThreadPoolExecutor parallelism |

### commit0/harness/spec.py (384 lines)

Generates Docker setup and eval scripts for different benchmark types.

| Class | What it does |
|-------|-------------|
| `Spec` (ABC) | Base class with `repo_image_key` property and platform auto-detection |
| `Commit0Spec` | Setup: clone repo, create venv, install deps, reset to base_commit. Handles pip, uv, python, and arbitrary install commands. |
| `SWEBenchSpec` | SWE-bench specific setup and eval scripts |
| `SimpleSpec` | Simple benchmarks (HumanEval, MBPP) |

### commit0/harness/evaluate.py (175 lines)

Runs evaluation: applies a git diff as a patch, runs pytest in Docker, collects pass rates.

| Function | What it does |
|----------|-------------|
| `main(dataset_name, dataset_split, repo_split, base_dir, branch, ...)` | Iterates repos, applies patches, runs tests, reports per-repo pass rates |

### commit0/cli.py (431 lines)

Typer CLI providing all `commit0` subcommands: `setup`, `build`, `test`, `evaluate`, `lint`, `save`.

### run_pipeline.sh (~1330 lines)

3-stage pipeline orchestrator. Configures the agent, runs each stage, evaluates between stages.

| Function | What it does |
|----------|-------------|
| `resolve_model()` | Maps presets (kimi, opus, glm5, minimax, gpt54) to full model IDs |
| `resolve_dataset()` | Resolves dataset name or path |
| `preflight()` | Checks dependencies: jq, bc, python, venv |
| `preflight_model_api()` | Probes model API with litellm (timeout configurable via PROBE_TIMEOUT env var) |
| `get_mtime()` | Cross-platform file modification time (GNU stat first, BSD fallback) |
| `watchdog_run()` | Monitors agent for inactivity, kills if no log changes for INACTIVITY_TIMEOUT seconds |
| `run_agent()` | Launches agent with stage-specific YAML config |
| `run_evaluate()` | Runs `commit0 evaluate`, parses results JSON |
| `ensure_spec_docs()` | Pre-pipeline: ensures each repo has `spec.pdf.bz2`. Checks repo dir, then local cache (specs/), then scrapes from specification URL |
| Stage 1 (draft) | Agent drafts implementations. `run_tests=false`, `use_unit_tests_info=true`. No test feedback. |
| Stage 2 (lint) | Agent fixes ruff lint/format issues. `use_lint_info=true`, `run_tests=false`. |
| Stage 3 (test) | Agent iterates on pytest failures in Docker. `run_tests=true`, `use_lint_info=true`. Most impactful stage. |

---

## Configuration

### .commit0.yaml

Written by `commit0 setup`. All downstream commands read this file to find the dataset and repos.

```yaml
# commit0 config for custom dataset
dataset_name: ./batch_dataset.json
dataset_split: test
repo_split: all
base_dir: repos

# Repos in this split (3):
#   - Ethara-Ai/itsdangerous
#   - Ethara-Ai/click
#   - Ethara-Ai/markupsafe
```

| Field | Description |
|-------|-------------|
| `dataset_name` | Path to the dataset JSON file (absolute or relative) |
| `dataset_split` | HuggingFace dataset split to use (`test` for local files) |
| `repo_split` | Which repo split to operate on (e.g., `all`, or a custom split name) |
| `base_dir` | Directory where repos are cloned (default: `repos`) |

### .env file

Store model API keys here. `run_pipeline.sh` sources this automatically if present.

```bash
# For Bedrock models
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1

# For Bedrock with bearer token auth
AWS_BEARER_TOKEN_BEDROCK=...

# For OpenAI models
OPENAI_API_KEY=sk-...

# For other providers (via litellm)
ANTHROPIC_API_KEY=...
```

### Agent config YAML

Generated by `run_pipeline.sh` for each stage. You don't normally edit these directly, but here's the structure:

```yaml
agent_name: aider
model_name: bedrock/converse/arn:aws:bedrock:us-east-1:...
use_user_prompt: false
run_tests: false            # true in stage 3
max_iteration: 3
use_repo_info: false
use_unit_tests_info: true   # true in stages 1 and 3
use_spec_info: true         # true by default; disable with --no-spec-info
use_lint_info: false         # true in stages 2 and 3
pre_commit_config_path: .pre-commit-config.yaml
agent_config_file: .agent.yaml
backend: local
cache_prompts: false
```

### Model presets

| Preset | Model ID | Short name | Cache prompts |
|--------|----------|------------|---------------|
| `kimi` | `bedrock/converse/arn:aws:bedrock:us-east-1:...:5m69567zugvx` | kimi-k2.5 | false |
| `opus` | `bedrock/global.anthropic.claude-opus-4-6-v1` | opus4.6 | true |
| `glm5` | `bedrock/converse/arn:aws:bedrock:us-east-1:...:8lzlkxguk85a` | glm-5 | false |
| `minimax` | `bedrock/converse/arn:aws:bedrock:us-east-1:...:6oaav7wbxid4` | minimax-m2.5 | false |
| `gpt54` | `openai/gpt-5.4` | gpt-5.4 | false |
| (custom) | Pass full model string directly | auto-derived | auto-detected for Anthropic |

Use a preset: `--model kimi`. Or pass a full model string: `--model bedrock/converse/arn:aws:...`.

### run_pipeline.sh CLI options

| Flag | Description | Default |
|------|-------------|---------|
| `--model` | Model preset or full model string | (required) |
| `--dataset` | Path to dataset JSON | (required) |
| `--repo-split` | Repo split name | `all` |
| `--max-iteration` | Agent iterations per file | `3` |
| `--no-spec-info` | Disable spec PDF injection into agent context | spec enabled |
| `--skip-to-stage` | Resume from a specific stage (2 or 3) | start from 1 |

---

## Troubleshooting

### Docker buildx issues (builder driver detection)

**Symptom**: Build fails with errors about `--output type=oci` or "builder doesn't support export".

**Cause**: `docker_build.py` uses `_safe_builder_args()` to detect the Docker builder driver. If you have a `docker-container` builder set as default, OCI export fails differently than with the `docker` driver.

**Fix**: Switch to the default builder:
```bash
docker buildx use default
```
Or remove custom builders:
```bash
docker buildx ls
docker buildx rm <builder-name>
```

### Platform mismatches (arm64 vs amd64)

**Symptom**: Docker images build but containers fail to start, or `exec format error` appears.

**Cause**: Images built on arm64 (Apple Silicon, Graviton) won't run on amd64 hosts, and vice versa. All Docker operations in the pipeline auto-detect platform via `_get_docker_platform()`, but manual `docker run` commands need an explicit `--platform` flag.

**Fix**: Always pass `--platform` when running containers manually:
```bash
# On arm64 hosts (Apple Silicon, AWS Graviton)
docker run --platform linux/arm64 --rm <image> ...

# On amd64 hosts (Intel/AMD)
docker run --platform linux/amd64 --rm <image> ...
```

### Push failures (GitHub token, credential mismatch)

**Symptom**: `prepare_repo.py` fails at the push step with authentication errors.

**Cause**: The `gh` CLI token and git credential helper may disagree, or the token lacks `repo` scope.

**Fix**:
```bash
# Check gh auth status
gh auth status

# Re-authenticate if needed
gh auth login

# Ensure git uses gh for credentials
gh auth setup-git
```

If push still fails, `prepare_repo.py` falls back to `resolve_commits_from_remote()` to fetch commit SHAs via the GitHub API. You can then push manually.

### 0 test IDs collected

**Symptom**: `generate_test_ids.py` reports 0 test IDs or the .bz2 file is empty.

**Causes and fixes**:

1. **Broken stubs**: The stubbing removed something test collection depends on (a fixture, a conftest helper, a module-level constant). Check by running pytest --collect-only inside the container and reading the error output.

2. **Missing test deps**: A test dependency isn't in `pip_packages`, so `import` fails silently during collection. Compare `pip_packages` in your dataset JSON against the repo's pyproject.toml test dependencies.

3. **Wrong test_dir**: The `test_dir` in the dataset doesn't match where tests actually live. Verify by inspecting the repo structure.

4. **Docker image stale**: If you changed the dataset after building, the image still has old commits. Rebuild: `.venv/bin/commit0 build --rebuild`.

### Stage 2 regression (lint damaging code)

**Symptom**: Pass rate drops after Stage 2 (lint) compared to Stage 1.

**Cause**: Ruff auto-fix can break working code. Common scenarios: removing "unused" imports that are actually re-exports, reformatting string literals that change behavior, or fixing line lengths by splitting expressions incorrectly.

**Mitigation**: Check Stage 2 diffs. If lint is consistently harmful for a repo, you can skip Stage 2 by editing `run_pipeline.sh` or running stages individually. Stage 3 (test feedback) usually recovers the damage, but starting from a higher Stage 1 baseline is always better.

### Model API probe failures (timeout, auth)

**Symptom**: `run_pipeline.sh` exits with "Model API probe failed" before any stage runs.

**Causes**:

1. **Timeout**: The probe sends a small request to verify connectivity. Increase the timeout:
   ```bash
   PROBE_TIMEOUT=30 bash run_pipeline.sh --model kimi ...
   ```

2. **Missing credentials**: For Bedrock models, ensure AWS credentials are set (env vars or ~/.aws/credentials). For OpenAI, ensure OPENAI_API_KEY is set.

3. **Missing boto3**: Bedrock models need boto3 in the venv:
   ```bash
   .venv/bin/pip install boto3
   ```

4. **Wrong region**: Bedrock model ARNs are region-specific. Ensure AWS_DEFAULT_REGION matches the ARN's region.

### Spec PDF scraping failures

**Symptom**: `prepare_repo.py` or `ensure_spec_docs()` fails to create `spec.pdf.bz2`, or the file is 0 bytes.

**Causes**:
1. **Playwright not installed**: Spec scraping uses Playwright for headless browsing. Install with: `.venv/bin/pip install playwright && .venv/bin/playwright install chromium`
2. **specification URL is wrong or dead**: Check the `specification` field in your dataset JSON. The URL must point to actual documentation.
3. **Site blocks scraping**: Some documentation sites block headless browsers. Try accessing the URL manually first.

**Workaround**: The pipeline works without spec PDFs. Use `--no-spec-info` to skip spec injection, or manually create a spec PDF and place it as `spec.pdf.bz2` in the repo directory.

---

## Common Gotchas Checklist

Before running the pipeline, verify each item:

- [ ] `src_dir` matches actual source directory (watch for `src/` layout where code is in `src/<package>/`)
- [ ] `pip_packages` includes ALL test dependencies (check pyproject.toml `[dependency-groups]`, `[project.optional-dependencies]`, requirements files)
- [ ] Test IDs file is non-empty: `bzcat commit0/data/test_ids/<repo>.bz2 | wc -l`
- [ ] Stubbed code validation passes: `pytest --collect-only` shows tests > 0
- [ ] `boto3` is installed in venv when using Bedrock models
- [ ] Docker image tar is non-zero bytes (if you need to export/share it)
- [ ] `gh auth status` shows you're authenticated with repo scope
- [ ] `.commit0.yaml` exists and points to the correct dataset JSON
- [ ] No stale Docker images from previous runs (rebuild if dataset changed)
- [ ] `spec.pdf.bz2` exists in repo dir if using spec info (check: `ls repos/<repo>/spec.pdf.bz2`)
- [ ] Enough disk space for Docker images (~1GB per repo image)

---

## Reference Example: pallets/itsdangerous

This section documents a complete run of the manual method against `pallets/itsdangerous`, including corrections that were needed. Use it as a reference for what to expect.

### Repo profile

| Property | Value |
|----------|-------|
| Repo | `pallets/itsdangerous` |
| Description | Safely pass trusted data to untrusted environments and back |
| Language | >99% Python |
| Layout | `src/` layout (`src/itsdangerous/`) |
| Source files | 9 files (7 with stubs + `__init__.py` + `py.typed`) |
| Test dir | `tests/test_itsdangerous/` (6 test files) |
| Test count | 297 |
| Runtime deps | Zero |
| Test deps | `freezegun`, `pytest` |
| Build backend | `flit_core` |
| Python | `>=3.10` |
| Fork | `Ethara-Ai/itsdangerous` |

### Commands run

```bash
# 1. Prepare
.venv/bin/python tools/prepare_repo.py \
    --repo pallets/itsdangerous \
    --clone-dir ./repos_staging \
    --output itsdangerous_entries.json \
    --removal-mode all \
    --specs-dir ./specs \
    --org Ethara-Ai
```

Detected src dir: `src/itsdangerous/`. Stubbed 6 files (42 lines added, 310 lines removed). Preserved 16 import-time functions. Created branch `commit0_all`, pushed to fork. reference_commit: `672971d66a2e`, base_commit: `ce6186db1113`. Scraped spec PDF from `https://itsdangerous.palletsprojects.com/` (spec.pdf.bz2 committed in branch).

```bash
# 2. Manual fixes applied to itsdangerous_entries.json (see corrections below)

# 3. Create dataset
.venv/bin/python tools/create_dataset.py itsdangerous_entries.json \
    --output itsdangerous_dataset.json

# 4. Setup
.venv/bin/commit0 setup all --dataset-name ./itsdangerous_dataset.json --dataset-split train

# 5. Build
.venv/bin/commit0 build

# 6. Test IDs (collected 297)
.venv/bin/python tools/generate_test_ids.py itsdangerous_dataset.json --docker --install

# 7. Validate
docker run --platform linux/arm64 --rm \
    "commit0.repo.itsdangerous.e49f646a3985ffa496baed:v0" \
    bash -c 'cd /testbed && source .venv/bin/activate && pytest --collect-only tests/'
# Result: 297 tests collected

# 8. Run pipeline
bash run_pipeline.sh \
    --model kimi \
    --dataset ./itsdangerous_dataset.json \
    --repo-split all \
    --max-iteration 3 2>&1 | tee logs/kimi_itsdangerous.log
```

### Corrections applied

Three fixes were needed in the entries JSON before creating the dataset:

**1. Empty src_dir**: `prepare_repo.py` left `src_dir` as `""`. Fixed to `"src/itsdangerous"`. Without this, the agent receives zero source files.

**2. Missing freezegun**: `pip_packages` was `["pytest", "pytest-json-report"]`. Fixed to include `"freezegun"` (listed in pyproject.toml under `[dependency-groups] tests`). Without it, tests importing freezegun get silently skipped during collection.

**3. Python version**: Changed from `"3.10"` to `"3.12"`. Both work (repo requires `>=3.10`), but 3.12 is preferred for performance and Docker base image compatibility.

### Final dataset JSON

```json
[
  {
    "instance_id": "commit-0/itsdangerous",
    "repo": "Ethara-Ai/itsdangerous",
    "original_repo": "pallets/itsdangerous",
    "base_commit": "ce6186db1113cb6b9cf8cbf834560a7fe8f8011d",
    "reference_commit": "672971d66a2ef9f85151e53283113f33d642dabd",
    "setup": {
      "install": "pip install -e \".[tests]\"",
      "packages": "",
      "pip_packages": ["pytest", "pytest-json-report", "freezegun"],
      "pre_install": [],
      "python": "3.12",
      "specification": "https://itsdangerous.palletsprojects.com/"
    },
    "test": {
      "test_cmd": "pytest",
      "test_dir": "tests"
    },
    "src_dir": "src/itsdangerous"
  }
]
```

### Results

| Stage | Pass Rate | Passed/Total | Stage Cost | Cumul. Cost | Time |
|-------|-----------|-------------|------------|-------------|------|
| Stage 1 (Draft) | 12.7% | 38/299 | $0.75 | $0.75 | 30 min |
| Stage 2 (Lint) | 3.0% | 9/299 | $0.66 | $1.41 | 13 min |
| Stage 3 (Test) | 80.6% | 241/299 | $0.78 | $2.19 | 18 min |

**Total**: 80.6% pass rate (241/299), $2.19, ~62 minutes.

Stage 1 produced a low initial pass rate because crypto/signing functions (HMAC, base64, timestamp validation) need precise implementation details the model can't guess without test feedback. Stage 2 regressed from 12.7% to 3.0% (lint fixes damaged working code). Stage 3 recovered massively via test feedback iteration.

### Output file locations

| What | Path |
|------|------|
| Entries JSON | `itsdangerous_entries.json` |
| Dataset JSON | `itsdangerous_dataset.json` |
| Results JSON | `output/pallets_itsdangerous/kimi-k2.5/results.json` |
| Pipeline log | `logs/kimi_itsdangerous.log` |
| Stage logs | `output/pallets_itsdangerous/kimi-k2.5/stage{1,2,3}_*/` |
| Test IDs | `commit0/data/test_ids/itsdangerous.bz2` |
| Staged repo | `repos_staging/pallets__itsdangerous/` |
| Working repo | `repos/itsdangerous/` |
| Commit0 config | `.commit0.yaml` |
