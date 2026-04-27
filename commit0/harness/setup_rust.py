import logging
import os
from typing import Iterator

from commit0.harness.utils import clone_repo, load_dataset_from_config
from commit0.harness.constants_rust import (
    RUST_BASE_BRANCH,
    RUST_SPLIT,
    RUST_GITIGNORE_ENTRIES,
    RustRepoInstance,
)


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main(
    dataset_name: str,
    dataset_split: str,
    repo_split: str,
    base_dir: str,
) -> None:
    dataset: Iterator[RustRepoInstance] = load_dataset_from_config(
        dataset_name, split=dataset_split
    )  # type: ignore
    dataset_name = dataset_name.lower()

    for example in dataset:
        repo_name = example["repo"].split("/")[-1]
        clone_url = f"https://github.com/{example['repo']}.git"

        if repo_split != "all":
            if repo_split in RUST_SPLIT:
                if repo_name not in RUST_SPLIT[repo_split]:
                    continue
            else:
                if repo_name.replace("-", "_") != repo_split.replace("-", "_"):
                    continue

        clone_dir = os.path.abspath(os.path.join(base_dir, repo_name))

        if dataset_name.endswith(".json") or os.sep in dataset_name:
            branch = "commit0_all"
        else:
            branch = dataset_name.split("/")[-1]

        repo = clone_repo(clone_url, clone_dir, branch, logger)

        if RUST_BASE_BRANCH in repo.branches:
            repo.git.branch("-D", RUST_BASE_BRANCH)
        repo.git.checkout("-b", RUST_BASE_BRANCH)
        logger.info(f"Checked out the base branch: {RUST_BASE_BRANCH}")

        try:
            gitignore_path = os.path.join(clone_dir, ".gitignore")

            existing_lines: list[str] = []
            if os.path.exists(gitignore_path):
                with open(gitignore_path, "r") as f:
                    existing_lines = f.read().splitlines()

            added_lines: list[str] = []
            for entry in RUST_GITIGNORE_ENTRIES:
                if entry not in existing_lines:
                    added_lines.append(entry)

            if added_lines:
                with open(gitignore_path, "a") as f:
                    for line in added_lines:
                        f.write(f"\n{line}")
                    f.write("\n")
                repo.git.add(".gitignore")
                repo.git.commit(
                    "-m", "chore: add target/aider/logs to gitignore"
                )
                logger.info(f"Added {added_lines} to .gitignore")
            else:
                logger.info(".gitignore already has all Rust exclusions")

        except Exception as e:
            logger.warning(f"Failed to update .gitignore: {e}")


__all__: list[str] = []
