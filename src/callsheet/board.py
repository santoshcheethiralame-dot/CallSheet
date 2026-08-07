"""The call sheet as a coordinator would read it. Pure: no clock, no I/O.

Everything here is a view of numbers another module already computed. It takes
finished forecasts, the decision, the guard's rejections and the verifier's
residuals and arranges them; it never re-derives an ETA or re-decides whether a
shot misses. That is the point of the file — the page must not become a second
place where scheduling logic lives, quietly disagreeing with the annotation
written to Grafana.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from callsheet.decide import Action, Decision
from callsheet.domain import Review, Shot
from callsheet.forecast import Forecast
from callsheet.verify import Residual

STRUCK = "struck"
IN_THE_CAN = "in_the_can"
AT_RISK = "at_risk"
RENDERING = "rendering"
WAITING = "waiting"

STOCKS = ("white", "blue", "pink", "goldenrod")
"""Revision stock in production order. A call sheet is reissued on a new colour
each time it is amended, and everyone on the crew reads the colour before the
content."""


@dataclass(frozen=True)
class ShotCard:
    shot_id: str
    quality: str
    frames_total: int
    frames_done: int
    eta_s: int | None
    """Seconds from now until this shot finishes, straight off its forecast.
    `None` when the shot has no forecast at all. Not measured against the
    deadline: the page has `deadline_epoch_s` and can draw the difference, and
    keeping the raw figure means a card is still readable when there is no
    review pending."""

    state: str
    thumbnail: str | None
    estimate_source: str
    """Carried through from the forecast so the card can admit when its ETA came
    from a default rather than a measurement."""


@dataclass(frozen=True)
class Rejection:
    """A guard refusal, in the shape the page consumes.

    The engine passes rejections as `(Action, why)` tuples. Naming the two
    halves here keeps that pairing from reaching the page as a two-element
    array whose meaning depends on remembering the order."""

    action: Action
    reason: str


@dataclass(frozen=True)
class BoardEvent:
    at_epoch_s: int
    text: str
    """One plain sentence, already phrased for the feed. The board does not
    compose these; whoever drives the round does."""


@dataclass(frozen=True)
class BoardState:
    review_name: str
    deadline_epoch_s: int
    revision: int
    stock: str
    """`revision_stock(revision)`, resolved here so the page does not keep its
    own copy of the table."""

    cards: list[ShotCard]
    summary: str
    actions: list[Action]
    """What was actually done — the guard's survivors, not the model's wishlist."""

    rejections: list[Rejection]
    residuals: list[Residual]
    events: list[BoardEvent] = field(default_factory=list)
    degraded_reason: str | None = None
    """Why this round has no plan behind it, verbatim from `RoundResult`.

    Carried onto the board rather than swallowed because the cards keep showing
    real forecasts while the model is out: without this the page would present a
    degraded round as a healthy one that simply decided to do nothing."""


def revision_stock(revision: int) -> str:
    """White, blue, pink, goldenrod — and then goldenrod forever.

    Real productions do carry on past goldenrod (salmon, buff, cherry), but the
    scale is only useful because everyone recognises every colour on it. Holding
    goldenrod already means the day has gone badly; there is nothing worse to
    say, so the board stops saying it louder.
    """
    return STOCKS[min(max(revision, 0), len(STOCKS) - 1)]


def _card_state(struck: bool, done: int, total: int, forecast: Forecast | None) -> str:
    """struck -> in_the_can -> at_risk -> rendering -> waiting.

    The order is the whole content of this function. Struck comes first because
    a struck shot is no longer anyone's problem and its progress and ETA have
    stopped meaning anything. `at_risk` beats `rendering` because a shot that is
    underway and *still* going to miss is precisely the card a coordinator has
    to act on — reporting it as merely "rendering" would hide the only thing on
    the board worth interrupting someone for.
    """
    if struck:
        return STRUCK
    if total > 0 and done >= total:
        return IN_THE_CAN
    if forecast is not None and forecast.misses_deadline:
        return AT_RISK
    if done > 0:
        return RENDERING
    return WAITING


def _thumbnail(shot: Shot, done: int) -> str | None:
    """The latest frame this shot has actually produced, as a URL.

    Indexed into the shot's own frame list rather than formatted from the count,
    because a shot need not start at frame 1 and a card showing a frame that was
    never rendered is a lie about the only real pixels on the page.
    """
    if done <= 0 or not shot.frames:
        return None
    frame = shot.frames[min(done, len(shot.frames)) - 1]
    return f"/frames/{shot.id}_{frame:04d}.png"


def _summary(decision: Decision | None, cards: Sequence[ShotCard]) -> str:
    if decision is not None:
        return decision.summary
    if any(card.state == AT_RISK for card in cards):
        # There is a gap and no plan yet. Saying nothing here, or saying
        # everything is fine, would be the board contradicting its own cards.
        return "A required shot is behind. No amendment has been issued yet."
    return "Every required shot makes the review."


def build_board(
    shots: Sequence[Shot],
    review: Review,
    forecasts: Sequence[Forecast],
    decision: Decision | None = None,
    applied: Sequence[Action] = (),
    rejections: Sequence[tuple[Action, str]] = (),
    residuals: Sequence[Residual] = (),
    *,
    revision: int = 0,
    frames_done: Mapping[str, int] | None = None,
    events: Sequence[BoardEvent] = (),
    degraded_reason: str | None = None,
    now_epoch_s: int,
) -> BoardState:
    """Arrange one round's output as a board.

    `applied` is the guard's survivors, never `decision.actions`: an action the
    guard threw out was not carried out, and striking its shot on the board
    would make the page claim something the annotation denies.
    """
    done_by_shot = dict(frames_done or {})
    forecast_by_shot = {forecast.shot_id: forecast for forecast in forecasts}
    struck = {a.shot_id for a in applied if a.action == "preempt"}
    downgraded = {a.shot_id for a in applied if a.action == "downgrade"}

    cards = []
    for shot in shots:
        done = done_by_shot.get(shot.id, 0)
        total = len(shot.frames)
        forecast = forecast_by_shot.get(shot.id)
        cards.append(
            ShotCard(
                shot_id=shot.id,
                quality="proxy" if shot.id in downgraded else shot.quality,
                frames_total=total,
                frames_done=done,
                eta_s=None if forecast is None
                else forecast.finishes_at_epoch_s - now_epoch_s,
                state=_card_state(shot.id in struck, done, total, forecast),
                thumbnail=_thumbnail(shot, done),
                estimate_source=forecast.estimate_source if forecast else "fallback",
            )
        )

    return BoardState(
        review_name=review.name,
        deadline_epoch_s=review.deadline_epoch_s,
        revision=revision,
        stock=revision_stock(revision),
        cards=cards,
        summary=_summary(decision, cards),
        actions=list(applied),
        rejections=[Rejection(action, why) for action, why in rejections],
        residuals=list(residuals),
        events=list(events),
        degraded_reason=degraded_reason,
    )
