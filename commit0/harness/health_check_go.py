"""Go-specific health checks for Docker images."""

from __future__ import annotations

import logging
from typing import Optional

import docker

logger = logging.getLogger(__name__)


def check_go_version(
    client: docker.DockerClient,
    image_name: str,
    expected: str,
) -> tuple[bool, str]:
    version_cmd = 'go version | grep -oE "go[0-9]+\\.[0-9]+\\.?[0-9]*"'
    try:
        output = client.containers.run(
            image_name,
            ["bash", "-c", version_cmd],
            remove=True,
            stderr=True,
            stdout=True,
        )
        actual = output.decode().strip()
        if f"go{expected}" in actual:
            return True, f"Go {actual}"
        return False, f"Expected Go {expected}, got {actual}"
    except Exception as e:
        logger.warning("Non-critical failure during Go version check: %s", e)
        return False, f"Go version check error: {e}"


def check_go_tools(
    client: docker.DockerClient,
    image_name: str,
) -> tuple[bool, str]:
    check_cmd = "which goimports && which staticcheck && echo OK"
    try:
        output = client.containers.run(
            image_name,
            ["bash", "-c", check_cmd],
            remove=True,
            stderr=True,
            stdout=True,
        )
        if b"OK" in output:
            return True, "goimports and staticcheck available"
        return False, f"Go tools check unexpected output: {output.decode().strip()}"
    except Exception as e:
        logger.warning("Non-critical failure during Go tools check: %s", e)
        return False, f"Go tools check error: {e}"


def run_go_health_checks(
    client: docker.DockerClient,
    image_name: str,
    go_version: Optional[str] = None,
) -> list[tuple[bool, str, str]]:
    results: list[tuple[bool, str, str]] = []

    if go_version:
        passed, detail = check_go_version(client, image_name, go_version)
        results.append((passed, "go_version", detail))

    passed, detail = check_go_tools(client, image_name)
    results.append((passed, "go_tools", detail))

    return results


__all__ = [
    "check_go_version",
    "check_go_tools",
    "run_go_health_checks",
]
