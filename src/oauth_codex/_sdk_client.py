from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any, cast

import httpx

from .resources.beta import AsyncBeta, Beta
from .resources.chat import AsyncChat, Chat
from .resources.files import AsyncFiles, Files
from .resources.models import AsyncModels, Models
from .resources.responses import AsyncResponses, Responses
from .resources.vector_stores import AsyncVectorStores, VectorStores

from ._base_client import AsyncAPIClient, SyncAPIClient
from .auth._oauth import OAuthProvider
from .auth._provider import AsyncAuthProvider, Headers, SyncAuthProvider
from .auth.config import OAuthConfig
from .core_types import TokenStore

DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"


def _with_auth_headers(
    headers: Mapping[str, str] | None, auth_headers: Headers | None
) -> dict[str, str] | None:
    if not headers and not auth_headers:
        return None
    merged: dict[str, str] = {}
    if headers:
        merged.update(headers)
    if auth_headers:
        merged.update(auth_headers)
    return merged


def _iter_sse_events(payload: str) -> Iterator[dict[str, Any]]:
    for block in payload.split("\n\n"):
        if not block.strip():
            continue

        data_lines: list[str] = []
        event_name: str | None = None

        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())

        if not data_lines:
            continue

        event_data = json.loads("\n".join(data_lines))
        if isinstance(event_data, dict):
            event_data.setdefault("raw", event_data)
            if event_name and "type" not in event_data:
                event_data["type"] = event_name
            yield event_data


def _payload_without_none(values: dict[str, Any]) -> dict[str, Any]:
    payload = {k: v for k, v in values.items() if v is not None}
    payload.pop("self", None)
    return payload


class _SyncEngine:
    def __init__(self, client: Client) -> None:
        self._client = client

    def responses_create(self, **kwargs: Any) -> Any:
        payload = _payload_without_none(kwargs)
        if payload.get("stream"):
            return self._stream_responses(payload)

        response = self._client.request("POST", "/responses", json_data=payload)
        return response.json()

    def responses_input_tokens_count(self, **kwargs: Any) -> Any:
        payload = _payload_without_none(kwargs)
        response = self._client.request(
            "POST", "/responses/input_tokens", json_data=payload
        )
        return response.json()

    def _stream_responses(self, payload: dict[str, Any]) -> Iterator[Any]:
        payload = dict(payload)
        payload["stream"] = True
        response = self._client.request("POST", "/responses", json_data=payload)
        yield from _iter_sse_events(response.text)


class _AsyncEngine:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def aresponses_create(self, **kwargs: Any) -> Any:
        payload = _payload_without_none(kwargs)
        if payload.get("stream"):
            return self._stream_responses(payload)

        response = await self._client.request("POST", "/responses", json_data=payload)
        return response.json()

    async def aresponses_input_tokens_count(self, **kwargs: Any) -> Any:
        payload = _payload_without_none(kwargs)
        response = await self._client.request(
            "POST", "/responses/input_tokens", json_data=payload
        )
        return response.json()

    async def _stream_responses(self, payload: dict[str, Any]) -> AsyncIterator[Any]:
        payload = dict(payload)
        payload["stream"] = True
        response = await self._client.request("POST", "/responses", json_data=payload)
        for event in _iter_sse_events(response.text):
            yield event


