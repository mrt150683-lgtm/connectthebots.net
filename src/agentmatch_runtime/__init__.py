"""Local, fictional agent operations with persistent authority and a separate viewer.

This package is optional and separate from the immutable, dependency-free R0
reference. It does not establish a real-person identity or a production vault.
"""

from .runtime import AgentRuntime, AgentRuntimeError, command_digest

__all__ = ["AgentRuntime", "AgentRuntimeError", "command_digest"]
