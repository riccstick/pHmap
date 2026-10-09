"""Shared exceptions for pHmap."""

from __future__ import annotations


class PHMapError(Exception):
    """Base class for errors that should be shown cleanly to users."""


class ConfigurationError(PHMapError):
    """Raised when user input or configuration is invalid."""


class WorkspaceError(PHMapError):
    """Raised when a safe run workspace cannot be created."""


class BackendError(PHMapError):
    """Raised when an external scientific backend fails."""


class ArtifactError(PHMapError):
    """Raised when an expected workflow artifact is absent or invalid."""

