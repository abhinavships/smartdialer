# Railway Deployment Guide: SmartDialer

This guide outlines deploying SmartDialer on Railway.

---

## Deployment Instructions

### 1. Provision PostgreSQL Database
1. Open your Railway project canvas.
2. Click **+ New** $\rightarrow$ **Database** $\rightarrow$ **Add PostgreSQL**.
3. Railway automatically provisions PostgreSQL 16 and exposes the connection variable `DATABASE_URL`.

### 2. Deploy API Service
1. Click **+ New** $\rightarrow$ **GitHub Repo** and select the SmartDialer repository.
2. Rename the service to `smartdialer-api`.
3. In **Settings** $\rightarrow$ **Build & Deploy**:
   - Set **Dockerfile Path** to `deploy/Dockerfile` (or set Root Directory).
   - Set **Custom Start Command**:
     ```bash
     uvicorn smartdialer.main:app --host 0.0.0.0 --port $PORT
     ```
4. Connect the service to the PostgreSQL instance so `DATABASE_URL` is automatically injected into the container environment.
5. Railway exposes a public HTTP domain and maps `$PORT` automatically.

### 3. Deploy Worker Service
1. In the same project canvas, click **+ New** $\rightarrow$ **GitHub Repo** and select the *same* SmartDialer repository.
2. Rename the service to `smartdialer-worker`.
3. In **Settings** $\rightarrow$ **Build & Deploy**:
   - Set **Dockerfile Path** to `deploy/Dockerfile`.
   - Set **Custom Start Command**:
     ```bash
     python -m smartdialer.worker
     ```
4. Connect the service to the PostgreSQL instance so `DATABASE_URL` is automatically injected into the worker environment.

---

## Why Vercel Cannot Host SmartDialer

Vercel is designed exclusively for request-scoped serverless functions (e.g. Next.js API routes, AWS Lambda equivalents) that spin up on HTTP invocation and terminate immediately after returning an HTTP response.

SmartDialer requires **long-running background execution processes**:
- The **Pacing Engine Loop** runs continuously every 1 second to recalculate campaign targets and dispatch calls.
- The **Reaper Coroutine** executes every 5 seconds to reclaim expired leases (`reserved_until`) and handle timed-out calls.
- The **Worker Daemon** continuously executes agent reservation transactions (`FOR UPDATE SKIP LOCKED`).

Because serverless platforms like Vercel freeze process state and terminate background timers after HTTP responses complete, long-running loops and lease reapers cannot execute reliably on Vercel. Continuous container platforms such as Railway or AWS ECS are required.
