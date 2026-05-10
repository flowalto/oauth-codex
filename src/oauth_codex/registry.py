from __future__ import annotations

from dataclasses import dataclass

from oauth_codex.auth.config import OAuthConfig


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    display_name: str
    config: OAuthConfig


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        id="openai",
        display_name="OpenAI",
        config=OAuthConfig(),
    ),
}


def get_provider_spec(provider_id: str) -> ProviderSpec:
    """Look up a provider by its canonical ID. Raises KeyError if unknown."""
    return PROVIDERS[provider_id]


def list_provider_ids() -> list[str]:
    """Return a sorted list of registered provider IDs."""
    return sorted(PROVIDERS.keys())