class Client(SyncAPIClient):
    def __init__(
        self,
        *,
        token_store: TokenStore | None = None,
        oauth_config: OAuthConfig | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        super().__init__(
            base_url=(base_url or DEFAULT_BASE_URL),
            timeout=timeout,
            max_retries=max_retries,
        )
        self._token_store = token_store
        self._oauth_config = oauth_config
        self._responses: Responses | None = None
        self._files: Files | None = None
        self._models: Models | None = None
        self._vector_stores: VectorStores | None = None
        self._chat: Chat | None = None
        self._beta: Beta | None = None
        self._auth_provider: SyncAuthProvider | None = None
        self._vector_file_batches: dict[str, dict[str, Any]] = {}
        self._engine = _SyncEngine(self)

    def authenticate(self) -> None:
        self.auth.ensure_valid(interactive=True)

    @property
    def auth(self) -> SyncAuthProvider:
        if self._auth_provider is None:
            self._auth_provider = self._build_auth_provider()
        return self._auth_provider

    @property
    def responses(self) -> Responses:
        if self._responses is None:
            self._responses = Responses(cast(Any, self))
        return self._responses

    @property
    def files(self) -> Files:
        if self._files is None:
            self._files = Files(cast(Any, self))
        return self._files

    @property
    def models(self) -> Models:
        if self._models is None:
            self._models = Models(cast(Any, self))
        return self._models

    @property
    def vector_stores(self) -> VectorStores:
        if self._vector_stores is None:
            self._vector_stores = VectorStores(cast(Any, self))
        return self._vector_stores

    @property
    def chat(self) -> Chat:
        if self._chat is None:
            self._chat = Chat(cast(Any, self))
        return self._chat

    @property
    def beta(self) -> Beta:
        if self._beta is None:
            self._beta = Beta(cast(Any, self))
        return self._beta

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json_data: Any = None,
        data: Any = None,
        files: Any = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        auth_headers = self.auth.get_headers()
        merged_headers = _with_auth_headers(headers, auth_headers)
        return super().request(
            method,
            path,
            params=params,
            headers=merged_headers,
            json_data=json_data,
            data=data,
            files=files,
            timeout=timeout,
        )

    def _build_auth_provider(self) -> SyncAuthProvider:
        return OAuthProvider(
            token_store=cast(Any, self._token_store),
            oauth_config=self._oauth_config,
            timeout=self.timeout,
        )


class AsyncClient(AsyncAPIClient):
    def __init__(
        self,
        *,
        token_store: TokenStore | None = None,
        oauth_config: OAuthConfig | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        super().__init__(
            base_url=(base_url or DEFAULT_BASE_URL),
            timeout=timeout,
            max_retries=max_retries,
        )
        self._token_store = token_store
        self._oauth_config = oauth_config
        self._responses: AsyncResponses | None = None
        self._files: AsyncFiles | None = None
        self._models: AsyncModels | None = None
        self._vector_stores: AsyncVectorStores | None = None
        self._chat: AsyncChat | None = None
        self._beta: AsyncBeta | None = None
        self._auth_provider: AsyncAuthProvider | None = None
        self._vector_file_batches: dict[str, dict[str, Any]] = {}
        self._engine = _AsyncEngine(self)

    async def authenticate(self) -> None:
        await self.auth.aensure_valid(interactive=True)

    @property
    def auth(self) -> AsyncAuthProvider:
        if self._auth_provider is None:
            self._auth_provider = self._build_auth_provider()
        return self._auth_provider

    @property
    def responses(self) -> AsyncResponses:
        if self._responses is None:
            self._responses = AsyncResponses(cast(Any, self))
        return self._responses

    @property
    def files(self) -> AsyncFiles:
        if self._files is None:
            self._files = AsyncFiles(cast(Any, self))
        return self._files

    @property
    def models(self) -> AsyncModels:
        if self._models is None:
            self._models = AsyncModels(cast(Any, self))
        return self._models

    @property
    def vector_stores(self) -> AsyncVectorStores:
        if self._vector_stores is None:
            self._vector_stores = AsyncVectorStores(cast(Any, self))
        return self._vector_stores

    @property
    def chat(self) -> AsyncChat:
        if self._chat is None:
            self._chat = AsyncChat(cast(Any, self))
        return self._chat

    @property
    def beta(self) -> AsyncBeta:
        if self._beta is None:
            self._beta = AsyncBeta(cast(Any, self))
        return self._beta

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json_data: Any = None,
        data: Any = None,
        files: Any = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        auth_headers = await self.auth.aget_headers()
        merged_headers = _with_auth_headers(headers, auth_headers)
        return await super().request(
            method,
            path,
            params=params,
            headers=merged_headers,
            json_data=json_data,
            data=data,
            files=files,
            timeout=timeout,
        )

    def _build_auth_provider(self) -> AsyncAuthProvider:
        return OAuthProvider(
            token_store=cast(Any, self._token_store),
            oauth_config=self._oauth_config,
            timeout=self.timeout,
        )
