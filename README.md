# Agent CRM

A local, agent-driven CRM for the ranch creative/tech brands **MidnightSatin**, **Celestial-Nexus**, and **HeyBuddy**. It captures leads, runs outbound research and hunting, extracts contact profiles from scraped pages, and tracks pipeline state — all on your own hardware.

**There is no outreach or sending in this stack.** The lead verifier checks DNS, MX, and HTTP only. MX means the domain can receive mail, not that a specific local-part inbox exists.

---

## Stack and how to run

### Docker Compose (Mini / ranch target)

```bash
cp .env.example .env   # adjust if needed
docker compose up -d --build
```

| Service | Port | Role |
|---------|------|------|
| `api` | 8000 | FastAPI — runs `alembic upgrade head` on boot, then serves |
| `dashboard` | 8501 | Streamlit observer + pipeline/hunter/research/contacts/verifier |
| `db` | 5432 | Postgres 16 |
| `spark-queue` | 8088 | GPU-aware LLM queue proxy |

API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

### Host services (not in Compose)

SearXNG and Firecrawl run on the ranch host. Containers reach them via `host.docker.internal`:

| Variable | Default | Service |
|----------|---------|---------|
| `CRM_SEARXNG_URL` | `http://host.docker.internal:8080` | JSON search |
| `CRM_FIRECRAWL_URL` | `http://host.docker.internal:3002` | Page scrape (markdown) |

### Spark LLM

All CRM agents call the LLM through **spark-queue only**:

```
CRM_LLM_BASE_URL=http://spark-queue:8088/v1
```

- Model: `qwen3.8-27b-sglang` (configured on the `spark-queue` container)
- Global cap: **4 concurrent Spark sessions** (shared with Hermes; leaves GPU headroom for ComfyUI)
- **Never point agents at Spark SGLang directly**

The dashboard **Live agents** tab shows spark-queue occupancy alongside agent heartbeats.

### Database and migrations

| Backend | Schema path |
|---------|-------------|
| **Postgres** (Compose / NAS) | `alembic upgrade head` — API entrypoint runs this on boot |
| **SQLite** (local dev/tests) | `init_db()` / `create_all` via `agent-crm init-db` |

Set `CRM_DATABASE_URL` in `.env`. On Postgres, do **not** rely on `create_all`; Alembic is the source of truth.

**Enum convention:** legacy tables (`leads`, `opportunities`, …) use name-bound Postgres enums (`HUNTER`, `MIDNIGHTSATIN`). Newer tables (`hunt_queries`, `contact_verifications`, `contact_profiles`, …) use lowercase values via `str_enum()` in `models.py`. Reuse the existing `brand` enum with `create_type=False`.

### Brands

| Slug | Use |
|------|-----|
| `midnightsatin` | MidnightSatin routing and hunts |
| `celestial-nexus` | Celestial-Nexus routing and hunts |
| `heybuddy` | HeyBuddy routing; research defaults to nonprofit partnership hunts |

---

## Collection systems

The ranch stack builds prospect intelligence through six layered systems. Each writes to Postgres (or SQLite in dev).

### 1. SearXNG search

Paginated JSON search against the local SearXNG instance. Collects unique URLs until the configured limit (default **50 hits** per query). Used by hunter, hunt-loop, research, and contact social lookup.

### 2. Firecrawl scrape

Each search hit can be scraped to markdown via the host Firecrawl API. Hunter defaults to **50 pages per query** (`CRM_HUNTER_MAX_PAGES_PER_RUN`).

### 3. Outbound Hunter and hunt loop

**Single hunt** (`outbound_hunter`): one query → SearXNG → scrape → create page-level leads + enrichment summary.

**Hunt loop** (`hunt_loop`): bounded branching collection of **sites** into `hunt_resources` (not people). Features:

- FIFO query queue (`hunt_queries`) with dedupe
- SearXNG param rotation (general, social media, news, IT, …)
- LLM branch-term extraction to enqueue new queries
- **Community/person feedback**: newly catalogued communities and extracted contact names enqueue deterministic follow-up queries (`origin` prefix `community:` / `person:`)
- Defaults: **40 queries**, **unlimited wall clock**, **50 pages per query** (still capped by community/person term limits per run)

