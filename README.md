# Agent CRM

A local, agent-driven CRM that recreates the parts of Salesforce that actually matter for finding clients and chasing leads — without Salesforce scale, licenses, or cloud lock-in.

This repo starts as the working brief for that system. Salesforce’s “find clients + chase leads” engine is not one product. It is **six subsystems** coordinated as if they were a single machine. The bet here is that those same six jobs can be done by a small roster of specialized local agents, a lightweight CRM database, and an orchestrator that keeps them in lockstep.

The non-obvious insight: **Salesforce’s magic is not the CRM. It is the coordination of multiple specialized agents.** A ranch-scale stack can cover most of that surface if it is tuned for a handful of creative/tech brands instead of a global sales org.

Target brands for routing and nurture:

- **Midnightsatin**
- **Celestial-Nexus**
- **HeyBuddy**

This is not a Salesforce clone. It is a mini Salesforce: inbound capture, scoring, research, outbound hunting, drip nurture, and pipeline tracking, all owned locally.

---

## The six subsystems

| # | Job | Salesforce analog | Local equivalent |
|---|-----|-------------------|------------------|
| 1 | Lead capture (inbound) | Web-to-lead, bots, APIs, social listening | Inbound Listener agent |
| 2 | Lead qualification | Einstein scoring, routing | Lead Scorer agent + rules |
| 3 | Lead enrichment | Data Cloud, Clearbit, ZoomInfo | Researcher agent |
| 4 | Outbound prospecting | Sales Engagement, cadences, Agentforce | Outbound Hunter + Outreach Writer |
| 5 | Nurture automation | Marketing Cloud journeys | Nurture Engine agent |
| 6 | Pipeline tracking | Opportunities, accounts, activities, forecast | Pipeline Manager + Analytics |

Each subsystem is a bounded agent (or a pair of agents) with a clear input, a clear output, and a write-back into the same CRM store. The orchestrator is the only component allowed to sequence them.

---

### 1. Lead Capture (Inbound)

**Salesforce tools:** web-to-lead forms, chatbots (Piper, Einstein Bots), API integrations, social listening.

**Local job:** notice that a human asked to be in the pipeline, then write a lead record before anything else happens.

**Agent: Inbound Listener**

- Scrapes or receives website contact-form submissions
- Monitors an email inbox
- Reads DMs from Instagram / Twitter / Discord via API where credentials exist
- Pushes new leads into the local CRM store (SQLite first; Postgres on a NAS when the file outgrows a laptop)

**Tools:** FastAPI webhook receiver, a parser for inbound messages, SQLite or Postgres.

Inbound is a write path, not a conversation. The listener does not score, research, or reply. It creates the record and hands off.

---

### 2. Lead Qualification (Scoring)

**Salesforce tools:** Einstein Lead Scoring, behavioral signals, automated routing.

**Local job:** decide whether this lead is worth time, and which brand should own it.

**Agent: Lead Scorer**

Reads each new lead and scores on:

- Budget signals
- Urgency
- Project type
- Fit for Midnightsatin / Celestial-Nexus / HeyBuddy

Then tags a priority level and a brand recommendation. Routing can be a separate Brand Router agent so scoring stays numeric and brand choice stays explicit.

**Tools:** a small local classifier (Mistral 7B or Phi-3) plus a Python rules engine for hard gates (missing email, obvious spam, wrong geography, etc.). Rules first, model second. The KV-cache optimized local inference pipeline is the intended runtime, not a hosted ranking API.

---

### 3. Lead Enrichment (Research)

**Salesforce tools:** Data Cloud, Clearbit, ZoomInfo, company profiles.

**Local job:** put enough context on the record that outreach is not a cold guess.

**Agent: Researcher**

- Searches the public web for the lead and their company
- Scrapes their site
- Pulls social profiles when they are public
- Summarizes the business in a few paragraphs
- Writes that context back onto the CRM entry

**Tools:** Playwright for browser work, a local LLM summarizer, the same orchestration layer that runs the rest of the roster.

Enrichment is best-effort and must be idempotent. A failed scrape is a note on the record, not a blocked pipeline.

---

### 4. Outbound Prospecting (Finding Clients)

**Salesforce tools:** Sales Engagement, cadence automation, AI outbound agents (Hunter, Agentforce).

**Local job:** grow the top of funnel on purpose, not only from inbound luck.

**Agent: Outbound Hunter**

- Searches for potential clients
- Builds prospect lists
- Hands each prospect to the Outreach Writer
- Schedules follow-ups rather than sending forever in one burst

**Agent: Outreach Writer** (paired, not mixed into the hunter)

- Writes personalized first-touch copy from enrichment + brand voice
- Does not send. Sending belongs to the Nurture Engine so delivery, throttling, and unsubscribe live in one place.

**Tools:** Playwright + scraping, local LLM writer, a scheduler the orchestrator already owns.

---

### 5. Nurture Automation (Follow-ups)

**Salesforce tools:** Marketing Cloud Journeys, automated email sequences, multi-channel messaging.

**Local job:** keep a human in the loop without requiring a human to remember every follow-up.

**Agent: Nurture Engine**

