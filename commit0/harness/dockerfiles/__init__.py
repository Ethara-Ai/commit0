from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from commit0.harness.constants import DOCKERFILES_DIR, SUPPORTED_PYTHON_VERSIONS


def get_dockerfile_base(python_version: str) -> str:
    if python_version not in SUPPORTED_PYTHON_VERSIONS:
        raise ValueError(
            f"Unsupported Python version: {python_version}. "
            f"Supported: {sorted(SUPPORTED_PYTHON_VERSIONS)}"
        )
    template_path = DOCKERFILES_DIR / f"Dockerfile.python{python_version}"
    if not template_path.exists():
        raise FileNotFoundError(f"Base Dockerfile template not found: {template_path}")
    return template_path.read_text()


def get_dockerfile_repo(
    base_image: str,
    pre_install: Optional[List[str]] = None,
    packages: Optional[str] = None,
    pip_packages: Optional[List[str]] = None,
    install_cmd: Optional[str] = None,
) -> str:
    lines = [
        f"FROM {base_image}",
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
        "# Set workdir to repo root so relative paths (requirements.txt, -e .) resolve",
        "WORKDIR /testbed/",
        "",
    ]

    apt_packages: list[str] = []
    if pre_install:
        for cmd in pre_install:
            if cmd.startswith("apt-get install") or cmd.startswith("apt install"):
                pkgs = cmd.split("install", 1)[1].replace("-y", "").strip().split()
                apt_packages.extend(p for p in pkgs if not p.startswith("-"))
            else:
                lines.append(f"RUN {cmd}")

    if apt_packages:
        pkg_str = " \\\n    ".join(sorted(set(apt_packages)))
        lines.append(
            f"RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
            f"    {pkg_str} \\\n"
            f"    && rm -rf /var/lib/apt/lists/*"
        )
        lines.append("")

    if packages:
        lines.append(f"RUN pip install --no-cache-dir -r {packages}")
        lines.append("")

    if pip_packages:
        escaped = " ".join(f'"{p}"' for p in pip_packages)
        lines.append(f"RUN pip install --no-cache-dir {escaped}")
        lines.append("")

    if install_cmd:
        pip_cmd = install_cmd.replace("uv pip install", "pip install --no-cache-dir")
        if pip_cmd.startswith("pip install"):
            pip_cmd = "pip install --no-cache-dir" + pip_cmd[len("pip install") :]
        lines.append(f"RUN {pip_cmd}")
        lines.append("")

    lines.append(
        "RUN pip install --no-cache-dir -U pytest pytest-cov coverage pytest-json-report"
    )
    lines.append("")
    lines.append("WORKDIR /testbed/")
    lines.append("")

    return "\n".join(lines)


__all__: list[str] = ["get_dockerfile_base", "get_dockerfile_repo"]
