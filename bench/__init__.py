"""The scheduler ablation: three scheduling policies over one workload.

WHAT THIS MEASURES
    The scheduling policy the agent operates *within* — the forecaster
    (`callsheet.forecast`), the structural guard (`callsheet.guard`), the
    verifier (`callsheet.verify`), and the preempt/downgrade repertoire
    (`callsheet.apply`). Those modules are imported and driven here, not
    reimplemented, so a regression in the product is a regression in the
    benchmark.

WHAT THIS DOES NOT MEASURE
    The language model. Gemini's free tier is 20 generate requests per day
    (§16), so an ablation of a few hundred runs cannot call it and this one
    does not pretend to. `callsheet.decide` — the only place a model is
    called — is never imported for its `decide()` function. In its place the
    CALLSHEET arm uses `bench.policies.choose_sacrifices`, a deterministic
    rule that picks the cheapest structurally valid sacrifice.

    The model's actual contribution is *choosing among valid sacrifices* when
    several would close the gap and they cost the production different things.
    Nothing here evaluates that. A result from this harness is evidence about
    the scheduling machinery; it is not evidence about Gemini.

    Nor is this real Blender. Frame durations are sampled from the measured
    means in §14 with the run-to-run variance recorded in §12/§14/§15. It is
    arithmetic over a measured cost model, not a re-run of the farm.
"""
