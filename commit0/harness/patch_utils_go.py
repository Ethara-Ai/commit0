"""Go-specific patch generation — filters diffs to Go source files only."""

import git as gitpython

from commit0.harness.utils import generate_patch_between_commits


GO_PATCH_EXTENSIONS = (".go", "go.mod", "go.sum")


def generate_go_patch(repo_path: str, old_commit: str, new_commit: str) -> str:
    """Generate a patch filtered to .go/go.mod/go.sum files that existed at base commit.

    Prevents LLM-generated non-Go files from contaminating the diff.
    NOTE: generate_patch_between_commits takes git.Repo, not str.
    """
    repo = gitpython.Repo(repo_path)
    full_patch = generate_patch_between_commits(repo, old_commit, new_commit)

    if not full_patch or not full_patch.strip():
        return full_patch

    filtered_lines: list[str] = []
    include_hunk = False

    for line in full_patch.split("\n"):
        if line.startswith("diff --git"):
            parts = line.split(" ")
            if len(parts) >= 4:
                file_path = parts[2][2:]  # strip "a/" prefix
                include_hunk = any(
                    file_path.endswith(ext) for ext in GO_PATCH_EXTENSIONS
                )
            else:
                include_hunk = False

        if include_hunk:
            filtered_lines.append(line)

    return "\n".join(filtered_lines) if filtered_lines else ""


__all__ = ["generate_go_patch"]
