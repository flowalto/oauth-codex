from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

from .core_types import OAuthTokens, TokenStore


TOKEN_STORE_MODE_ENV = "CODEX_OAUTH_TOKEN_STORE"
TOKEN_STORE_PATH_ENV = "CODEX_OAUTH_TOKEN_PATH"
VALID_TOKEN_STORE_MODES = {"auto", "keyring", "file"}


def _default_file_path() -> Path:
    return Path(user_config_dir("oauth-codex", False, ensure_exists=False)) / "auth.json"


def _resolve_file_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()

    env_path = os.getenv(TOKEN_STORE_PATH_ENV)
    if env_path:
        return Path(env_path).expanduser()

    return _default_file_path()


def _resolve_store_mode(store_mode: str | None = None) -> str:
    resolved = (store_mode or os.getenv(TOKEN_STORE_MODE_ENV) or "auto").strip().lower()
    if resolved not in VALID_TOKEN_STORE_MODES:
        valid_modes = ", ".join(sorted(VALID_TOKEN_STORE_MODES))
        raise ValueError(
            f"Invalid token store mode '{resolved}'. Expected one of: {valid_modes}"
        )
    return resolved


DEFAULT_FILE_PATH = _resolve_file_path()
LEGACY_FILE_PATHS = tuple(
    path
    for path in (Path.home() / ".oauth_codex" / "auth.json",)
    if path != DEFAULT_FILE_PATH
)
DEFAULT_KEYRING_SERVICE = "oauth-codex"


def _tokens_from_payload(payload: dict[str, Any]) -> OAuthTokens:
    return OAuthTokens(
        access_token=payload["access_token"],
        api_key=payload.get("api_key"),
        refresh_token=payload.get("refresh_token"),
        id_token=payload.get("id_token"),
        token_type=payload.get("token_type", "Bearer"),
        scope=payload.get("scope"),
        expires_at=payload.get("expires_at"),
        account_id=payload.get("account_id"),
        last_refresh=payload.get("last_refresh"),
    )


def _tokens_to_payload(tokens: OAuthTokens) -> dict[str, Any]:
    return asdict(tokens)


class FileTokenStore(TokenStore):
    def __init__(
        self,
        path: str | Path | None = None,
        legacy_paths: list[str | Path] | tuple[str | Path, ...] | None = None,
    ) -> None:
        self.path = _resolve_file_path(path)
        candidate_legacy_paths = legacy_paths or LEGACY_FILE_PATHS
        self.legacy_paths = tuple(
            Path(candidate).expanduser()
            for candidate in candidate_legacy_paths
            if Path(candidate).expanduser() != self.path
        )

    def _load_path(self, path: Path) -> OAuthTokens | None:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "access_token" not in data:
            return None
        return _tokens_from_payload(data)

    def _delete_path(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return

    def _migrate_legacy_tokens(self, legacy_path: Path, tokens: OAuthTokens) -> OAuthTokens:
        try:
            self.save(tokens)
        except Exception:
            return tokens

        self._delete_path(legacy_path)
        return tokens

    def load(self) -> OAuthTokens | None:
        try:
            tokens = self._load_path(self.path)
            if tokens:
                return tokens

            for legacy_path in self.legacy_paths:
                legacy_tokens = self._load_path(legacy_path)
                if legacy_tokens:
                    return self._migrate_legacy_tokens(legacy_path, legacy_tokens)

            return None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, tokens: OAuthTokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        serialized = json.dumps(_tokens_to_payload(tokens), ensure_ascii=True, indent=2)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(self.path.parent), delete=False
        ) as fp:
            tmp_path = Path(fp.name)
            fp.write(serialized)
            fp.flush()
            os.fsync(fp.fileno())

        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_path, self.path)

        for legacy_path in self.legacy_paths:
            self._delete_path(legacy_path)

    def delete(self) -> None:
        self._delete_path(self.path)
        for legacy_path in self.legacy_paths:
            self._delete_path(legacy_path)


class KeyringTokenStore(TokenStore):
    def __init__(self, service_name: str = DEFAULT_KEYRING_SERVICE, username: str = "default") -> None:
        self.service_name = service_name
        self.username = username

    def _require_keyring(self):
        try:
            import keyring  # type: ignore
        except ImportError as exc:
            raise RuntimeError("keyring is not installed") from exc
        return keyring

    def load(self) -> OAuthTokens | None:
        keyring = self._require_keyring()
        raw = keyring.get_password(self.service_name, self.username)
        if not raw:
            return None
        payload = json.loads(raw)
        return _tokens_from_payload(payload)

    def save(self, tokens: OAuthTokens) -> None:
        keyring = self._require_keyring()
        keyring.set_password(
            self.service_name,
            self.username,
            json.dumps(_tokens_to_payload(tokens), ensure_ascii=True),
        )

    def delete(self) -> None:
        keyring = self._require_keyring()
        try:
            keyring.delete_password(self.service_name, self.username)
        except Exception:
            return


class FallbackTokenStore(TokenStore):
    def __init__(
        self,
        keyring_store: TokenStore | None = None,
        file_store: TokenStore | None = None,
    ) -> None:
        self.keyring_store = keyring_store or KeyringTokenStore(service_name=DEFAULT_KEYRING_SERVICE)
        self.file_store = file_store or FileTokenStore(path=DEFAULT_FILE_PATH)

    def _safe_load(self, store: TokenStore) -> OAuthTokens | None:
        try:
            return store.load()
        except Exception:
            return None

    def _safe_delete(self, store: TokenStore) -> None:
        try:
            store.delete()
        except Exception:
            return

    def load(self) -> OAuthTokens | None:
        tokens = self._safe_load(self.keyring_store)
        if tokens:
            return tokens
        return self._safe_load(self.file_store)

    def save(self, tokens: OAuthTokens) -> None:
        try:
            self.keyring_store.save(tokens)
            return
        except Exception:
            self.file_store.save(tokens)

    def delete(self) -> None:
        self._safe_delete(self.keyring_store)
        self._safe_delete(self.file_store)


def build_default_token_store(
    *,
    store_mode: str | None = None,
    file_path: str | Path | None = None,
    service_name: str = DEFAULT_KEYRING_SERVICE,
    username: str = "default",
) -> TokenStore:
    resolved_mode = _resolve_store_mode(store_mode)

    resolved_file_store = FileTokenStore(path=file_path)
    resolved_keyring_store = KeyringTokenStore(
        service_name=service_name,
        username=username,
    )

    if resolved_mode == "file":
        return resolved_file_store

    if resolved_mode == "keyring":
        return resolved_keyring_store

    return FallbackTokenStore(
        keyring_store=resolved_keyring_store,
        file_store=resolved_file_store,
    )
