# CALLSHEET Phase 4 — Shot Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the system a face — a production surface a judge understands in ten seconds without narration, carrying the same honesty the engine now has.

**Architecture:** FastAPI serves one hand-built page plus a JSON state endpoint and an SSE stream. Rendered frames are served straight from `out/` as shot thumbnails. No framework, no CDN, no build step — a strict CSP-free single file so the whole surface is auditable in one read.

**Tech Stack:** FastAPI + uvicorn, server-sent events, vanilla HTML/CSS/JS, system fonts only.

## Global Constraints

- **$0, no credit card.** FastAPI and uvicorn are free; hosting is Cloud Run inside existing credits (Phase 5).
- **No CDN, no npm, no build step.** Every asset is local or a system font. This is also a hackathon-rules benefit: everything a judge needs to run is in the repo.
- **No non-Google AI models, agent frameworks, or AI APIs.**
- **Python 3.11+. Windows 11 / PowerShell.**
- Baseline: **114 passed, 8 deselected.** Keep it green.
- Quality floor, unannounced: responsive to mobile, visible keyboard focus, `prefers-reduced-motion` respected.

---

## Design direction

### The thesis

**The page is a call sheet being amended in real time.** Not a dashboard that happens to list shots — the production document itself, revised as the night goes wrong.

### The signature: revision stock

Film call sheets are reissued on coloured paper as they are revised — **white, then blue, then pink, then goldenrod**. Every crew member knows that holding goldenrod means the day has gone badly. That convention maps exactly onto what this agent does: each decision issues a new revision, and the paper colour advances.

It is specific to this subject, invisible to anyone who has not worked in production, and instantly legible once shown — which is exactly what a three-minute video needs. **This is the one bold element; everything else stays quiet.**

### Tokens

```css
--suite-black:  #14171A;   /* the room: a monitor at 2am, cold and slightly blue */
--suite-panel:  #1C2126;   /* raised surface */
--suite-line:   #2A3138;   /* hairlines, borders */
--stock-white:  #EDE9DE;   /* revision 0 — the paper */
--stock-blue:   #8FB8D6;   /* revision 1 */
--stock-pink:   #E39BAE;   /* revision 2 */
--stock-gold:   #D9A441;   /* revision 3+ — and the at-risk colour */
--grease:       #B33A2B;   /* grease-pencil red: struck, preempted, missing */
--in-the-can:   #6E9E7B;   /* rendered, delivered, safe */
--ink:          #0E1113;   /* type on paper */
--ink-soft:     #6B7379;   /* secondary type on dark */
```

Deliberately **not** the near-black-plus-one-acid-accent look this brief would default to. The palette is a production artifact, not a mood: every colour names a real thing a coordinator would recognise.

### Type

System faces only — and chosen, not defaulted:

```css
--display: "Bahnschrift", "Archivo Narrow", "Haettenschweiler", Impact, sans-serif;
--mono:    ui-monospace, "Cascadia Mono", Consolas, "SF Mono", monospace;
--body:    system-ui, -apple-system, "Segoe UI", sans-serif;
```

`Bahnschrift` ships with Windows 10+ and is a DIN-derived condensed grotesque — industrial, stencil-adjacent, and almost never used on the web. It carries the slate header. Timecode, frame counts and shot codes are **always** mono, because that is what post actually looks like. Labels are uppercase at small size with wide tracking, as on a real call sheet.

### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│ CALLSHEET      DIRECTOR REVIEW 09:00     [REV 2 · PINK]  T-04:32 │  slate header
├────────────────────────────────────┬─────────────────────────────┤
│ SHOT BOARD                         │  ┌───────────────────────┐  │
│ ┌────────┐ ┌────────┐ ┌────────┐   │  │  REVISION 2 — PINK    │  │  the paper
│ │ frame  │ │ frame  │ │ frame  │   │  │  ─────────────────    │  │
│ │ SH001  │ │ SH002  │ │ SH003  │   │  │  Preempting SH002...  │  │
│ │ ███▁▁  │ │ ▚▚▚▚▚  │ │ █▁▁▁▁  │   │  │  PREEMPT   SH002      │  │
│ │ +16s   │ │ STRUCK │ │ +96s   │   │  │  ───────────────────  │  │
│ └────────┘ └────────┘ └────────┘   │  │  GAP NOT CLOSED       │  │
│                                    │  │  SH003 short by 65s   │  │
│                                    │  └───────────────────────┘  │
│                                    │  EVENT FEED                 │
│                                    │  02:14:07 SH003/2 rendered  │
└────────────────────────────────────┴─────────────────────────────┘
```

Shot cards lead with the **real rendered frame**. That is the visible input the whole demo rests on — a judge sees actual pixels being produced, not a simulation.

### Copy

Production vernacular, from the user's side of the screen:

| Not this | This |
|---|---|
| Job failed | SH003 frame 2 did not render |
| Deadline missed | SH003 will not make the 09:00 review |
| Action applied | Struck from tonight's queue |
| No data | Nothing queued. The farm is idle. |
| Error: quota exceeded | Scheduling by priority — the model is unavailable |

The last one matters: the degraded state must read as a working system in a lesser mode, not a broken one.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/callsheet/board.py` | Assembles `BoardState` from shots, forecasts, decision, residuals — pure |
| `src/callsheet/server.py` | FastAPI app: page, `/api/state`, `/api/stream`, `/frames/*` |
| `web/index.html` | The page: markup, tokens, styles, and behaviour in one auditable file |
| `tests/test_board.py` | State assembly |
| `tests/test_server.py` | Routes, via `fastapi.testclient` |

