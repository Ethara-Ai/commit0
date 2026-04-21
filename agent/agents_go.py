"""Go-specific agent wrapper for commit0.

Mirrors agents.py but configures the aider Coder for Go source files
with Go-specific lint commands and test commands.
"""

import logging
import os
import re
import sys
from abc import ABC, abstractmethod
from io import StringIO
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GoAgentReturn(ABC):
    """Base class for Go agent run results."""

    def __init__(self, log_file: Optional[str] = None):
        self.log_file = log_file
        self.last_cost: float = 0.0


class GoAgents(ABC):
    """Base class for Go agents."""

    def __init__(self, max_iteration: int):
        self.max_iteration = max_iteration

    @abstractmethod
    def run(
        self,
        message: str,
        test_cmd: str,
        lint_cmd: str,
        fnames: list[str],
        log_dir: str,
        test_first: bool = False,
        lint_first: bool = False,
    ) -> GoAgentReturn:
        raise NotImplementedError


class AiderGoReturn(GoAgentReturn):
    """Result from an aider Go agent run."""

    def __init__(self, log_file: Optional[str] = None):
        super().__init__(log_file)
        self.last_cost = self._parse_cost()

    def _parse_cost(self) -> float:
        if not self.log_file or not os.path.exists(self.log_file):
            return 0.0
        try:
            with open(self.log_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            costs = re.findall(r"\$(\d+\.\d+)", content)
            return float(costs[-1]) if costs else 0.0
        except Exception:
            return 0.0


class AiderGoAgents(GoAgents):
    """Aider-based agent configured for Go source files.

    Uses aider's Coder with Go-specific settings:
    - lint_cmds maps "go" instead of "python"
    - test_cmd runs go test
    - System prompt appends Go-specific "NEVER edit test files" instruction
    """

    def __init__(
        self,
        max_iteration: int,
        model_name: str,
        cache_prompts: bool = True,
    ):
        super().__init__(max_iteration)
        self.model_name = model_name
        self.cache_prompts = cache_prompts

    def run(
        self,
        message: str,
        test_cmd: str,
        lint_cmd: str,
        fnames: list[str],
        log_dir: str,
        test_first: bool = False,
        lint_first: bool = False,
    ) -> AiderGoReturn:
        from aider.coders import Coder
        from aider.models import Model
        from aider.io import InputOutput

        log_file = os.path.join(log_dir, "aider.log")
        os.makedirs(log_dir, exist_ok=True)

        model = Model(self.model_name)
        io = InputOutput(yes=True, chat_history_file=log_file)

        lint_cmds = {"go": lint_cmd} if lint_cmd else None

        coder = Coder.create(
            main_model=model,
            fnames=fnames,
            io=io,
            auto_lint=bool(lint_cmd),
            auto_test=bool(test_cmd),
            lint_cmds=lint_cmds,
            test_cmd=test_cmd,
            max_chat_history_tokens=0,
            cache_prompts=self.cache_prompts,
        )

        coder.max_reflections = self.max_iteration
        coder.stream = False

        coder.cur_messages += [
            {
                "role": "system",
                "content": (
                    "IMPORTANT: You must NEVER modify, edit, or delete any "
                    "test files (files ending with _test.go). Test files are "
                    "read-only and define the expected behavior. If tests fail, "
                    "fix the implementation code, not the tests."
                ),
            }
        ]

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        try:
            if test_first:
                coder.run(
                    message
                    + "\n\nPlease run the tests first to understand what's expected."
                )
            elif lint_first:
                coder.run(
                    message + "\n\nPlease run the linter first to check code quality."
                )
            else:
                coder.run(message)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        return AiderGoReturn(log_file=log_file)
