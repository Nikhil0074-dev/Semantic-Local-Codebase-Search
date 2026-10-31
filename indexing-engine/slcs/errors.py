"""Typed errors so the UI can show actionable messages (see docs/planning section 38)."""


class SlcsError(Exception):
    code = "internal_error"
    recoverable = False

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class QueryError(SlcsError):
    code = "invalid_query"
    recoverable = True


class RepositoryError(SlcsError):
    code = "repository_error"
    recoverable = True


class RepositoryUnavailable(RepositoryError):
    code = "repository_unavailable"


class EmbeddingError(SlcsError):
    code = "embedding_unavailable"
    recoverable = True


class ModelMismatchError(SlcsError):
    """Index was built with another embedding model; a full re-index is required."""
    code = "model_mismatch"
    recoverable = True


class IncompatibleIndexError(SlcsError):
    code = "incompatible_index"


class CorruptIndexError(SlcsError):
    code = "corrupt_index"


class PathSecurityError(SlcsError):
    code = "path_not_allowed"
    recoverable = True


class EditorUnavailable(SlcsError):
    code = "editor_unavailable"
    recoverable = True


class JobCancelled(SlcsError):
    code = "cancelled"
    recoverable = True
