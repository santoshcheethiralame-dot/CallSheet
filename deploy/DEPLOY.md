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

## Hugging Face Spaces — the chosen target

Free, no credit card, no expiry. That keeps the campaign's `$0, no card` rule
intact end to end, which Cloud Run cannot: Cloud Run needs billing enabled on the
GCP project even when the free tier and the $100 hackathon credit would cover the
whole thing several times over.

### Steps

1. **Create the Space.** huggingface.co → New Space → SDK **Docker** → blank
   template. Account creation and Space creation are yours; nothing here can do
   them for you.
2. **Set the secrets.** Space → Settings → *Variables and secrets*. Add each as a
   **secret**, not a variable:

   | Name | Where it came from |
   |---|---|
   | `GRAFANA_URL` | the stack URL, e.g. `https://<stack>.grafana.net` |
   | `GRAFANA_SERVICE_ACCOUNT_TOKEN` | Grafana → Administration → Service accounts |
   | `OTLP_ENDPOINT` | Grafana → Connections → OpenTelemetry |
   | `OTLP_AUTH` | base64 of `instanceID:token`, the blob after `Basic%20` |
   | `GEMINI_API_KEY` | aistudio.google.com |

   `BLENDER_PATH` is **not** needed — nothing renders in the container.
   `MCP_GRAFANA_PATH` and `CALLSHEET_DB` are already set in the Dockerfile.

3. **Push the code.** The Space is its own git remote:

   ```bash
   git remote add space https://huggingface.co/spaces/<user>/callsheet
   cp deploy/SPACE_README.md README.md   # on the space branch only, see below
   git push space main
   ```

   The Space needs YAML frontmatter at the top of `README.md` to know it is a
   Docker Space and which port to route to. GitHub renders that frontmatter as a
   table, which is why it lives in `deploy/SPACE_README.md` rather than at the
   root — keep the GitHub README clean and swap it in on a branch you push only
   to the Space.

### What a healthy Space looks like

- Build log ends with the image pushed and the container starting uvicorn.
- The board loads, cards show real frames, the countdown ticks.
- Within one round (30s) the sheet either stays white with *"Every required shot
  makes the review"*, or advances to blue with an amendment.
- With no secrets set, the Space **still serves** — `start_rounds` treats missing
  credentials as "no live round" rather than an error, and the board falls back
  to what is on disk. A judge cloning the repo with an empty `.env` gets the same
  behaviour.

### Known constraints

- **Gemini free tier is 20 requests per day, per model.** The session only asks
  the model when the *situation* changes, so a running Space costs roughly one
  call per frame that completes rather than one per round. Still: if the quota is
  spent, the board shows *"Scheduling by priority - the daily model quota is
  spent"* and keeps forecasting. That is the degraded path working, not a
  failure.
- **Spaces sleep** when idle on the free tier. First load after a sleep pays a
  cold start.
- **The container has never been built here** — Docker is not installed on the
  development machine. The Dockerfile is written against a verified
  `mcp-grafana_Linux_x86_64.tar.gz` asset and the real import surface, but the
  first build is the first test of it.

## Cloud Run — if the card constraint is ever dropped

The same image works. Cloud Run injects `$PORT`, which overrides the 7860
default, and `/tmp` is writable, so `CALLSHEET_DB` needs no change.

```bash
gcloud run deploy callsheet --source . --region asia-south1 \
  --allow-unauthenticated --set-secrets=GEMINI_API_KEY=gemini-key:latest,...
```
