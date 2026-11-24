"""Port: where API keys live.

Where API keys live. A port with three adapters, picked by SECRETS_BACKEND (bootstrap.py):

    env  read-only: environment variables / .env (the 12-factor default; nothing stored by the app)
    db   local and compose: keys entered on the Settings page, encrypted with a master key
         (Fernet: AES-128-CBC + HMAC-SHA256) before they reach the database. The master key
         comes from SECRETS_KEY, or is generated once into data/secrets.key (mode 0600,
         gitignored), which every container sees through the ./data mount.
    aws  production: AWS Secrets Manager (KMS-encrypted, IAM-scoped, audited by CloudTrail).
         Task roles get secretsmanager:GetSecretValue on the `policy-labeler/*` prefix only.

Every adapter falls back to the environment, so a key in .env always works. The API never
returns a key: only where it came from and its last four characters (see DESIGN.md §6).
"""

from __future__ import annotations

from typing import Any, Protocol

from labeler.errors import InvalidInput

# The only secrets the app knows. Anything else is rejected, so the store cannot become a
# general-purpose key-value bucket written from the UI.
KNOWN = {
    "ANTHROPIC_API_KEY": "Claude API",
    "OPENAI_API_KEY": "OpenAI",
    "COMPAT_API_KEY": "OpenAI-compatible endpoint (Gemini, Groq, OpenRouter, ...)",
}


def hint(value: str) -> str:
    return "…" + value[-4:] if len(value) >= 8 else "…"


class SecretStore(Protocol):
    backend: str

    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> bool: ...
    def describe(self, name: str) -> dict[str, Any]: ...


def check_known(name: str) -> None:
    if name not in KNOWN:
        raise InvalidInput(f"unknown secret {name!r}; known: {', '.join(KNOWN)}")
