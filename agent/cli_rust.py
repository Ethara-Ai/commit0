"""CLI entry point for the Rust aider pipeline.

Usage::

    python -m agent.cli_rust run <branch> [OPTIONS]
"""

import typer

from agent.run_rust_agent import run_rust_agent
from commit0.harness.constants import RUN_AGENT_LOG_DIR

rust_agent_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_show_locals=False,
    help="Run the aider agent on Rust Commit-0 repositories.",
)


@rust_agent_app.command()
def run(
    branch: str = typer.Argument(
        ...,
        help="Branch for the agent to commit changes",
    ),
    override_previous_changes: bool = typer.Option(
        False,
        help="Reset branch to base commit before running",
    ),
    backend: str = typer.Option(
        "modal",
        help="Test backend (ignored for Rust — uses cargo test directly)",
    ),
    agent_config_file: str = typer.Option(
        ".agent.yaml",
        help="Path to the agent config file",
    ),
    commit0_config_file: str = typer.Option(
        ".commit0.yaml",
        help="Path to the commit0 config file",
    ),
    log_dir: str = typer.Option(
        str(RUN_AGENT_LOG_DIR.resolve()),
        help="Log directory to store the logs",
    ),
    max_parallel_repos: int = typer.Option(
        1,
        help="Maximum number of repos to process in parallel",
    ),
) -> None:
    """Run the aider agent on Rust repositories."""
    run_rust_agent(
        branch=branch,
        override_previous_changes=override_previous_changes,
        backend=backend,
        agent_config_file=agent_config_file,
        commit0_config_file=commit0_config_file,
        log_dir=log_dir,
        max_parallel_repos=max_parallel_repos,
    )


if __name__ == "__main__":
    rust_agent_app()