`board.py` is pure and takes already-computed values, so the page can never
become a second place where forecasting logic lives.

---

### Task 1: Board state assembly

**Files:**
- Create: `src/callsheet/board.py`
- Test: `tests/test_board.py`

**Interfaces:**
- Produces: `ShotCard(shot_id, quality, frames_total, frames_done, eta_s, state, thumbnail, estimate_source)` where `state ∈ {"waiting","rendering","in_the_can","struck","at_risk"}`; `BoardState(review_name, deadline_epoch_s, revision, cards, summary, actions, rejections, residuals, events)`; `build_board(...) -> BoardState`; `revision_stock(revision) -> str` returning `"white"|"blue"|"pink"|"goldenrod"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_board.py
from callsheet.board import build_board, revision_stock
from callsheet.decide import Action, Decision
from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast
from callsheet.verify import Residual

NOW = 1_000_000
SHOTS = [
    Shot("SH001", "a.blend", 16, [1, 2, 3]),
    Shot("SH002", "b.blend", 64, [1, 2, 3]),
    Shot("SH003", "c.blend", 256, [1, 2, 3]),
]
REVIEW = Review("Director review", NOW + 30, ["SH001", "SH003"])
FORECASTS = [
    Forecast("SH001", 3, 15_441.0, NOW + 16, False, "observed"),
    Forecast("SH002", 3, 22_023.0, NOW + 38, False, "observed"),
    Forecast("SH003", 3, 80_445.0, NOW + 118, True, "observed"),
]


def test_revision_stock_follows_the_production_convention():
    assert revision_stock(0) == "white"
    assert revision_stock(1) == "blue"
    assert revision_stock(2) == "pink"
    assert revision_stock(3) == "goldenrod"


def test_revision_stock_stays_goldenrod_past_the_third_revision():
    """There is no paper worse than goldenrod. The scale ends there."""
    assert revision_stock(9) == "goldenrod"


def test_a_missing_shot_is_marked_at_risk():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH003").state == "at_risk"


def test_a_preempted_shot_is_marked_struck():
    decision = Decision("s", [Action("SH002", "preempt", "not required")])
    board = build_board(SHOTS, REVIEW, FORECASTS, decision,
                        applied=decision.actions, rejections=[], revision=1,
                        frames_done={}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH002").state == "struck"


def test_a_rejected_preempt_does_not_strike_the_shot():
    """The board must agree with the annotation about what was actually done."""
    action = Action("SH002", "preempt", "x")
    decision = Decision("s", [action])
    board = build_board(SHOTS, REVIEW, FORECASTS, decision,
                        applied=[], rejections=[(action, "behind")], revision=1,
                        frames_done={}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH002").state != "struck"


def test_a_fully_rendered_shot_is_in_the_can():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={"SH001": 3}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH001").state == "in_the_can"


def test_a_partially_rendered_shot_is_rendering():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={"SH001": 1}, events=[], now_epoch_s=NOW)
    card = next(c for c in board.cards if c.shot_id == "SH001")
    assert card.state == "rendering"
    assert card.frames_done == 1
    assert card.frames_total == 3


def test_the_thumbnail_points_at_the_latest_rendered_frame():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={"SH001": 2}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH001").thumbnail == "/frames/SH001_0002.png"


def test_a_shot_with_no_rendered_frames_has_no_thumbnail():
    board = build_board(SHOTS, REVIEW, FORECASTS, None, [], [], revision=0,
                        frames_done={}, events=[], now_epoch_s=NOW)
    assert next(c for c in board.cards if c.shot_id == "SH001").thumbnail is None
```

- [ ] **Step 2: Run to verify it fails, then implement**