| Entry | Command / API |
|-------|---------------|
| Single hunt | `agent-crm hunt "<query>"` · `POST /hunt` |
| Branching loop | `agent-crm hunt-loop [--brand …] [query]` · `POST /hunt/loop` |
| List sites | `GET /hunt/resources` |
| Queue status | `GET /hunt/queue` |

Hunt-loop prompt explicitly forbids inventing emails or person names. Sites land in `hunt_resources`; people are handled by contact extraction (below).

**Community & name feedback.** When the loop first sees a community/forum URL (e.g. `reddit.com/r/<sub>`, Discord invite, Facebook group), it enqueues bounded `community:` search terms (`site:reddit.com/r/<sub>`, `"<sub>" community`, etc.). After contact extraction on a scraped page, real person names (not emails, not single tokens like “Admin”) enqueue `person:` terms (`"Jane Doe" reddit`, `"Jane Doe" discord`, …). Caps per run: `CRM_HUNTER_COMMUNITY_TERMS_PER_RUN` (default **30**) and `CRM_HUNTER_PERSON_TERMS_PER_RUN` (default **20**). Dedupe uses `hunt_queries.dedupe_key` as usual. The dashboard **Hunter** tab lists catalogued communities and derived queued terms; `GET /hunt/queue` reports aggregate pending counts (inspect `hunt_queries.origin` for `community:` / `person:` prefixes).

### 4. Research agent

Competitor and nonprofit prospecting with the same SearXNG + Firecrawl + Spark summarization pipeline.

| Brand | Default kind | Focus |
|-------|--------------|-------|
| `celestial-nexus`, `midnightsatin` | `competitor` | Competitor site scans |
| `heybuddy` | `nonprofit` | 501(c)(3) partnership / grant prospects (HeyBuddy itself is **not** a nonprofit) |

Run-wide defaults: **20 queries**, **200 pages scraped**, **60 minutes**, **50 SERP hits** per query. Output persists in `research_findings`.

| Entry | Command / API |
|-------|---------------|
| Run | `agent-crm research --brand celestial-nexus [--kind nonprofit] [query]` · `POST /research` |
| List | `GET /research/findings` |

### 5. Contact extraction and social lookup

After every successful Firecrawl scrape (hunter, hunt-loop, research), the stack extracts contacts from page markdown/HTML:

- **Emails** via regex and `mailto:` links; skip noreply, no-reply, donotreply, privacy, mailer-daemon, notifications@, example.com, sentry.io, wixpress, cloudflare, githubnoreply
- **Names** only when clearly present (`Name <email>`, prior line, mailto anchor text) — **never invented**
- **Social URLs** already on the page (x.com, linkedin.com/in, instagram.com, facebook.com)

Each email upserts a row in **`contact_profiles`** (unique lowercase email) and a matching **`Lead`** (`source=CONTACT`). `source_urls` and `socials` merge across pages.

When a profile still has no socials after scrape, a **bounded SearXNG lookup** runs (X / LinkedIn / Instagram) — no paid APIs, no login:

| Variable | Default | Meaning |
|----------|---------|---------|
| `CRM_CONTACT_SOCIAL_QUERIES_PER_PROFILE` | 4 | Max SearXNG queries per profile lookup |
| `CRM_CONTACT_SOCIAL_LOOKUPS_PER_RUN` | 40 | Max profiles looked up per hunt/research run |

Skipped when the scrape already attached socials to that contact.

| Entry | Command / API |
|-------|---------------|
| List profiles | `agent-crm contacts list [--brand …] [--email …]` · `GET /contacts` |

### 6. Lead verifier

Defensive contact checks — **DNS, MX, HTTP only**. No SMTP, no RCPT/VRFY, no sending.

| Entry | Command / API |
|-------|---------------|
| One lead | `agent-crm verify --lead-id N` · `POST /leads/{id}/verify` |
| Batch | `agent-crm verify --unverified [--limit 50]` · `POST /verify/batch` |
| Raw | `agent-crm verify --email a@b.com` · `POST /verify/raw` |

