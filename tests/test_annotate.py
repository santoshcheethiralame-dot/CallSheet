import os

import pytest

from callsheet.annotate import build_annotation
from callsheet.decide import Action, Decision

DECISION = Decision(
    summary="Preempting SH002 so SH001 makes the 09:00 review.",
    actions=[Action("SH002", "preempt", "already cut")],
)


def test_annotation_carries_the_summary_as_text():
    payload = build_annotation(DECISION, now_epoch_s=1_000_000)
    assert "SH002" in payload["text"]
    assert DECISION.summary in payload["text"]


def test_annotation_time_is_epoch_milliseconds():
    """Grafana expects ms. Passing seconds silently places it in 1970."""
    payload = build_annotation(DECISION, now_epoch_s=1_000_000)
    assert payload["time"] == 1_000_000_000


def test_annotation_is_tagged_for_retrieval():
    payload = build_annotation(DECISION, now_epoch_s=1_000_000)
    assert "callsheet" in payload["tags"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_writes_a_real_annotation_and_reads_it_back():
    """Uses the real clock deliberately.

    A fixed epoch of 1_000_000 dates the annotation to January 1970, where
    Grafana's default time range will never show it — a successful write would
    look like a failure. Determinism belongs in the pure tests above; this one
    needs to land somewhere a human can actually see it.
    """
    import time

    from callsheet.annotate import write_annotation
    from callsheet.config import Config
    from callsheet.grafana_mcp import call_tool

    config = Config.from_env(os.environ)
    now = int(time.time())

    result = await write_annotation(config, DECISION, now_epoch_s=now)
    assert "error" not in result.lower(), result

    # Read it back by tag. `create_annotation` can accept and discard a
    # malformed payload, so a non-error response is not evidence of anything.
    found = await call_tool(config, "get_annotations", {"tags": ["callsheet"], "matchAny": False})
    assert DECISION.summary in found, f"annotation not retrievable: {found[:400]}"