`build_board` must derive `state` in this precedence: struck (in `applied`
preempts) → in_the_can (frames_done == total) → at_risk (forecast misses) →
rendering (0 < frames_done < total) → waiting. Struck wins because a struck shot
is no longer anyone's problem; at_risk beats rendering because a shot in progress
that will still miss is the thing a coordinator needs to see.

- [ ] **Step 3: Commit**

```bash
git add src/callsheet/board.py tests/test_board.py
git commit -m "Assemble the board state from what the round already computed"
```

---

### Task 2: The server

**Files:**
- Create: `src/callsheet/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- `GET /` → the page
- `GET /api/state` → `BoardState` as JSON
- `GET /api/stream` → SSE, one `state` event per tick
- `GET /frames/{name}` → a rendered PNG from `out/`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server.py
from fastapi.testclient import TestClient

from callsheet.server import app

client = TestClient(app)


def test_the_page_is_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "CALLSHEET" in response.text


def test_state_endpoint_returns_the_board():
    response = client.get("/api/state")
    assert response.status_code == 200
    body = response.json()
    assert "cards" in body
    assert "revision" in body


def test_a_frame_outside_the_output_directory_is_refused():
    """The frames route takes a filename from the URL. It must not walk out."""
    response = client.get("/frames/..%2F..%2F.env")
    assert response.status_code in (400, 404)


def test_a_missing_frame_is_a_404_not_a_crash():
    assert client.get("/frames/SH999_9999.png").status_code == 404
```

The path-traversal test is not ceremony: the route interpolates a URL segment
into a filesystem path, and `.env` sits two directories up.

- [ ] **Step 2: Implement, verify, commit**

Serve frames by resolving the requested name against `out/` and rejecting
anything whose resolved path is not inside it. Do not use `StaticFiles` for this
— an explicit check is what the test is asserting.

```bash
git add src/callsheet/server.py tests/test_server.py pyproject.toml
git commit -m "Serve the board state and the rendered frames"
```

---

### Task 3: The page

**Files:**
- Create: `web/index.html`

Build to the design direction above. Order of work: tokens and the slate header
first, then the shot grid, then the paper panel, then the feed. Check each
against the brief before moving on.

- [ ] **Step 1: Slate header** — product name in `--display`, review name, the revision chip in the current stock colour, and a live countdown in `--mono`. The countdown is the only always-moving element.
- [ ] **Step 2: Shot cards** — rendered frame as the card face, shot code and quality in mono, a segmented progress bar (one segment per frame, not a continuous bar — a coordinator counts frames), ETA against deadline. `at_risk` cards take a goldenrod edge; `struck` cards get a grease-pencil strike drawn across them.
- [ ] **Step 3: The paper panel** — light stock on the dark suite, the revision stamp rotated a few degrees like a real rubber stamp, the agent's summary in body type, actions in mono, and the residual line in `--grease` when the gap is open.
- [ ] **Step 4: Event feed** — timecode plus a plain sentence per line, newest first, capped.
- [ ] **Step 5: Live updates** — subscribe to `/api/stream`, patch the DOM in place. Honour `prefers-reduced-motion`.
- [ ] **Step 6: Quality floor** — mobile down to 375px, visible focus rings, no horizontal scroll.

- [ ] **Step 7: Commit**

```bash
git add web/index.html
git commit -m "Add the shot board"
```

---

### Task 4: Wire the board to a live round

**Files:**
- Modify: `src/callsheet/server.py`, `scripts/demo_round.py`

The board must show a real round, not a fixture. A background task runs
`run_round` on the timer from §5.3, bumps the revision on each decision, appends
events, and pushes new state to connected clients.

**The revision only advances on a decision**, not on every tick — otherwise the
signature element becomes a clock and means nothing.

- [ ] **Step 1: Implement, then verify in the browser**

Start it, open it, and check: cards carry real frames, the countdown runs, a
decision advances the revision and changes the stock colour, the residual line
appears when the gap is open.

- [ ] **Step 2: Screenshot for the record, then commit**

```bash
git add src/callsheet/server.py scripts/demo_round.py README.md
git commit -m "Drive the board from a live scheduling round"
```

---

## Definition of done for Phase 4

- [ ] `python -m pytest` green with no credentials
- [ ] The board renders real frames from `out/`
- [ ] A decision visibly advances the revision and its stock colour
- [ ] An open gap is stated on the page, not just in the terminal
- [ ] Degraded mode reads as a working system in a lesser mode
- [ ] Mobile at 375px, visible focus, reduced motion respected
- [ ] Phase 4 findings appended to the design doc
