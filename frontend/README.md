# The Agency dashboard (Vite + React)

Local Live Agents UI. Streamlit on port 8501 still holds the other tabs.

```bash
# API on :8000 first, then:
npm install
npm run dev
```

http://localhost:5173 proxies `/api` to `http://127.0.0.1:8000` and forwards `CRM_API_TOKEN` from the repo `.env`.

Compose serves the production build on port 3000 (`web` service), bound to `0.0.0.0` for LAN access. Postgres and spark-queue stay on loopback.
