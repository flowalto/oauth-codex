[English](README.md) | [한국어](README.ko.md)

# oauth-codex

OAuth PKCE-based Python SDK for the Codex backend.

## Highlights

- Resource-style clients: `Client` and `AsyncClient`
- OAuth PKCE only, with interactive login and automatic token refresh
- OpenAI-style `chat.completions.create(...)`
- Lower-level `responses.create(...)`, `responses.parse(...)`, and `responses.stream(...)`
- Callable tool loop helpers via `client.beta.chat.completions.run_tools(...)`

## Installation

```bash
pip install oauth-codex
```

Requires Python 3.11 or newer.

## Quick Start

### Synchronous Client

```python
from oauth_codex import Client

client = Client()
client.authenticate()

completion = client.chat.completions.create(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "Hello from oauth-codex"}],
)
print(completion.choices[0].message.content)
```

### Asynchronous Client

```python
import asyncio

from oauth_codex import AsyncClient


async def main():
    client = AsyncClient()
    await client.authenticate()

    completion = await client.chat.completions.create(
        model="gpt-5.3-codex",
        messages=[{"role": "user", "content": "Hello async"}],
    )
    print(completion.choices[0].message.content)


asyncio.run(main())
```

## Authentication

This SDK uses OAuth PKCE only. API keys are not supported.

```python
client = Client()
client.authenticate()
```

On first authentication, the SDK prints an authorization URL, waits for the browser sign-in flow, and asks you to paste the localhost callback URL back into the terminal. Tokens are stored locally and refreshed automatically on later requests.

By default, oauth-codex stores tokens in the OS keyring when available. If keyring storage fails, it falls back to a per-user file in the platform-native config directory, such as `%LOCALAPPDATA%\\oauth-codex\\auth.json` on Windows, `~/Library/Application Support/oauth-codex/auth.json` on macOS, or `~/.config/oauth-codex/auth.json` on Linux. Legacy `~/.oauth_codex/auth.json` files are migrated automatically when read.

For deterministic deployments on clean machines, you can control the storage backend explicitly:

- `CODEX_OAUTH_TOKEN_STORE=auto` uses keyring first, then file fallback.
- `CODEX_OAUTH_TOKEN_STORE=keyring` requires OS keyring storage and fails if keyring is unavailable.
- `CODEX_OAUTH_TOKEN_STORE=file` always uses the file backend.
- `CODEX_OAUTH_TOKEN_PATH=/absolute/path/to/auth.json` overrides the file location when using file storage or auto fallback.

For headless servers, use `CODEX_OAUTH_TOKEN_STORE=file` and set `CODEX_OAUTH_TOKEN_PATH` to a deployment-managed per-user secret path if you do not want keyring-dependent behavior.

## Main API Surface

### Chat Completions

```python
response = client.chat.completions.create(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "Write a sorting function"}],
)
print(response.choices[0].message.content)
```

### Responses Resource

```python
response = client.responses.create(
    model="gpt-5.3-codex",
    input=[{"role": "user", "content": "Analyze this code snippet."}],
)
print(response.output_text)
```

### Streaming

```python
for event in client.responses.stream(
    model="gpt-5.3-codex",
    input=[{"role": "user", "content": "Say hello in three words"}],
):
    if event.type == "text_delta" and event.delta:
        print(event.delta, end="", flush=True)
```

### Structured Output

```python
from pydantic import BaseModel


class Summary(BaseModel):
    title: str
    score: int


response = client.responses.parse(
    model="gpt-5.3-codex",
    input=[{"role": "user", "content": "Return JSON with title and score"}],
    response_format=Summary,
)
print(response.parsed)
```

### Tool Execution

```python
def add(a: int, b: int) -> int:
    return a + b


completion = client.beta.chat.completions.run_tools(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "What is 2 + 3?"}],
    tools=[add],
)
print(completion.choices[0].message.content)
```

## Available Namespaces

- `client.chat.completions`
- `client.responses`
- `client.files`
- `client.vector_stores`
- `client.vector_stores.files`
- `client.vector_stores.file_batches`
- `client.models`
- `client.beta.chat.completions`

`AsyncClient` exposes the same namespaces with async methods.

## Removed In 4.0

- `authenticate_on_init`
- `generate`, `agenerate`, `stream`, `astream`
- legacy `OAuthCodexClient` and `AsyncOAuthCodexClient`
- module-level proxy usage such as `oauth_codex.responses.create(...)`

## Error Handling

The package exports OpenAI-style exception classes such as `AuthenticationError`, `RateLimitError`, `APIConnectionError`, and `APIStatusError`.

## Documentation

- English: [`docs/en/index.md`](docs/en/index.md)
- Korean: [`docs/ko/index.md`](docs/ko/index.md)

## Development

```bash
pip install -e .[dev]
pytest -q
python -m build
```

## Changelog

[`CHANGELOG.md`](CHANGELOG.md)
