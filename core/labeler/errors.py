"""Domain errors. The core raises these; only the API layer knows about HTTP status codes."""


class DomainError(Exception):
    """Base for errors that are the caller's fault, with a message safe to show them."""


class NotFound(DomainError, LookupError):
    """A policy version, posting or label that does not exist."""


class Conflict(DomainError):
    """The request is valid but clashes with current state (e.g. editing a published policy)."""


class InvalidInput(DomainError, ValueError):
    """The request content itself is wrong."""


class PolicyError(InvalidInput):
    """A policy document that does not parse or validate."""


class NotReady(DomainError):
    """A dependency is not configured yet (e.g. no API key for the chosen model provider)."""
