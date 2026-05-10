from __future__ import annotations

import json
from dataclasses import asdict

from oauth_codex.core_types import OAuthTokens
from oauth_codex.store import (
    TOKEN_STORE_MODE_ENV,
    TOKEN_STORE_PATH_ENV,
    FallbackTokenStore,
    FileTokenStore,
    KeyringTokenStore,
    build_default_token_store,
)


def _tokens() -> OAuthTokens:
    return OAuthTokens(
        access_token="access-1",
        refresh_token="refresh-1",
        expires_at=9_999_999_999,
        token_type="Bearer",
        account_id="acct-1",
    )


def test_file_token_store_migrates_legacy_file(tmp_path) -> None:
    current_path = tmp_path / "native" / "auth.json"
    legacy_path = tmp_path / ".oauth_codex" / "auth.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps(asdict(_tokens())), encoding="utf-8")

    store = FileTokenStore(path=current_path, legacy_paths=[legacy_path])

    tokens = store.load()

    assert tokens is not None
    assert tokens.access_token == "access-1"
    assert current_path.exists()
    assert not legacy_path.exists()


def test_file_token_store_save_removes_legacy_copy(tmp_path) -> None:
    current_path = tmp_path / "native" / "auth.json"
    legacy_path = tmp_path / ".oauth_codex" / "auth.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps(asdict(_tokens())), encoding="utf-8")

    store = FileTokenStore(path=current_path, legacy_paths=[legacy_path])

    store.save(_tokens())

    assert current_path.exists()
    assert not legacy_path.exists()


def test_build_default_token_store_uses_file_mode_from_env(monkeypatch, tmp_path) -> None:
    expected_path = tmp_path / "server" / "auth.json"
    monkeypatch.setenv(TOKEN_STORE_MODE_ENV, "file")
    monkeypatch.setenv(TOKEN_STORE_PATH_ENV, str(expected_path))

    store = build_default_token_store()

    assert isinstance(store, FileTokenStore)
    assert store.path == expected_path


def test_build_default_token_store_uses_keyring_mode_from_env(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_STORE_MODE_ENV, "keyring")
    monkeypatch.delenv(TOKEN_STORE_PATH_ENV, raising=False)

    store = build_default_token_store()

    assert isinstance(store, KeyringTokenStore)


def test_build_default_token_store_uses_auto_mode_by_default(monkeypatch) -> None:
    monkeypatch.delenv(TOKEN_STORE_MODE_ENV, raising=False)
    monkeypatch.delenv(TOKEN_STORE_PATH_ENV, raising=False)

    store = build_default_token_store()

    assert isinstance(store, FallbackTokenStore)


def test_build_default_token_store_rejects_invalid_mode(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_STORE_MODE_ENV, "garbage")

    try:
        build_default_token_store()
    except ValueError as exc:
        assert "Invalid token store mode" in str(exc)
    else:
        raise AssertionError("Expected invalid token store mode to raise ValueError")