Results in `contact_verifications` (one row per lead + contact).

---

## Data model (implemented tables)

| Table | Purpose |
|-------|---------|
| `leads` | People at top of funnel (intake, hunter pages, contact emails) |
| `accounts` | Company/project home; `socials` JSON for **company** profiles |
| `opportunities` | Pipeline stage per lead |
| `activities` | Append-only agent history |
| `journeys` | Nurture state machines (schema present; sending not implemented) |
| `hunt_queries` | Hunt-loop search queue |
| `hunt_resources` | Discovered **sites** (directories, communities, newsletters) |
| `research_findings` | Research agent output |
| `contact_profiles` | **People** keyed by email — name, socials, source pages |
| `contact_verifications` | Verifier results per lead contact |
| `agent_heartbeats` | Live agent observer state |

`Account.socials` is for companies. Person socials live on `contact_profiles` (and in lead `raw_payload`), not on accounts.

---

## Agents and dashboard

Agents call `CRMToolkit(actor="…")` for typed writes. Every mutation appends an `Activity`. Stage changes go through `PipelineManager`.

Post heartbeats via `POST /agents/{agent_name}/heartbeat`. The dashboard polls `GET /agents` and `GET /agents/spark`.

### Dashboard tabs

| Tab | Shows |
|-----|-------|
| **Live agents** | Heartbeats + spark-queue slot occupancy (auto-refresh) |
| **Pipeline & leads** | Weekly metrics, stage chart, lead table, activity history, verifications |
| **Hunter** | Query queue status + `hunt_resources` table |
| **Research** | `research_findings` with brand/kind filters |
| **Contacts** | `contact_profiles` — name, email, socials, source pages |
| **Verifier** | Hunter leads and verification status |

### Agent roster (as implemented)

| Agent | Actor name | What it does today |
|-------|------------|-------------------|
| Lead Intake | `lead_intake` | `POST /intake/webhook` → create lead |
| Lead Scoring | `lead_scoring` | Score + priority via tooling |
| Brand Router | `brand_router` | Assign brand |
| Research | `research` | Competitor / nonprofit runs → `research_findings` |
| Outbound Hunter | `outbound_hunter` | Hunt + hunt-loop → sites and page leads |
| Lead Verifier | `lead_verifier` | DNS/MX/HTTP checks |
| CRM / Pipeline | `api`, `dashboard`, … | Stage transitions, reporting |

Outreach, nurture sends, and orchestrator scheduling are **not** implemented.

---

## CLI cheatsheet

```bash
# Stack
agent-crm serve                          # API on CRM_API_PORT (default 8000)
agent-crm init-db                        # SQLite only — Postgres uses Alembic
agent-crm seed                           # Demo leads
agent-crm report                         # Weekly JSON report

# Hunter
agent-crm hunt "boutique design NYC" \
  [--brand midnightsatin|celestial-nexus|heybuddy] \
  [--max-pages 50] [--search-limit 50] \
  [--no-prospect] [--no-summarize]

agent-crm hunt-loop [query] \
  [--brand midnightsatin|celestial-nexus|heybuddy] \
  [--max-queries 40] [--max-minutes 0] [--max-pages-per-query 50] \
  [--no-resume] [--no-summarize]

# Research
agent-crm research --brand heybuddy [--kind nonprofit|competitor|other] [query] \
  [--max-queries 20] [--max-pages 200] [--max-minutes 60] \
  [--search-limit 50] [--no-summarize] [--no-accounts]

# Contacts
agent-crm contacts list \
  [--brand midnightsatin|celestial-nexus|heybuddy|unassigned] \
  [--email user@example.com] [--limit 500]

# Verifier (no mail sent)
agent-crm verify --lead-id 42
agent-crm verify --unverified [--limit 50]
agent-crm verify --email a@b.com
agent-crm verify --url https://example.com
```

Dashboard (outside Compose):

```bash
streamlit run src/agent_crm/dashboard.py
```

---

