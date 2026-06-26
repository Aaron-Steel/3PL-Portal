# Macgear 3PL Portal

Customer visibility portal + weekly billing automation for Macgear's 3PL service
(receive / store / dispatch stock Macgear doesn't own, charge handling & storage fees).
NetSuite-backed, multi-tenant. First customer: **Mova**. Reference: **Skriva**.

See `CLAUDE.md` for the business brief, `docs/data_model.md` for the schema/sync design,
and `docs/netsuite_validation.md` for the **validated** SuiteQL the sync layer uses.

## Run locally
```powershell
./run.ps1
```
Opens http://127.0.0.1:8000. First run creates a SQLite DB and seeds the two customers,
Mova's rate card, a month of demo cache data so all views render, and the dev logins below.
Auth is always on — sign in as `admin@macgeargroup.com` / `admin123` (CHANGE for any real use).

## Layout
```
app/
  main.py        FastAPI app: auth, customer switcher, 6 views, billing run, admin console,
                 token-authed /admin/ingest + /admin/billing/* (n8n)
  models.py      ORM (mirrors db/01_schema.sql) — the read cache + billing tables
  service.py     read-side: cache -> the 6 portal views + overview tiles
  billing.py     billing engine: cache + rate card -> the 5 weekly service charges
  netsuite.py    ingest layer: upserts rows n8n pushes in (the app never calls NetSuite)
  perms.py       roles + per-user view permissions
  security.py    pbkdf2 password hashing + signed-cookie sessions
  seed.py        seeds Mova/Skriva + rate cards + users (+ demo cache unless SEED_DEMO=0)
  templates/ static/   server-rendered portal UI
db/01_schema.sql  canonical Postgres DDL (v1, validated columns)
netsuite/      3pl_restlet.js (deployed in NetSuite) + n8n_3pl_sync.js (n8n Code node)
```

## NetSuite integration (app never calls NetSuite)
All NetSuite comms are server-to-server: **n8n signs Token-Based Auth → a RESTlet** runs the
validated SuiteQL / creates draft invoices. The droplet app holds no NetSuite credentials and
runs no AI/MCP. See `docs/netsuite_integration.md` for the architecture and `docs/deploy.md` for
the sandbox-first deploy walkthrough.

## Deploy
Docker on the n8n droplet behind Caddy, `DATABASE_URL` -> Postgres. `docker compose up -d --build`
(see `docker-compose.yml`, `.env.example`, `docs/deploy.md`). A scheduled n8n Code node POSTs the
synced rows to `/admin/ingest` and drains the billing-push queue.

## Config (env)
| var | purpose |
|---|---|
| `DATABASE_URL` | Postgres URL on the droplet (default: local SQLite) |
| `APP_SECRET` | session-cookie signing key (set a long random one) |
| `SYNC_TOKEN` | shared secret for `/admin/ingest` + `/admin/billing/*` (must match the n8n node) |
| `SEED_DEMO` | `0` on a real deploy (no fake cache rows); `1`/unset plants demo data locally |
| `PGPASSWORD` | bundled-Postgres password (compose); keep in sync with `DATABASE_URL` |
| `SHARED_NETWORK` | name of the Docker network shared with Caddy + n8n |

The app needs **no** NetSuite credentials — TBA creds live only in the n8n Code node.
