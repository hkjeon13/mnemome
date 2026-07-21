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

    @classmethod
    def from_environment(cls) -> Settings:
        environment = os.getenv("MNEMOME_ENV", "development").strip().lower()
        database_path = os.getenv("MNEMOME_DATABASE_PATH", "./data/mnemome.db")
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
        )