- Sends follow-up email
- Records whether a message was opened or replied to when that signal exists
- Fires reminders
- Runs brand-specific drip campaigns for Midnightsatin, Celestial-Nexus, and HeyBuddy

**Tools:** SMTP, local LLM for message generation, a state machine per “journey” (stage, next send at, stop conditions).

Journeys stop on reply, unsubscribe, disqualification, or closed-won / closed-lost. No infinite drips.

---

### 6. Pipeline Tracking (CRM)

**Salesforce tools:** Opportunities, Accounts, Activities, Forecasting.

**Local job:** be the source of truth the other agents read and write.

**Agent: Pipeline Manager**

- Maintains the CRM database
- Moves leads through stages
- Alerts when a lead is hot
- Produces the weekly report with the Analytics agent

**Tools:** SQLite or Postgres, a local dashboard (Streamlit or a small Node/Next app), the orchestration layer.

This is the only subsystem that must exist on day one of implementation. Capture, score, research, hunt, and nurture are useless if stage and activity history live in chat logs.

---

## Recommended agent roster

Deploy these roles even if some of them share a process at first. Names stay stable so the orchestrator can route work without renaming jobs later.

| # | Agent | Owns |
|---|--------|------|
| 1 | **Lead Intake** | Inbound listening; new lead records |
| 2 | **Lead Scoring** | Priority, budget/urgency/fit signals |
| 3 | **Research** | Public-web enrichment and summaries |
| 4 | **Outbound Hunter** | Prospect discovery and list building |
| 5 | **Outreach Writer** | Personalized copy, brand voice |
| 6 | **Nurture** | Drips, reminders, send/stop rules |
| 7 | **CRM Manager** | Stages, accounts, opportunities, activities |
| 8 | **Analytics** | Weekly reports, hot-lead alerts |
| 9 | **Brand Router** | Midnightsatin vs Celestial-Nexus vs HeyBuddy |
| 10 | **Orchestrator** | Sequencing, retries, handoffs, schedules |

That is one orchestrator and nine specialists. Five to ten specialists plus a store plus browser and email automation is the whole machine.

```
                    ┌─────────────────┐
                    │  Orchestrator   │
                    └────────┬────────┘
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                 ▼
    Lead Intake        Outbound Hunter    CRM Manager
           │                 │                 │
           ▼                 ▼                 ▼
    Lead Scoring      Outreach Writer      Analytics
           │                 │
           ▼                 ▼
    Brand Router         Nurture
           │
           ▼
    Research
```

Handoff contract (happy path for inbound):

1. Intake writes a lead (`new`)
2. Scoring writes a score + priority
3. Brand Router writes a brand
4. Research writes enrichment
5. CRM Manager opens or updates the opportunity stage
6. Outreach Writer drafts (if outbound or first reply is needed)
7. Nurture owns the journey
8. Analytics reads the week; it does not mutate pipeline except via alerts

---

## What “local” means here

- **Store:** SQLite on disk to start; Postgres on a Synology NAS (or equivalent) when concurrent agents need it.
- **Inference:** local models (Mistral 7B / Phi-3 class) on the existing KV-cache optimized pipeline. Hosted APIs are a fallback, not the architecture.
- **Browser:** Playwright, run where the ranch already runs agents — not a SaaS scraper.
- **Mail:** SMTP you control. No Marketing Cloud.
- **Orchestration:** the multi-agent layer already in use. This CRM should be a new *workload* on that layer, not a second orchestrator.

The system should still be useful if the LLM is down: rules can score, humans can move stages, and intake can still write rows.

---

## Intended data, in one page

Enough to implement later without inventing a Salesforce schema.

**Lead** — person at the top of funnel. Source (form, email, DM, hunter), raw payload, score, priority, brand, enrichment summary, status.

**Account** — company or project home. Site, socials, notes from Research.

**Opportunity** — a lead that is in play. Stage, amount if known, brand, next action at.

**Activity** — every send, scrape, score, stage change, and human note. Agents append; they do not rewrite history.

**Journey** — nurture state machine instance: template set, step index, next run, stop reason.

Suggested inbound stages: `new → scored → enriched → contacted → replied → qualified → won / lost`. Outbound inserts a `prospect` stage before `contacted`.

---

## What this repo is (and is not)

**Is:** the charter for a ranch-scale CRM built from local agents. Next work should implement against this brief rather than rediscovering Salesforce feature lists.

**Is not:** a hosted Salesforce alternative, a multi-tenant SaaS, or a scraping product. Outbound hunting must respect robots.txt, terms of service, and anti-spam law. Enrichment uses public pages. Mail must honor unsubscribe.

---

## Implementation order (when code starts)

The roster is the destination. The first slice of software should be smaller:

1. CRM store + Pipeline Manager (stages, activities, a readable dashboard)
2. Lead Intake (webhook / form / mailbox → row)
3. Scoring + Brand Router (rules first)
4. Research (Playwright + summary)
5. Outreach Writer + Nurture (SMTP, journeys)
6. Outbound Hunter
7. Analytics (weekly report)

Do not stand up ten agents on day one. Stand up the store, then attach agents in the order they create value.

---

## Running locally

No application is in this repo yet. This document is the starting artifact.

When the first slice lands, this section will cover how to start the store, the intake webhook, and the dashboard.
