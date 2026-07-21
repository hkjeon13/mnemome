from __future__ import annotations

import secrets
import time


class SortableIdGenerator:
    def new(self, prefix: str) -> str:
        timestamp = int(time.time() * 1000)
        return f"{prefix}_{timestamp:012x}{secrets.token_hex(8)}"
