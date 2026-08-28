# Agent CRM

A local, agent-driven CRM for the ranch creative/tech brands **Tactic-Studio**, **MidnightSatin**, **Celestial-Nexus**, and **HeyBuddy**. It captures leads, runs outbound research and hunting, extracts contact profiles from scraped pages, and tracks pipeline state — all on your own hardware.

**There is no outreach or sending in this stack.** The lead verifier checks DNS, MX, and HTTP only. tactic.studio outbound (direct mail, DMs) remains gated by Pete (`pete@tactic.studio`) and naming-rights — this repo collects and categorizes only.

---

## Stack and how to run

### Docker Compose (Mini / ranch target)

```bash
cp .env.example .env   # adjust if needed
docker compose up -d --build
```

`docker compose up -d` starts the full self-organizing pipeline — no extra CLI required:

| Service | Port | Role |
|---------|------|------|
| `api` | 8000 | FastAPI — runs `alembic upgrade head` on boot, then serves |
| `dashboard` | 8501 | Streamlit observer + pipeline/hunter/research/contacts/verifier |
| `db` | 5432 | Postgres 16 |
| `spark-queue` | 8088 | GPU-aware LLM queue proxy |
| `contact-worker` | — | Job dispatcher — enrich + verify (`agent-crm jobs`) |
| `hunt-loop` | — | Standing outbound hunter (`agent-crm hunt-loop`, global priority queue) |
| `research-loop` | — | Standing research queue drain (20 queries / 60 min / 200 pages per cycle; queue only grows) |
| `engagement-loop` | — | Standing forum rescan + comment drafts (never posts) |
| `orchestrator` | — | Self-learning stack inspector (`agent-crm orchestrate`) — writes improvement notes |

API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

The `contact-worker` enqueues and runs `verify_lead` jobs automatically when contact profiles are upserted with an email, and backfills unverified leads when idle. Non-Spark verify jobs drain even when Spark enrich jobs are stuck on 500s. You do **not** need to run `agent-crm verify` by hand for the pipeline to work — that CLI remains for one-off debugging.

The `orchestrator` inspects heartbeats, job failures, Spark health, verification coverage, and worker errors every few minutes. It writes deduped rows to `agent_improvement_notes` (gaps, performance issues, repairs). Manager/Cursor pulls open notes via `GET /improvement-notes?status=open`, investigates, patches the container, and rebuilds — no outbound mail, no Spark unless you choose to summarize notes later.

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

