"""Go agent display utilities.

The TerminalDisplay from display.py is language-agnostic and can be
reused directly. This module re-exports it for Go agent convenience
and provides any Go-specific display helpers if needed.
"""

from agent.display import TerminalDisplay

__all__ = ["TerminalDisplay"]
