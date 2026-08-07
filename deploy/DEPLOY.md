# Deploying the board

The hosted surface is the **agent**, not the farm. Blender is deliberately not in
the image: rendering is local work that produces telemetry, and the hosted board
is the thing that reads that telemetry back. Everything the hackathon rules check
for at runtime — `google-genai` calling Gemini, `mcp-grafana` talking to Grafana
Cloud — happens inside this container. Adding Blender would cost ~500 MB to serve
frames that were already rendered.

Six real frames from a real render ship in `demo/frames/`, so a fresh container
has something to show. A live render still wins whenever `out/` has anything in
it.

## Render — the chosen target

Free tier, **no credit card**, Docker supported, 750 hours a month. That keeps
the campaign's `$0, no card` rule intact end to end.

`render.yaml` at the repo root is a blueprint: Render reads it and provisions the
service without any dashboard clicking beyond the secrets.

### Steps

1. **Sign in at render.com** with the GitHub account. Account creation is yours.
2. **New → Blueprint**, point it at `santoshcheethiralame-dot/CallSheet`. Render
   finds `render.yaml` and proposes the `callsheet` service.
3. **Fill the five secrets** when prompted. They are declared `sync: false` in the
   blueprint, so Render asks once and never writes them to the repo:

   | Name | Where it came from |
   |---|---|
   | `GRAFANA_URL` | the stack URL, e.g. `https://<stack>.grafana.net` |
   | `GRAFANA_SERVICE_ACCOUNT_TOKEN` | Grafana → Administration → Service accounts |
   | `OTLP_ENDPOINT` | Grafana → Connections → OpenTelemetry |
   | `OTLP_AUTH` | base64 of `instanceID:token` — the blob after `Basic%20` |
   | `GEMINI_API_KEY` | aistudio.google.com |

   `BLENDER_PATH` is **not** needed — nothing renders in the container.
   `MCP_GRAFANA_PATH` and `CALLSHEET_DB` are already set.

4. **Deploy.** First build pulls `mcp-grafana` and installs the package; expect a
   few minutes.

### What a healthy service looks like

- Build log ends with uvicorn starting on `$PORT`.
- `/api/state` returns JSON with a `cards` array — this is the health check path.
- The board loads, rows show real frames, the countdown ticks.
- Within one round (30s) the sheet either stays white with *"Every required shot
  makes the review"*, or advances to blue with an amendment.
- With no secrets set it **still serves**: `start_rounds` treats missing
  credentials as "no live round" rather than an error, and the board falls back
  to what is on disk. A judge cloning the repo with an empty `.env` sees the same
  thing.

### Known constraints

- **Free services sleep after 15 minutes idle**, with a 30–60 second cold start.
  Judging runs 23 Sept – 7 Oct; hit the URL yourself before sharing it, and
  consider a cheap external pinger during that window. This is the price of the
  no-card rule and it was chosen deliberately.
- **Gemini free tier is 20 requests per day, per model.** The session asks the
  model only when the *situation* changes, so a running service costs roughly one
  call per frame that completes rather than one per round. If the quota is spent
  the board shows *"Scheduling by priority - the daily model quota is spent"* and
  keeps forecasting. That is the degraded path working, not a failure.
- **The container has never been built here** — Docker is not installed on the
  development machine. The Dockerfile is written against a verified
  `mcp-grafana_Linux_x86_64.tar.gz` release asset and the real import surface, but
  the first build is the first test of it.

## Rejected: Hugging Face Spaces

Was the plan until the Space creation form showed **Docker marked Paid**. Both
Docker and Gradio Spaces now require PRO at $9/month; only Static Spaces remain
free, and static cannot run a server. Worth knowing because most write-ups
online still describe Docker Spaces as free.

## Rejected for now: Cloud Run

The same image would work — Cloud Run injects `$PORT`, which overrides the
default, and `/tmp` is writable, so nothing changes. Real spend would be $0: the
always-free tier covers this, before the $300 trial and the $100 hackathon
credit. The blocker is that Cloud Run requires billing enabled on the project,
which means a card on file.

If that constraint is ever dropped, it is the stronger host — no sleep penalty,
faster cold starts, and a better answer to *"how effectively does it use Google
Cloud"*.

```bash
gcloud run deploy callsheet --source . --region asia-south1 --allow-unauthenticated
```
