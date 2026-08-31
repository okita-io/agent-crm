"""The Agency: a local, agent-driven CRM that writes SEO and AEO/GEO documents.

Product name: The Agency. Python package and CLI remain ``agent_crm`` / ``agent-crm``.

The specialized agents build on:

- ``agent_crm.models``  -- the CRM data model (Lead, Account, Opportunity, Activity, Journey, SEO docs)
- ``agent_crm.db``      -- engine/session management for SQLite and Postgres
- ``agent_crm.tooling`` -- the stable CRM SDK every agent calls to read/write the store
- ``agent_crm.pipeline``-- the Pipeline Manager (stage transitions, hot-lead logic)
- ``agent_crm.api``     -- the FastAPI service (health + intake webhook)
"""

__version__ = "0.1.0"
