from __future__ import annotations

import sqlite3

import httpx
import pytest

from mnemome.service.app import create_app
from mnemome.service.settings import Settings


@pytest.mark.asyncio
async def test_playground_account_shares_memory_across_clients(tmp_path) -> None:
    database_path = tmp_path / "mnemome.db"
    settings = Settings(
        environment="test",
        database_path=str(database_path),
        api_keys={},
        log_level="WARNING",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://first.test") as first:
            assert (await first.get("/demo/api/status")).status_code == 401
            registered = await first.post(
                "/demo/api/auth/register",
                json={"username": "memory.owner", "password": "correct-horse"},
            )
            assert registered.status_code == 201
            assert registered.json()["user"]["username"] == "memory.owner"
            assert first.cookies.get("mnemome_login_session")

            created = await first.post(
                "/demo/api/memories",
                json={"kind": "preference", "content": "표로 답해 주세요.", "tags": []},
            )
            assert created.status_code == 201

            logged_out = await first.post("/demo/api/auth/logout")
            assert logged_out.status_code == 204
            assert (await first.get("/demo/api/memories")).status_code == 401

        async with httpx.AsyncClient(transport=transport, base_url="https://second.test") as second:
            invalid = await second.post(
                "/demo/api/auth/login",
                json={"username": "memory.owner", "password": "wrong-password"},
            )
            assert invalid.status_code == 401

            logged_in = await second.post(
                "/demo/api/auth/login",
                json={"username": "MEMORY.OWNER", "password": "correct-horse"},
            )
            assert logged_in.status_code == 200
            memories = (await second.get("/demo/api/memories")).json()["items"]
            assert any(item["content"] == "표로 답해 주세요." for item in memories)

            duplicate = await second.post(
                "/demo/api/auth/register",
                json={"username": "Memory.Owner", "password": "another-password"},
            )
            assert duplicate.status_code == 409

        async with httpx.AsyncClient(transport=transport, base_url="https://third.test") as third:
            registered = await third.post(
                "/demo/api/auth/register",
                json={"username": "other-user", "password": "correct-horse"},
            )
            assert registered.status_code == 201
            memories = (await third.get("/demo/api/memories")).json()["items"]
            assert all(item["content"] != "표로 답해 주세요." for item in memories)

    restarted_app = create_app(settings)
    async with restarted_app.router.lifespan_context(restarted_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted_app),
            base_url="https://after-restart.test",
        ) as after_restart:
            logged_in = await after_restart.post(
                "/demo/api/auth/login",
                json={"username": "memory.owner", "password": "correct-horse"},
            )
            assert logged_in.status_code == 200
            memories = (await after_restart.get("/demo/api/memories")).json()["items"]
            assert any(item["content"] == "표로 답해 주세요." for item in memories)

    with sqlite3.connect(database_path) as connection:
        stored = connection.execute(
            "SELECT password_hash FROM demo_users WHERE normalized_username=?",
            ("memory.owner",),
        ).fetchone()[0]
    assert stored.startswith("pbkdf2_sha256$")
    assert "correct-horse" not in stored
