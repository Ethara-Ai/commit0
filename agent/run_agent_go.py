"""Go agent runner for commit0.

Mirrors run_agent.py but uses Go-specific splits, test IDs, utilities,
and agent configuration. Orchestrates parallel agent execution across
Go repositories.
"""

import logging
import multiprocessing
import os
from pathlib import Path
from typing import Optional

import git

from agent.agents_go import AiderGoAgents
from agent.agent_utils_go import (
    collect_go_test_files,
    create_branch,
    get_go_lint_cmd,
    get_go_message,
    get_target_edit_files,
    load_agent_config,
)
from agent.display import TerminalDisplay
from commit0.harness.constants import RepoInstance
from commit0.harness.constants_go import (
    GO_SPLIT,
    GO_SPLIT_ALL,
    RUN_GO_TEST_LOG_DIR,
)
from commit0.harness.get_go_test_ids import main as get_go_test_ids
from commit0.harness.utils import load_dataset_from_config

logger = logging.getLogger(__name__)

RUN_AGENT_LOG_DIR = Path("logs/agent_go")


def _read_commit0_go_config(config_file: str) -> dict:
    """Read .commit0.go.yaml config file."""
    import yaml

    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class DirContext:
    """Context manager for changing directory."""

    def __init__(self, path: str):
        self.path = path
        self.original: Optional[str] = None

    def __enter__(self):
        self.original = os.getcwd()
        os.chdir(self.path)
        return self

    def __exit__(self, *args):
        if self.original:
            os.chdir(self.original)


def run_eval_after_each_commit(
    dataset_name: str,
    dataset_split: str,
    repo_name: str,
    branch: str,
    backend: str,
    base_dir: str,
    timeout: int = 1800,
) -> None:
    """Run Go evaluation after each commit."""
    import subprocess

    cmd = [
        "python",
        "commit0/cli_go.py",
        "evaluate",
        "--dataset-name",
        dataset_name,
        "--dataset-split",
        dataset_split,
        "--repo-split",
        repo_name,
        "--branch",
        branch,
        "--backend",
        backend,
        "--base-dir",
        base_dir,
    ]
    try:
        subprocess.run(cmd, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        logger.warning(f"Eval timed out for {repo_name}")


def run_agent_for_repo(
    repo_name: str,
    dataset_name: str,
    dataset_split: str,
    base_dir: str,
    branch: str,
    override_previous_changes: bool,
    backend: str,
    agent_config_file: str,
    log_dir: str,
    update_queue: Optional[multiprocessing.Queue] = None,
) -> None:
    """Run the Go agent for a single repository."""
    agent_config = load_agent_config(agent_config_file)
    repo_dir = os.path.join(base_dir, repo_name)

    if not os.path.isdir(repo_dir):
        logger.error(f"Repo directory not found: {repo_dir}")
        if update_queue:
            update_queue.put(("error", repo_name, "Repo not found"))
        return

    repo = git.Repo(repo_dir)
    create_branch(repo, branch, override=override_previous_changes)

    test_ids = get_go_test_ids(repo_name, verbose=False)

    dataset = load_dataset_from_config(dataset_name, dataset_split)
    reference_commit = None
    src_dir = "."
    for example in dataset:
        name = example.get("repo", "")
        if name.endswith(f"/{repo_name}") or name == repo_name:
            reference_commit = example.get("reference_commit", "")
            src_dir = example.get("src_dir", ".")
            break

    target_files = get_target_edit_files(
        repo_dir, src_dir, branch, reference_commit or "HEAD"
    )
    test_files = collect_go_test_files(repo_dir)

    if not target_files:
        logger.info(f"No stubbed files found for {repo_name}")
        if update_queue:
            update_queue.put(("done", repo_name, "No stubs"))
        return

    agent = AiderGoAgents(
        max_iteration=agent_config.max_iteration,
        model_name=agent_config.model_name,
        cache_prompts=agent_config.cache_prompts,
    )

    test_cmd = f"go test -json -count=1 ./..."
    lint_cmd = (
        get_go_lint_cmd(dataset_name, dataset_split, repo_name, base_dir)
        if agent_config.use_lint_info
        else ""
    )

    message = get_go_message(
        agent_config,
        repo_dir,
        test_files,
        dataset_name,
        dataset_split,
        base_dir,
    )

    repo_log_dir = os.path.join(log_dir, repo_name)
    os.makedirs(repo_log_dir, exist_ok=True)

    if update_queue:
        update_queue.put(("start", repo_name, f"Editing {len(target_files)} files"))

    with DirContext(repo_dir):
        rel_files = [os.path.relpath(f, repo_dir) for f in target_files]

        if agent_config.run_tests:
            result = agent.run(
                message=message,
                test_cmd=test_cmd,
                lint_cmd=lint_cmd,
                fnames=rel_files,
                log_dir=repo_log_dir,
                test_first=True,
            )
        elif agent_config.use_lint_info:
            result = agent.run(
                message=message,
                test_cmd=test_cmd,
                lint_cmd=lint_cmd,
                fnames=rel_files,
                log_dir=repo_log_dir,
                lint_first=True,
            )
        else:
            result = agent.run(
                message=message,
                test_cmd=test_cmd,
                lint_cmd=lint_cmd,
                fnames=rel_files,
                log_dir=repo_log_dir,
            )

    if agent_config.record_test_for_each_commit:
        run_eval_after_each_commit(
            dataset_name,
            dataset_split,
            repo_name,
            branch,
            backend,
            base_dir,
        )

    if update_queue:
        update_queue.put(("done", repo_name, f"Cost: ${result.last_cost:.4f}"))


def run_agent(
    branch: str,
    override_previous_changes: bool = False,
    backend: str = "docker",
    agent_config_file: str = ".agent.go.yaml",
    commit0_config_file: str = ".commit0.go.yaml",
    log_dir: str = str(RUN_AGENT_LOG_DIR.resolve()),
    max_parallel_repos: int = 1,
    display_repo_progress_num: int = 5,
) -> None:
    """Run Go agents across all repos in GO_SPLIT."""
    config = _read_commit0_go_config(commit0_config_file)
    dataset_name = config.get("dataset_name", "wentingzhao/commit0_go")
    dataset_split = config.get("dataset_split", "test")
    base_dir = config.get("base_dir", "repos")
    repo_split = config.get("repo_split", "all")

    if repo_split == "all":
        repos = GO_SPLIT_ALL
    elif repo_split in GO_SPLIT:
        repos = GO_SPLIT[repo_split]
    else:
        repos = [repo_split]

    update_queue: multiprocessing.Queue = multiprocessing.Queue()

    display = TerminalDisplay(
        total_repos=len(repos),
        display_repo_progress_num=display_repo_progress_num,
    )

    def worker(repo_name: str) -> None:
        run_agent_for_repo(
            repo_name=repo_name,
            dataset_name=dataset_name,
            dataset_split=dataset_split,
            base_dir=base_dir,
            branch=branch,
            override_previous_changes=override_previous_changes,
            backend=backend,
            agent_config_file=agent_config_file,
            log_dir=log_dir,
            update_queue=update_queue,
        )

    with display:
        with multiprocessing.Pool(max_parallel_repos) as pool:
            pool.map(worker, repos)
