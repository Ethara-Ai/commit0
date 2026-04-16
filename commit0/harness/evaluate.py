import json
import logging
import os
from collections import Counter

import docker
import docker.errors
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from typing import Iterator, Union

from commit0.harness.run_pytest_ids import main as run_tests
from commit0.harness.get_pytest_ids import main as get_tests
from commit0.harness.constants import RepoInstance, SPLIT, RUN_PYTEST_LOG_DIR
from commit0.harness.spec import get_specs_from_dataset
from commit0.harness.utils import (
    get_hash_string,
    get_active_branch,
    load_dataset_from_config,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def _preflight_check_images(
    dataset_name: str,
    dataset_split: str,
    backend: str,
) -> list[str]:
    """Validate that all required Docker images exist BEFORE launching evaluation.

    Returns a list of missing image names. An empty list means all images are present.
    This catches missing/deleted images early with a clear error message instead of
    N parallel threads each failing independently with confusing docker pull errors.
    """
    if backend.upper() != "LOCAL":
        return []  # only Docker backend needs local images

    try:
        client = docker.from_env()
    except docker.errors.DockerException as e:
        logger.error(f"Pre-flight: cannot connect to Docker daemon: {e}")
        return ["<docker-daemon-unreachable>"]

    dataset = load_dataset_from_config(dataset_name, split=dataset_split)
    dataset_type = "swebench" if "swe" in dataset_name.lower() else "commit0"
    specs = get_specs_from_dataset(list(dataset), dataset_type, absolute=True)

    missing = []
    checked_images: set[str] = set()
    for spec in specs:
        for image_key in (spec.base_image_key, spec.repo_image_key):
            if image_key in checked_images:
                continue
            checked_images.add(image_key)
            try:
                client.images.get(image_key)
            except docker.errors.ImageNotFound:
                missing.append(image_key)
            except docker.errors.APIError as e:
                logger.warning(f"Pre-flight: API error checking {image_key}: {e}")

    return missing


def main(
    dataset_name: str,
    dataset_split: str,
    repo_split: str,
    base_dir: str,
    branch: Union[str, None],
    coverage: bool,
    backend: str,
    timeout: int,
    num_cpus: int,
    num_workers: int,
    rebuild_image: bool,
) -> None:
    dataset: Iterator[RepoInstance] = load_dataset_from_config(
        dataset_name, split=dataset_split
    )  # type: ignore
    if "swe" in dataset_name.lower():
        if repo_split == "all":
            repos = dataset["instance_id"]  # type: ignore
        else:
            repos = [one for one in dataset["instance_id"] if repo_split in one]  # type: ignore
    else:
        repos = (
            SPLIT[repo_split]
            if repo_split in SPLIT
            else [
                ex["repo"].split("/")[-1]
                for ex in load_dataset_from_config(dataset_name, split=dataset_split)
            ]
        )
    triples = []
    log_dirs = []
    for example in dataset:
        repo_name = example["repo"].split("/")[-1]
        if "swe" in dataset_name.lower():
            if repo_split != "all" and repo_split not in example["instance_id"]:
                continue
        else:
            if repo_split != "all":
                if repo_split in SPLIT:
                    if repo_name not in SPLIT[repo_split]:
                        continue
                else:
                    pass
        hashed_test_ids = get_hash_string(example["test"]["test_dir"])
        if branch is None:
            git_path = os.path.join(base_dir, example["instance_id"])
            branch = get_active_branch(git_path)
            logger.debug("Branch not specified, resolved to: %s", branch)
        log_dir = (
            RUN_PYTEST_LOG_DIR
            / example["instance_id"].split("/")[-1]
            / branch
            / hashed_test_ids
        )
        log_dirs.append(str(log_dir))
        triples.append((example["instance_id"], example["test"]["test_dir"], branch))

    # Pre-flight: validate all required Docker images exist before launching parallel eval
    if not rebuild_image:
        missing_images = _preflight_check_images(dataset_name, dataset_split, backend)
        if missing_images:
            logger.error(
                f"Pre-flight failed: {len(missing_images)} Docker image(s) not found: "
                f"{missing_images}. Run 'commit0 build' first."
            )
            raise RuntimeError(
                f"Missing Docker images: {missing_images}. Run 'commit0 build' first."
            )

    with tqdm(total=len(repos), smoothing=0, desc="Evaluating repos") as pbar:
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # Create a future for running each instance
            futures = {
                executor.submit(
                    run_tests,
                    dataset_name,
                    dataset_split,
                    base_dir,
                    repo,
                    branch,
                    test_dir,
                    coverage,
                    backend,
                    timeout,
                    num_cpus,
                    rebuild_image=rebuild_image,
                    verbose=0,
                ): None
                for repo, test_dir, branch in triples
            }
            # Wait for each future to complete
            for future in as_completed(futures):
                pbar.update(1)
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Evaluation failed for a repo: {e}")

    # get numbers
    out = []
    for name in tqdm(log_dirs):
        report_file = os.path.join(name, "report.json")
        name = name.split("/")[2]
        test_ids = get_tests(name, verbose=0)
        test_ids = [xx for x in test_ids for xx in x if xx]
        if not os.path.exists(report_file):
            log_parent = os.path.dirname(report_file)
            test_output_file = os.path.join(log_parent, "test_output.txt")
            if os.path.exists(test_output_file):
                reason = "pytest_crash_or_collection_error"
            else:
                reason = "container_or_infra_failure"
            logger.warning(
                f"{name}: missing report.json ({reason}) — check {log_parent}"
            )
            out.append(
                {
                    "name": name,
                    "sum": 0,
                    "passed": 0,
                    "num_passed": 0,
                    "num_tests": len(test_ids),
                }
            )
            continue
        with open(report_file, "r") as file:
            report = json.load(file)
        # new version of pytest json
        if "created" in report:
            logger.debug("Using new pytest report format for %s", name)
            tests = {x["nodeid"]: x["call"] for x in report["tests"] if "call" in x}
        # old version of pytest json
        else:
            logger.debug("Using old pytest report format for %s", name)
            tests = {
                x["nodeid"]: {"outcome": x["outcome"], "duration": x["duration"]}
                for x in report
                if x["when"] == "call"
            }
        status = []
        runtimes = []
        no_runs = 0
        for test_id in test_ids:
            if test_id in tests and tests[test_id] is not None:
                status.append(tests[test_id]["outcome"])
                runtimes.append(tests[test_id]["duration"])
                no_runs += 1
            else:
                status.append("failed")
                runtimes.append(0)
        status = Counter(status)
        if no_runs == 0:
            total = 0
        else:
            total = sum(runtimes)
        if "xfail" not in status:
            status["xfail"] = 0
        passed = (status["passed"] + status["xfail"]) / sum(status.values())
        out.append(
            {
                "name": name,
                "sum": total,
                "passed": passed,
                "num_passed": status["passed"] + status["xfail"],
                "num_tests": len(test_ids),
            }
        )
    print("repo,runtime,num_passed/num_tests")
    out = sorted(out, key=lambda x: x["sum"], reverse=True)
    for x in out:
        print(f"{x['name']},{x['sum']},{x['num_passed']}/{x['num_tests']}")
    total_runtime = sum([x["sum"] for x in out])
    averaged_passed = sum([x["passed"] for x in out]) / len(out)
    print(f"total runtime: {total_runtime}")
    print(f"average pass rate: {averaged_passed}")
    logger.info("Evaluation complete: %d repos, avg pass rate %.2f%%, total runtime %.1fs", len(out), averaged_passed * 100, total_runtime)


__all__ = []
