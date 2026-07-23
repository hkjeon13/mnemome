from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ..errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class ApiPrincipal:
    tenant_id: str
    principal_id: str
    roles: frozenset[str]

    def can(self, role: str) -> bool:
        return "admin" in self.roles or role in self.roles


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_path: str
    api_keys: dict[str, ApiPrincipal]
    log_level: str
    storage_backend: str = "sqlite"
    database_url: str | None = None
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_command_timeout_s: float = 5.0
    tenant_delegation_secret: str | None = None
    tenant_delegation_max_skew_s: int = 60
    valkey_url: str | None = None
    valkey_prefix: str = "mnemome:v1"
    recall_cache_ttl_s: int = 60

    @classmethod
    def from_environment(cls) -> Settings:
        environment = os.getenv("MNEMOME_ENV", "development").strip().lower()
        database_path = os.getenv("MNEMOME_DATABASE_PATH", "./data/mnemome.db")
        storage_backend = os.getenv("MNEMOME_STORAGE_BACKEND", "sqlite").strip().lower()
        if storage_backend not in {"sqlite", "postgres"}:
            raise ConfigurationError("MNEMOME_STORAGE_BACKEND must be sqlite or postgres")
        database_url = os.getenv("MNEMOME_DATABASE_URL", "").strip() or None
        if storage_backend == "postgres" and not database_url:
            raise ConfigurationError(
                "MNEMOME_DATABASE_URL is required when MNEMOME_STORAGE_BACKEND=postgres"
            )
        raw_keys = os.getenv("MNEMOME_API_KEYS", "")
        if not raw_keys:
            if environment == "production":
                raise ConfigurationError("MNEMOME_API_KEYS is required in production")
            raw_keys = json.dumps(
                {
                    "local-development-key": {
                        "tenant_id": "local",
                        "principal_id": "developer",
                        "roles": ["admin", "agent", "memory:read", "memory:write"],
                    }
                }
            )
        try:
            parsed = json.loads(raw_keys)
            api_keys = {
                key: ApiPrincipal(
                    tenant_id=value["tenant_id"],
                    principal_id=value["principal_id"],
                    roles=frozenset(value.get("roles", [])),
                )
                for key, value in parsed.items()
            }
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
            raise ConfigurationError("MNEMOME_API_KEYS must be a valid key mapping") from error
        if not api_keys:
            raise ConfigurationError("At least one API key must be configured")
        if environment == "production" and "local-development-key" in api_keys:
            raise ConfigurationError("The development API key is not allowed in production")
        return cls(
            environment=environment,
            database_path=database_path,
            api_keys=api_keys,
            log_level=os.getenv("MNEMOME_LOG_LEVEL", "INFO").upper(),
            storage_backend=storage_backend,
            database_url=database_url,
            db_pool_min_size=_positive_int("MNEMOME_DB_POOL_MIN_SIZE", 1),
            db_pool_max_size=_positive_int("MNEMOME_DB_POOL_MAX_SIZE", 10),
            db_command_timeout_s=_positive_float("MNEMOME_DB_COMMAND_TIMEOUT_S", 5.0),
            tenant_delegation_secret=(
                os.getenv("MNEMOME_TENANT_DELEGATION_SECRET", "").strip() or None
            ),
            tenant_delegation_max_skew_s=_positive_int(
                "MNEMOME_TENANT_DELEGATION_MAX_SKEW_S", 60
            ),
            valkey_url=os.getenv("MNEMOME_VALKEY_URL", "").strip() or None,
            valkey_prefix=(
                os.getenv("MNEMOME_VALKEY_PREFIX", "mnemome:v1").strip()
                or "mnemome:v1"
            ),
            recall_cache_ttl_s=_positive_int("MNEMOME_RECALL_CACHE_TTL_S", 60),
        )


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value < 1:
        raise ConfigurationError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a number") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")
    return value
