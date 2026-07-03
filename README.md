
This is the Home Edition of our Office/Enterprise software, which has most of the same features as our Office and Enterprise systems. These include:

- **Medications and supplements** — what you take and when.
- **Food logging** — keep track of what you eat.
- **Vitals and metrics** — blood pressure, blood sugar, weight, temperature, heart rate, sleep, and steps.
- **Clinical history** — conditions, allergies, family history, surgeries, the things a doctor always asks about.
- **Documents** — stash your medical paperwork; an on-board OCR pipeline reads it and makes it searchable.
- **Contacts** — a personal contact book for your providers.
- **Vaccinations** — a record of your shots.


Home Edition comes without the hassle of Postgres Row Level Security, Cloudflare Workers, HIPAA compliance measures, the healthcare Provider module, defensive rate limiting, and all the other stuff that requires a distributed systems engineering background to manage.

This is software you can simply load on any Docker capable system and start using to track your health or integrate your own coding project. There is also a companion phone application called [MinowaMobile](https://github.com/MinowaHealth/MinowaMobile).

If you are a vibe coder this repo contains the harness we use with Claude Code to produce quality software and limit token burn. See ClaudeHarness.md for details, and read [CONTRIBUTING.md](CONTRIBUTING.md) before pointing an LLM at the code — it marks the areas where a careless change can leak one family member's health data to another.

If you want to access this software on the go using MinowaMobile we strongly suggest [Tailscale](https://tailscale.com/) (the free Personal plan is plenty). The default install allows the three RFC1918 private IP ranges (192.168.0.0/16, 172.16.0.0/12, 10.0.0.0/8) and the CGNAT range 100.64.0.0/10 that Tailscale uses.

This appliance is **not hardened for the public internet** — do not port-forward it and do not publish it through a Cloudflare tunnel. The source-IP allowlist cannot protect a box exposed through a local tunnel or reverse proxy, because the tunnel connects from an address the allowlist permits. See [SECURITY.md](SECURITY.md) for the threat model.

We employ Cloudflare for our systems, but we are not in the business of providing free tech support for people who are blissfully unaware of internet security hazards. If you are already fluent with Cloudflare Access, that can work; the only clue we can offer is that Cloudflare tunnels use the same CGNAT space as Tailscale, so you have to pick one or the other.

## Support

The [MinowaHealth](https://www.reddit.com/r/MinowaHealth/) subreddit is the best place to start. Please keep general discussions there and leave the GitHub Issues area for problems WITH the code, as opposed to your use of the code.

## Claude Harness

We use LSP Enforcement Kit plus the Serena MCP server to keep Claude from constantly using poor quality shell tools like grep when there are better options. We employ CodeSight and OptiVault in complimentary roles, tracking the structure of the project. If you are just starting with Claude Code and haven't changed much, this setup will save roughly 90% of tokens burnt on development.

## Testing

We have extension CI (continuous integration), described in detail in TESTING.md, which covers everything from basic lint to full tilt live tests of the completed app and MCP server. If you make changes to the system, try to have Claude also update the testing methods.

## API & Data Model

The API and data model are two areas where we put energy into ensuring things are and REMAIN correct. While you're getting the minimized Home Edition, we intend to keep this in sync with the Enterprise software in this area, anticipating a sync/backup service later in 2026. A PR that removes existing API routes would likely be reviewed but not accepted. Proposed changes to database structures have to be mapped to a multi-tenant RLS protected structure.

If you are a vibe coder and most familiar with SQLite3 or a permissive MySQL environment, the thing to do is access Postgres using an account that has CRUD (create, read, update, delete) but which can not modify table structure. We further constrain LLM misbehavior by ensuring the MCP service uses a read only account. 

## Crystal Ball

If you're seeing a lot of possibilities with this software, please read CrystalBall.md, which exposes some of the systems we use in the Enterprise version, and what our overall direction is. There are changes we would welcome from community effort, and there are changes we could not accommodate for reasons such as scaling plans, security, or lack of resources.


## Architecture

One PostgreSQL database serving one household (~6 people) on one machine. No Row-Level Security — per-user privacy is enforced **in the application** with explicit `user_id` predicates on every query.


Three containers + one host process:

- **UserApp** (Flask, port 80) — the household API; in-process OCR.
- **UserMCP** (port 13282) — MCP server, a stateless proxy to UserApp's `/api/v1/*`.
- **PostgreSQL 18 + pgvector** (port 5432) — the single `healthv10` database.
- **Ollama** (host) — `nomic-embed-text-v2-moe` embeddings; best-effort, never blocks a write.

## Quick start

```bash
# Bring the whole stack up from the repo root
docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env up -d --build

# Create a household member (CLI is the only provisioning path)
cd UserApp && ./admin.py provision-user alice
```

The web UI binds to the LAN per `BIND_ADDR` in `local.env` (`127.0.0.1` local default, `0.0.0.0` for the appliance).

## Where to go next

| You want to…                                  | Read |
|-----------------------------------------------|------|
| Run the tests / set up a dev environment      | **[TESTING.md](TESTING.md)** |
| Understand the architecture & conventions     | [CLAUDE.md](CLAUDE.md) |
| Deploy the appliance                          | [HowToDeploy/](HowToDeploy/) |
| Use the REST API                              | [APIDocumentation/](APIDocumentation/) |
| Open Postgres to an external tool             | [PostgresAccess.md](PostgresAccess.md) |
| Contribute code or docs                       | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Report a security issue                       | [SECURITY.md](SECURITY.md) |
| See the Ponytail audit behind recent cleanups | [Ponytail/Ponytail-Consensus.md](Ponytail/Ponytail-Consensus.md) |

## Technology

Python 3.12 · Flask 3.1 · Gunicorn · PostgreSQL 18 + pgvector · psycopg 3 · Argon2id/PyOTP · Docker · Tesseract OCR.

## License

[BSD 3-Clause](LICENSE). Use it however you like; the Minowa name may not be used to promote derived products without prior arrangement.

