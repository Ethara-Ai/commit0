import json
import logging
from dataclasses import dataclass
from typing import Dict, List

from commit0.harness.constants import TestStatus

logger = logging.getLogger(__name__)

__all__ = ["RustTestResult", "parse_nextest_json", "parse_nextest_report"]

_EVENT_STATUS_MAP: Dict[str, TestStatus] = {
    "ok": TestStatus.PASSED,
    "failed": TestStatus.FAILED,
    "ignored": TestStatus.SKIPPED,
    "timeout": TestStatus.ERROR,
}


@dataclass
class RustTestResult:
    name: str
    status: TestStatus
    duration: float
    stdout: str


def parse_nextest_json(json_str: str) -> List[RustTestResult]:
    results: List[RustTestResult] = []
    if not json_str or not json_str.strip():
        return results

    for line in json_str.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed JSON line: %s", line[:200])
            continue

        if obj.get("type") != "test":
            continue

        event = obj.get("event")
        status = _EVENT_STATUS_MAP.get(event)  # type: ignore[arg-type]
        if status is None:
            continue

        results.append(
            RustTestResult(
                name=obj.get("name", ""),
                status=status,
                duration=float(obj.get("exec_time", 0.0)),
                stdout=obj.get("stdout", ""),
            )
        )

    return results


def parse_nextest_report(report_path: str) -> Dict:
    empty: Dict = {
        "tests": [],
        "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "error": 0},
    }
    try:
        with open(report_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        logger.error("Report file not found: %s", report_path)
        return empty

    results = parse_nextest_json(content)
    if not results:
        return empty

    tests = [
        {"name": r.name, "outcome": r.status.value, "duration": r.duration}
        for r in results
    ]
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.status == TestStatus.PASSED),
        "failed": sum(1 for r in results if r.status == TestStatus.FAILED),
        "skipped": sum(1 for r in results if r.status == TestStatus.SKIPPED),
        "error": sum(1 for r in results if r.status == TestStatus.ERROR),
    }
    return {"tests": tests, "summary": summary}
