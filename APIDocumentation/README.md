# API Documentation

**Date**: 2026-06-13 14:20 UTC
**Version**: v10.9.0 Alpha
**App**: Minowa.ai Home Edition

## Overview

Minowa.ai Home Edition is a single-household appliance. It exposes one web application — the UserApp — served by a single Flask instance backed by the `healthv10` PostgreSQL database:

| API | App | Port | Audience | Endpoints |
|-----|-----|------|----------|-----------|
| **User API** | UserApp webapp | 80 | Web UI, mobile app, UserMCP | 140+ endpoints at `/api/v1/*` |

The User API uses JSON request/response bodies. There is no multi-tenant isolation: the appliance serves a single household, so data is scoped per user at the application layer via `user_id`. The `tenant_id` column remains on every table and is always `1`.

The same User API also backs **UserMCP**, a stateless MCP server that proxies a curated subset of `/api/v1/*` to MCP clients (e.g. Claude Desktop), authenticating per-request with a bearer token. It exposes no endpoints of its own — every request lands on the User API documented here. See [UserMCP/](UserMCP/) for the tool surface and bring-up.

## Files in This Folder

| File | Purpose |
|------|---------|
| [**UserAPI.md**](UserAPI.md) | Complete reference for `/api/v1/*` endpoints (health inputs, food, vitals, logging, analytics, integrations, documents, annotations, embedding sync, dietary settings, reminders, correlation report) |
| [**Authentication.md**](Authentication.md) | Auth patterns for the User API (sessions, bearer tokens, 2FA) |
| [**Conventions.md**](Conventions.md) | Error formats, timestamps, and common patterns |
| [**PaginationStandard.md**](PaginationStandard.md) | Pagination contract for list endpoints |
| [**DateFiltering-API.md**](DateFiltering-API.md) | Date-range filtering conventions |
| [**openapi.yaml**](openapi.yaml) | Machine-readable OpenAPI contract for the User API |
| [**HealthKit/**](HealthKit/) | Apple HealthKit import integration docs |
| [**UserMCP/**](UserMCP/) | UserMCP server (MCP proxy in front of the User API) docs |
| [**APIKeys/**](APIKeys/) | API key / bearer token management docs |

## Authoritative Source Code

| Concern | Source Files |
|-----|-------------|
| User API auth + 2FA + signup | [`UserApp/webapp/app.py`](../UserApp/webapp/app.py) |
| User API routes (incl. documents, in-process OCR) | [`UserApp/webapp/routes/*.py`](../UserApp/webapp/routes/) |
| Auth module | [`UserApp/webapp/auth.py`](../UserApp/webapp/auth.py) |
| DB connection management | [`UserApp/webapp/db_manager.py`](../UserApp/webapp/db_manager.py) |

## Consumers

| Consumer | API Used | Auth Method |
|----------|----------|-------------|
| Web UI (user portal) | User API | Session cookie |
| Mobile app (React Native) | User API | Bearer token (session ID) |
| UserMCP | User API | Bearer token |

## Quick Start

```bash
# User API login
curl -X POST http://minowa.local/api/v1/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "secret"}'

# Use the returned token
curl http://minowa.local/api/v1/health-inputs \
  -H "Authorization: Bearer <token>"
```
