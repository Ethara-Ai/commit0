"""Rust development environment health checks.

Verifies that the Rust toolchain (rustc, cargo, cargo-nextest, clippy, rustfmt)
is installed and accessible.
"""

from __future__ import annotations

import logging
import subprocess
from typing import List, Tuple

logger = logging.getLogger(__name__)

_RUST_TOOLS: List[Tuple[str, List[str], str]] = [
    ("rustc", ["rustc", "--version"], "Install via https://rustup.rs"),
    ("cargo", ["cargo", "--version"], "Install via https://rustup.rs"),
    (
        "cargo-nextest",
        ["cargo", "nextest", "--version"],
        "cargo install cargo-nextest",
    ),
    ("clippy", ["cargo", "clippy", "--version"], "rustup component add clippy"),
    ("rustfmt", ["rustfmt", "--version"], "rustup component add rustfmt"),
]


def _check_tool(
    name: str, cmd: List[str], hint: str
) -> Tuple[bool, str, str]:

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            version = result.stdout.strip().split("\n")[0]
            return True, name, version
        stderr = result.stderr.strip().split("\n")[0] if result.stderr else "unknown error"
        logger.debug("%s failed (rc=%d): %s", name, result.returncode, stderr)
        return False, name, f"FAIL — {stderr}  (fix: {hint})"
    except FileNotFoundError:
        logger.debug("%s not found on PATH", name)
        return False, name, f"FAIL — not found  (fix: {hint})"
    except subprocess.TimeoutExpired:
        logger.debug("%s timed out", name)
        return False, name, f"FAIL — timed out  (fix: {hint})"
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s unexpected error: %s", name, exc)
        return False, name, f"FAIL — {exc}  (fix: {hint})"


def main(base_dir: str = ".") -> bool:
    """Verify the Rust toolchain is ready. Returns True if all checks pass."""
    results: List[Tuple[bool, str, str]] = []
    for display_name, cmd, hint in _RUST_TOOLS:
        results.append(_check_tool(display_name, cmd, hint))

    print("Rust Health Check:")
    all_passed = True
    for passed, name, detail in results:
        status = "PASS" if passed else "FAIL"
        if passed:
            version_info = detail.split()
            ver = ""
            for token in version_info:
                if any(c.isdigit() for c in token):
                    ver = token.strip("()")
                    break
            suffix = f" ({ver})" if ver else ""
            print(f"  {name:<15} ... {status}{suffix}")
        else:
            print(f"  {name:<15} ... {detail}")
            all_passed = False

    if all_passed:
        logger.info("Rust health check: all tools available")
    else:
        logger.warning("Rust health check: some tools missing or broken")

    return all_passed


__all__ = ["main"]