The dashboard **Live agents** tab shows spark-queue occupancy alongside agent heartbeats, a compact hunt-loop phase strip, and per-agent in/out token counts with an hourly average rate and estimated cloud-cost avoided (default **$2.00 / million input** and **$10.00 / million output**). Token totals persist in the CRM database. Live panels cache Spark/API/DB snapshots and auto-refresh every **10 minutes** (use **Refresh now** for an immediate pull). The **Hunter** tab shows live drain status (current query, phase, queue breakdown, Pete's list progress, recently completed queries).

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
| `tactic-studio` | tactic.studio AR/XR/VR vendor BD (collection only; Pete + naming-rights gate outbound) |

tactic.studio contacts are tagged with an **audience** bucket on `contact_profiles` and matching `leads`: `marketing` (brand/industrial bid list), `influencer` (XR creators), `user` (community members who should see tactic work). Other brands leave audience unset.

### Marketing skill

Vendored MIT **marketing-agi** skill at [`skills/marketing-agi/`](skills/marketing-agi/) (router: `SKILL.md`; modules in `references/`). Upstream: [holy-templar/marketing-agi](https://github.com/holy-templar/marketing-agi) — see `skills/marketing-agi/SOURCE`.

**Brand context** for Research, Hunter, and dashboard agents:

| File | Brand |
|------|-------|
| `brand-context.md` | Ranch-wide constraints |
| `brand-context.midnightsatin.md` | MidnightSatin |
| `brand-context.celestial-nexus.md` | Celestial-Nexus |
| `brand-context.heybuddy.md` | HeyBuddy (not a nonprofit — hunts partner orgs) |
| `brand-context.tactic-studio.md` | tactic.studio |

Research competitor summaries load bounded slices of `references/competitive.md` (+ positioning) into the Spark summarizer. Ad-placement runs pull short `paid-ads` / `hooks` excerpts for discovery briefs. Shared rules: never invent proof (`[NEED: x]`), no live ad accounts, no outbound send. **tactic.studio outbound remains gated** by Pete (`pete@tactic.studio`) + naming-rights — this stack never sends.

---

## Collection systems

The ranch stack builds prospect intelligence through seven layered systems. Each writes to Postgres (or SQLite in dev).

### 1. SearXNG search

Paginated JSON search against the local SearXNG instance. Collects unique URLs until the configured limit (default **50 hits** per query). Used by hunter, hunt-loop, research, and contact social lookup.

### 2. Firecrawl scrape

Each search hit can be scraped to markdown via the host Firecrawl API. Hunter defaults to **50 pages per query** (`CRM_HUNTER_MAX_PAGES_PER_RUN`).

### 3. Outbound Hunter and hunt loop

**Single hunt** (`outbound_hunter`): one query → SearXNG → scrape → create page-level leads + enrichment summary.

**Hunt loop** (`hunt_loop`): bounded branching collection of **sites** into `hunt_resources` (not people). Features:

- **Priority queue** (`hunt_queries.priority`, higher number runs first) with composite index `(status, priority, id)`. tactic.studio marketing/influencer/user seeds dequeue ahead of older MidnightSatin rows. Unlisted origins (generic seeds, branch terms) use priority **30** and still run, but never block tactic marketing.
- SearXNG param rotation (general, social media, news, IT, …)
- LLM branch-term extraction to enqueue new queries
- **Community/person feedback**: newly catalogued communities and extracted contact names enqueue deterministic follow-up queries (`origin` prefix `marketing:` / `influencer:` / `user:` plus `community:` / `person:` when applicable)
- Defaults: **unlimited queries**, **unlimited wall clock**, **50 pages per query** (still capped by community/person term limits per run)
- Per-brand **seed packs** include AI-generated-content readers and promoters (communities, BookTok/TikTok creators, influencers) in addition to generic discovery terms; tactic.studio packs are split by audience intent

| Entry | Command / API |
|-------|---------------|
| Single hunt | `agent-crm hunt "<query>"` · `POST /hunt` |
| Global loop | `agent-crm hunt-loop` (no `--brand`) · `POST /hunt/loop` with `brand=unassigned` |
| Brand loop | `agent-crm hunt-loop --brand tactic-studio` · drains that brand by priority |
| List sites | `GET /hunt/resources` |
| Queue status | `GET /hunt/queue` |

**Ranch ops:** Compose runs **one** global `hunt-loop` service (no `--brand`) so tactic.studio marketing seeds jump ahead of pending MidnightSatin work. Do **not** run a second MidnightSatin-only loop alongside it. To seed tactic.studio once after deploy, either wait for pending `hunt_queries` rows or temporarily run:

```bash
docker compose run -d --rm --no-deps api agent-crm hunt-loop --brand tactic-studio
```

then rely on the standing `hunt-loop` service for ongoing collection. Spark LLM still routes through spark-queue (4 concurrent cap).

**Dequeue priority (highest first):** tactic `marketing` → tactic `influencer` → tactic `user` → MidnightSatin influencer/user → Celestial-Nexus influencer/user → HeyBuddy influencer/user → unlisted (30).

Hunt-loop prompt explicitly forbids inventing emails or person names. Sites land in `hunt_resources`; people are handled by contact extraction (below).

**Community & name feedback.** When the loop first sees a community/forum URL (e.g. `reddit.com/r/<sub>`, Discord invite, Facebook group), it enqueues bounded `community:` search terms (`site:reddit.com/r/<sub>`, `"<sub>" community`, etc.). Newly catalogued forums also enqueue `engagement:` terms aimed at popular posts (`site:reddit.com/r/<sub> hot`, `top this week`). After contact extraction on a scraped page, real person names (not emails, not single tokens like “Admin”) enqueue `person:` terms (`"Jane Doe" reddit`, `"Jane Doe" discord`, …). Comment authors with handles enqueue `handle:` terms (`site:reddit.com/u/<user>`, `u/<user> reddit`, …). Caps per run: `CRM_HUNTER_COMMUNITY_TERMS_PER_RUN` (default **30**), `CRM_HUNTER_PERSON_TERMS_PER_RUN` (default **20**), `CRM_HUNTER_HANDLE_TERMS_PER_RUN` (default **20**), and `CRM_HUNTER_ENGAGEMENT_TERMS_PER_RUN` (default **20**). Dedupe uses `hunt_queries.dedupe_key` as usual. The dashboard **Hunter** tab lists catalogued communities and derived queued terms; `GET /hunt/queue` reports aggregate pending counts (inspect `hunt_queries.origin` for `community:` / `person:` / `handle:` / `engagement:` prefixes). Forums, communities, and social networks also get an `engagement_score` from member/comment/hot-traffic hints so the engagement agent can prioritize high-traffic venues.

### 4. Research agent

Competitor, nonprofit, and **ad-placement** prospecting with the same SearXNG + Firecrawl + Spark summarization pipeline.

| Brand | Default kind | Focus |
|-------|--------------|-------|
| `celestial-nexus`, `midnightsatin`, `tactic-studio` | `competitor` | Competitor / landscape scans (XR studios for tactic.studio) |
| `heybuddy` | `nonprofit` | 501(c)(3) partnership / grant prospects (HeyBuddy itself is **not** a nonprofit) |
| any (explicit `--kind ad_placement`) | `ad_placement` | Sites that sell ads, sponsorships, or promo/sticky/banner/board placement — discovery only |

Pass `--kind ad_placement` (or `"kind": "ad_placement"` on `POST /research`) to hunt newsletters, forums (including offbeat/imageboard surfaces like 4chan boards), Discords, podcasts, subreddits, zines, and trade pubs where each brand’s audience actually hangs out. Summaries capture `ad_product`, `how_to_buy`, `brand_fit`, and `brand_safety` — no ad buying or account creation. Forum and community hits are also catalogued as hunter venues (`engagement_surface`) so the engagement agent can come back and scan popular threads.

Run-wide defaults: **20 queries**, **200 pages scraped**, **60 minutes**, **50 SERP hits** per query. Output persists in `research_findings`. Search terms persist in **`research_queries`** (append-only: rows are never deleted). Seed packs include AI-generated-content audiences and promoters alongside competitor/nonprofit/ad-placement discovery terms. tactic.studio competitor seeds cover **industrial visualization AR experiences** and **industrial training aids**. Celestial-Nexus competitor seeds cover multiple **divination types** (tarot, runes, I Ching, pendulum, scrying, palmistry, numerology, oracle/Lenormand, tasseography, cartomancy, horary, geomancy, aura, dream interpretation).

After each SearXNG search + Firecrawl scrape, the agent extracts new search terms from hit titles/snippets and page text (heuristic + Spark) and **enqueues** them for every brand — MidnightSatin, Celestial-Nexus, HeyBuddy, and tactic.studio. Completing a query only flips status; the table only grows.

| Entry | Command / API |
|-------|---------------|
| Run | `agent-crm research --brand celestial-nexus [--kind ad_placement\|nonprofit\|competitor] [query]` · `POST /research` |
| Loop | `agent-crm research-loop [--max-queries 0] [--max-pages 0] [--max-minutes 0]` — seeds all four brands (competitor/nonprofit + ad-placement) then drains the growing queue (`0` = unlimited) |
| List | `GET /research/findings` |

### 5. Agent engagement (comment drafts)

The comment-reply arm of ad-placement. The hunter (and ad-placement research) catalogs high-traffic forums, communities, and social networks. The engagement agent later rescans those venues for popular threads, stores them in `engagement_threads`, and drafts product-related replies in `engagement_drafts`.

**This stack never posts.** Drafts are for human review only.

| Entry | Command / API |
|-------|---------------|
| Loop | `agent-crm engagement-loop [--brand …] [--max-venues 10] [--max-pages-per-venue 15] [--max-minutes 45]` · `POST /engagement/loop` |
| Threads | `GET /engagement/threads` · dashboard **Engagement** tab |
| Drafts | `GET /engagement/drafts` |

Compose runs `engagement-loop` as a standing worker. It picks catalogued venues due for rescan (ordered by `engagement_score`), searches for hot/top threads, scrapes them, and asks Spark for a draft when popularity is high enough (`CRM_ENGAGEMENT_DRAFT_THRESHOLD`, default **55**).

### 6. Contact extraction and social lookup

After every successful Firecrawl scrape (hunter, hunt-loop, research), the stack extracts contacts from page markdown/HTML:

- **Emails** via regex and `mailto:` links; skip noreply, no-reply, donotreply, privacy, mailer-daemon, notifications@, example.com, sentry.io, wixpress, cloudflare, githubnoreply, and documentation dummy locals (`nowhere@`, `nobody@`, `borderify@`, …). `mailto:` query strings (`?cc=`, `&subject=`, `&body=`) and comma-glued addresses are stripped before storage.
- **Names** only when clearly present (`Name <email>`, prior line, mailto anchor text) — **never invented**
- **Social URLs** already on the page (x.com, linkedin.com/in, instagram.com, facebook.com)
- **Comment authors** (public usernames/handles from thread comment sections) — Reddit `u/username`, blog/Disqus comment blocks, etc. Stored in **`comment_people`** keyed by `(platform, handle)` with no fake email. Skips bots, `[deleted]`, AutoModerator, and 4chan anonymous IDs. Capped at `CRM_COMMENT_PEOPLE_PER_PAGE` (default **40**) unique handles per page.

Each email upserts a row in **`contact_profiles`** (unique lowercase email) and a matching **`Lead`** (`source=CONTACT`). `source_urls` and `socials` merge across pages. Handle-only comment authors do **not** enqueue `enrich_contact` Spark jobs.

**Contact-quality filters** run at extraction, verification, and via backfill:

- **Source relevance** — inspect `source_urls` / scrape page URL; drop contacts found only on ad/tracking hosts or legal boilerplate pages unrelated to the email domain. Community platforms (Reddit, Discord, etc.) and matching-domain pages stay relevant.
- **Generic support emails** — `support@`, `helpdesk@`, and similar role inboxes are not kept as prospects.
- **Documentation dummy emails** — `nowhere@mozilla.org`, `nobody@`, MDN sample add-on IDs, and similar template locals are dropped at extraction, marked invalid by the verifier (even when MX is valid), and removed by backfill.
- **Social scrub** — strip share-link templates (`/intent/tweet`, `sharer.php`, …), ad-firm accounts, and generic platform handles (`@support`, `@help`, …).
- **Notes scrub** — remove tracking-pixel / open-beacon URLs from `hunt_resources.notes` snippets during backfill.

When a profile still has no socials after scrape, a **bounded SearXNG lookup** runs (X / LinkedIn / Instagram) — no paid APIs, no login:

| Variable | Default | Meaning |
|----------|---------|---------|
| `CRM_CONTACT_SOCIAL_QUERIES_PER_PROFILE` | 4 | Max SearXNG queries per profile lookup |
| `CRM_CONTACT_SOCIAL_LOOKUPS_PER_RUN` | 40 | Max profiles looked up per hunt/research run |

Skipped when the scrape already attached socials to that contact.

| Entry | Command / API |
|-------|---------------|
| List profiles | `agent-crm contacts list [--brand …] [--audience marketing\|influencer\|user] [--email …]` · `GET /contacts?audience=…` |
| List comment authors | `GET /comment-people?platform=reddit&brand=…` · dashboard **Contacts** tab → View: `commenters` |
| Backfill filters | `agent-crm contacts backfill [--limit 500] [--dry-run]` · `POST /contacts/backfill` |

### 7. Lead verifier

Defensive contact checks — **DNS, MX, HTTP only**. No SMTP, no RCPT/VRFY, no sending.

Verification runs automatically: the `contact-worker` enqueues `verify_lead` jobs when contact profiles are upserted with a real email (skipping role inboxes and placeholders), and seeds a bounded batch of unverified leads on startup and when idle. Verify jobs do **not** consume Spark slots.

| Entry | Command / API |
|-------|---------------|
| One lead | `agent-crm verify --lead-id N` · `POST /leads/{id}/verify` (manual/debug) |
| Batch | `agent-crm verify --unverified [--limit 50]` · `POST /verify/batch` (manual/debug) |
| Raw | `agent-crm verify --email a@b.com` · `POST /verify/raw` |

Results in `contact_verifications` (one row per lead + contact).

For `source=CONTACT` leads, the verifier also applies the source-relevance / support-email quality gate before DNS/MX checks.

**Role inboxes and placeholders** (`info@`, `hello@`, `careers@`, `name@domain.com`, dotted role locals like `our.team@…`) are marked **invalid** by `check_email()` using the same `contact_quality` helpers as extraction and seeding — even when MX is valid. This keeps `contact_verifications` aligned with orchestrator gap checks (`Dummy or role inbox marked valid`).

To scrub historical rows that were marked valid before this gate: re-run verification on affected leads (`agent-crm verify --lead-id N` or `POST /leads/{id}/verify`). Rows update in place; no migration required. The orchestrator stops raising the gap note once no recent `valid` rows match role/placeholder filters.

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
| `hunt_resources` | Discovered **sites** (directories, communities, newsletters) plus engagement scores |
| `engagement_threads` | Popular posts/threads on catalogued forums for later scans |
| `engagement_drafts` | Comment drafts for human review (never posted) |
| `research_findings` | Research agent output |
| `contact_profiles` | **People** keyed by email — name, socials, source pages |
| `contact_verifications` | Verifier results per lead contact |
| `agent_jobs` | Background job queue (enrich, verify, decode) |
| `agent_improvement_notes` | Self-learning gap/performance notes for orchestrator + Cursor |
| `agent_heartbeats` | Live agent observer state |

`Account.socials` is for companies. Person socials live on `contact_profiles` (and in lead `raw_payload`), not on accounts.

---

## Hermes (read-only query API)

Hermes asks what the ranch has already collected via `GET /agent/*` (OpenAPI tag `hermes`). Same shared token as the rest of the API (`CRM_API_TOKEN`). Send `Authorization: Bearer …` or `X-CRM-Token`, plus `X-CRM-Agent: hermes` for spark-queue occupancy attribution.

Base URL on the Mini (loopback): `http://127.0.0.1:8000`. There is **no** send/outreach path on this prefix — catalog, search, and list only.

| Route | Purpose |
|-------|---------|
| `GET /agent/catalog` | Brands, audiences, resource/finding kinds |
| `GET /agent/search?q=` | Federated hits across contacts, websites, findings, comment people (~10/collection) |
| `GET /agent/contacts` | People with `q` / brand / audience / quality / verified + page envelope |
| `GET /agent/websites` | `hunt_resources` site catalog |
| `GET /agent/findings` | Research findings |
| `GET /agent/comment-people` | Comment authors (no inventing emails) |
| `GET /agent/pipeline-leads` | VALID, non-disqualified leads Pete can work |
| `GET /agent/engagement-threads` | Catalogued popular forum threads |

Every list returns `{ "items", "total", "offset", "limit" }` (default `limit=50`, max `200`).

```bash
curl -s -H "X-CRM-Token: $CRM_API_TOKEN" -H "X-CRM-Agent: hermes" \
  http://127.0.0.1:8000/agent/catalog | jq .

curl -s -H "X-CRM-Token: $CRM_API_TOKEN" -H "X-CRM-Agent: hermes" \
  'http://127.0.0.1:8000/agent/contacts?quality=person&limit=20' | jq .

curl -s -H "X-CRM-Token: $CRM_API_TOKEN" -H "X-CRM-Agent: hermes" \
  'http://127.0.0.1:8000/agent/websites?q=studio&limit=20' | jq .
```

Dashboard list GETs stay unchanged for the Streamlit UI.

---

## Agents and dashboard

Agents call `CRMToolkit(actor="…")` for typed writes. Every mutation appends an `Activity`. Stage changes go through `PipelineManager`.

Post heartbeats via `POST /agents/{agent_name}/heartbeat`. The dashboard polls `GET /agents` and `GET /agents/spark`.

### Dashboard tabs

| Tab | Shows |
|-----|-------|
| **Live agents** | Heartbeats + spark-queue occupancy + persisted tokens / tok/hr / savings (cached, refresh every 10 min) |
| **Pipeline & leads** | Weekly metrics, stage chart, lead table, activity history, verifications |
| **Hunter** | Live hunt-loop drain status + query queue + `hunt_resources` table |
| **Research** | `research_findings` with brand/kind filters |
| **Engagement** | Catalogued threads + comment drafts (not posted) |
| **Contacts** | `contact_profiles` — name, email, socials, source pages |
| **Verifier** | Hunter leads and verification status |
| **Improvement** | Open orchestrator gap/performance notes |

### Agent roster (as implemented)

| Agent | Actor name | What it does today |
|-------|------------|-------------------|
| Lead Intake | `lead_intake` | `POST /intake/webhook` → create lead |
| Lead Scoring | `lead_scoring` | Score + priority via tooling |
| Brand Router | `brand_router` | Assign brand |
| Research | `research` | Competitor / nonprofit / ad-placement runs → `research_findings` |
| Outbound Hunter | `outbound_hunter` | Hunt + hunt-loop → sites and page leads |
| Agent Engagement | `engagement` | Rescan forums → threads + comment drafts (never posts) |
| Lead Verifier | `lead_verifier` | DNS/MX/HTTP checks (auto via `contact-worker`) |
| Job dispatcher | `job-dispatcher` | Drains `agent_jobs` — verify before Spark enrich |
| Orchestrator | `orchestrator` | Stack health inspection → improvement notes |
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
agent-crm jobs                           # Job dispatcher (runs in contact-worker)
agent-crm orchestrate                    # Orchestrator (runs in orchestrator service)

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
agent-crm research --brand heybuddy [--kind nonprofit|competitor|ad_placement|other] [query] \
  [--max-queries 20] [--max-pages 200] [--max-minutes 60] \
  [--search-limit 50] [--no-summarize] [--no-accounts]

agent-crm research-loop \
  [--max-queries 0] [--max-pages 0] [--max-minutes 0] \
  [--search-limit 50] [--no-summarize] [--no-accounts]

# Engagement (never posts)
agent-crm engagement-loop \
  [--brand midnightsatin] [--max-venues 10] \
  [--max-pages-per-venue 15] [--max-minutes 45] [--no-summarize]

# Contacts
agent-crm contacts list \
  [--brand midnightsatin|celestial-nexus|heybuddy|unassigned] \
  [--email user@example.com] [--limit 500]

# Re-apply contact-quality filters to existing rows (Postgres or SQLite)
agent-crm contacts backfill [--limit 500] [--dry-run]

# Docker Compose example (Mini Postgres after merge):
# docker compose run --rm --no-deps api agent-crm contacts backfill --limit 500

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
| POST | `/engagement/loop` | Rescan forums and draft replies (never posts) |
| GET | `/engagement/threads` | List catalogued threads |
| GET | `/engagement/drafts` | List comment drafts |
| GET | `/contacts` | List `contact_profiles` (`?brand=`, `?email=`) |
| POST | `/contacts/backfill` | Re-apply contact-quality filters (`{"limit":500,"dry_run":false}`) |
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
| GET | `/jobs/status` | Agent job queue counts |
| GET | `/improvement-notes` | Self-learning gap notes (`?status=open`) |

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
| `CRM_LLM_INPUT_USD_PER_MILLION` | Cloud-equivalent input rate for Live Agents savings ($2.00) |
| `CRM_LLM_OUTPUT_USD_PER_MILLION` | Cloud-equivalent output rate for Live Agents savings ($10.00) |
| `CRM_HUNTER_MAX_PAGES_PER_RUN` | Max Firecrawl pages per hunt query (50) |
| `CRM_HUNTER_SEARCH_RESULT_LIMIT` | Max SearXNG hits per query (50) |
| `CRM_HUNTER_MAX_QUERIES_DEFAULT` | Hunt-loop query budget (`0` = unlimited) |
| `CRM_HUNTER_MAX_MINUTES_DEFAULT` | Hunt-loop wall clock in minutes (`0` = unlimited) |
| `CRM_HUNTER_COMMUNITY_TERMS_PER_RUN` | Max community feedback queries per loop run (30) |
| `CRM_HUNTER_PERSON_TERMS_PER_RUN` | Max person-name feedback queries per loop run (20) |
| `CRM_HUNTER_ENGAGEMENT_TERMS_PER_RUN` | Max popular-thread feedback queries per loop run (20) |
| `CRM_ENGAGEMENT_MAX_VENUES_PER_RUN` | Forums/communities rescanned per engagement cycle (10) |
| `CRM_ENGAGEMENT_MAX_PAGES_PER_VENUE` | Pages scraped per venue (15) |
| `CRM_ENGAGEMENT_MAX_MINUTES_DEFAULT` | Engagement-loop wall clock (45) |
| `CRM_RESEARCH_MAX_QUERIES_DEFAULT` | Research query budget (20) |
| `CRM_RESEARCH_MAX_PAGES_PER_RUN` | Research scrape budget (200) |
| `CRM_RESEARCH_MAX_MINUTES_DEFAULT` | Research wall clock (60) |
| `CRM_RESEARCH_SEARCH_RESULT_LIMIT` | Research SERP hits per query (50) |
| `CRM_RESEARCH_MAX_BRANCH_TERMS` | Follow-up search terms enqueued per query (8) |
| `CRM_CONTACT_SOCIAL_QUERIES_PER_PROFILE` | SearXNG queries per social lookup (4) |
| `CRM_CONTACT_SOCIAL_LOOKUPS_PER_RUN` | Profiles looked up per run (40) |
| `CRM_API_BASE_URL` | Dashboard → API for live agent panel |
| `CRM_OBSERVER_REFRESH_SECONDS` | Live Agents / Hunter cache + auto-refresh interval (600 = 10 min) |
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

Current chain includes `i4j5k6l7m8n9` (`agent_improvement_notes` + `activitytype.VERIFIED`, after `h3i4j5k6l7m8` comment_people). Do not use `create_all` on Postgres — the API entrypoint and `docker compose` api service run Alembic automatically.
