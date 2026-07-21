# Deployment

This repository ships a small-server SaaS/on-prem reference profile. It runs the
same `mnemome` Core package used by embedded applications and persists state in
SQLite. It is suitable for a single API replica, pilots, and development.

## Requirements

- Docker Engine with Compose v2
- A clone of this Git repository
- A high-entropy bearer key

## Install

```bash
git clone <repository-url> mnemome
cd mnemome
cp .env.example .env
# Replace MNEMOME_API_KEYS and set MNEMOME_ENV=production.
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8080/ready
```

The service binds to loopback by default. Put an HTTPS reverse proxy in front of
it, or explicitly set `MNEMOME_BIND_ADDRESS` when access from another host is
intended. Never publish the development API key.

## Lotte Agent demo integration

The root demo page can use the internal Lotte Agent runtime through
`MnemomeLongTermMemory`. Because the upstream library is not publicly
redistributed, an authorized build places `lotte_agent-*.whl` under `vendor/`
and sets `MNEMOME_REQUIRE_LOTTE_AGENT=1`. The wheel is installed into the same
service image but remains outside the public Git repository.

The public demo uses a deterministic model adapter. It exercises Lotte Agent's
real planning/run lifecycle and long-term-memory protocol without exposing a
provider API key or creating unbounded model cost.

## Upgrade and rollback

```bash
git fetch origin
git pull --ff-only
docker compose up -d --build
```

Before upgrading, back up the named volume. Git rollback and volume restoration
are separate operations because schema/data and application revisions have
different lifecycles.

## Current production boundary

The SQLite adapter serializes writes and provides tenant-scoped durable storage,
but does not provide horizontal write scaling, PostgreSQL RLS, distributed
leases, OIDC, a worker fleet, billing, or HA. Those remain Phase 3–7 work in
`ssd/16-implementation-roadmap.md`. The service shell already keeps tenant
identity out of caller-controlled tenant headers and the Core remains transport
independent, so a PostgreSQL/OIDC adapter can replace this profile without
forking domain lifecycle rules.