## HTTP API (summary)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + database kind |
| POST | `/intake/webhook` | Create lead |
| POST | `/hunt` | Single hunter run |
| POST | `/hunt/loop` | Branching hunt loop |
| GET | `/hunt/resources` | List `hunt_resources` |
| GET | `/hunt/queue` | Hunt queue status |
| POST | `/research` | Research run |
| GET | `/research/findings` | List findings |
| GET | `/contacts` | List `contact_profiles` (`?brand=`, `?email=`) |
| POST | `/leads/{id}/verify` | Verify lead contacts |
| GET | `/leads/{id}/verifications` | List verifications |
| POST | `/verify/batch` | Batch verify unverified leads |
| POST | `/verify/raw` | Verify raw email or URL |
| GET | `/leads`, `/leads/{id}`, `/leads/{id}/activities` | Lead reads |
| POST | `/leads/{id}/stage` | Pipeline stage transition |
| GET | `/report/weekly` | Weekly snapshot |
| POST | `/agents/{name}/heartbeat` | Agent heartbeat |
| GET | `/agents` | Observer roster |
| GET | `/agents/spark` | Spark queue slot summary |

Full OpenAPI at `/docs`.

---

## Environment variables

Copy `.env.example` to `.env`. Key settings:

| Variable | Purpose |
|----------|---------|
| `CRM_DATABASE_URL` | `postgresql+psycopg://…` or `sqlite:///./data/agent_crm.db` |
| `CRM_SEARXNG_URL` | Ranch SearXNG base URL |
| `CRM_FIRECRAWL_URL` | Ranch Firecrawl base URL |
| `CRM_LLM_BASE_URL` | Spark queue OpenAI-compatible endpoint (`http://spark-queue:8088/v1`) |
| `CRM_HUNTER_MAX_PAGES_PER_RUN` | Max Firecrawl pages per hunt query (50) |
| `CRM_HUNTER_SEARCH_RESULT_LIMIT` | Max SearXNG hits per query (50) |
| `CRM_HUNTER_MAX_QUERIES_DEFAULT` | Hunt-loop query budget (40) |
| `CRM_HUNTER_MAX_MINUTES_DEFAULT` | Hunt-loop wall clock in minutes (`0` = unlimited) |
| `CRM_HUNTER_COMMUNITY_TERMS_PER_RUN` | Max community feedback queries per loop run (30) |
| `CRM_HUNTER_PERSON_TERMS_PER_RUN` | Max person-name feedback queries per loop run (20) |
| `CRM_RESEARCH_MAX_QUERIES_DEFAULT` | Research query budget (20) |
| `CRM_RESEARCH_MAX_PAGES_PER_RUN` | Research scrape budget (200) |
| `CRM_RESEARCH_MAX_MINUTES_DEFAULT` | Research wall clock (60) |
| `CRM_RESEARCH_SEARCH_RESULT_LIMIT` | Research SERP hits per query (50) |
| `CRM_CONTACT_SOCIAL_QUERIES_PER_PROFILE` | SearXNG queries per social lookup (4) |
| `CRM_CONTACT_SOCIAL_LOOKUPS_PER_RUN` | Profiles looked up per run (40) |
| `CRM_API_BASE_URL` | Dashboard → API for live agent panel |
| `CRM_HOT_LEAD_THRESHOLD` | Score threshold for hot-lead flag (80) |

Spark queue container vars (`SPARK_LLM_*`) are documented in `.env.example`.

---

## Local dev (SQLite)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"

export CRM_DATABASE_URL="sqlite:///./data/agent_crm.db"
alembic upgrade head
agent-crm seed
agent-crm serve
```

Point `CRM_SEARXNG_URL` and `CRM_FIRECRAWL_URL` at your host services. For LLM summarization, run spark-queue locally or set `CRM_LLM_BASE_URL` to a reachable queue instance.

---

## Migrations

```bash
alembic upgrade head      # apply all revisions
alembic current           # show head
```

Current chain ends at `c5d6e7f8a9b0` (`contact_profiles` + `CONTACT` lead source). Do not use `create_all` on Postgres — the API entrypoint and `docker compose` api service run Alembic automatically.
