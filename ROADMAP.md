# Training OS Roadmap

This document tracks product direction and sequencing.
For project overview and usage, see README.md.

## Product vision

Build a personal training operating system that is:

- reliable for day-to-day execution,
- rich enough for meaningful load analysis,
- and ready for assistant-style coaching.

## Current baseline (done)

- Calendar-first workflow for sessions and day notes.
- Weekly plan separated from actual execution.
- Weekly stats and trend analysis pages.
- Dual ingestion strategy (FIT + Strava) with merge behavior.
- Token-based Strava sync with incremental refresh and full backfill modes.
- Local-first runtime data architecture.

## Near-term priorities

Focus: make data quality and UX strong enough for consistent training decisions.

- [ ] Improve session readability in UI for long notes and context-heavy sessions.
- [ ] Normalize sport/type taxonomy for cleaner analytics.
- [ ] Add clearer source provenance markers (manual / FIT / Strava) in views.
- [ ] Add explicit model-level fields for title and description (separate from notes).
- [ ] Add block summaries over rolling 4/8/12 weeks.

## Load analytics phase

Focus: move from descriptive tracking to actionable training-load monitoring.

- [ ] Define first training-load model from available summary metrics.
- [ ] Add sport-aware load aggregation and monotony/strain indicators.
- [ ] Add alerting thresholds for fatigue/risk patterns.
- [ ] Add confidence indicators when source metrics are incomplete.

## Coaching assistant phase

Focus: turn data into weekly guidance while preserving user control.

- [ ] Introduce backend LLM client abstraction.
- [ ] Add weekly coaching summary endpoint (structured + narrative).
- [ ] Add UI coaching panel with transparent rationale.
- [ ] Add prompt/version tracking for repeatable quality.

## Principles for future work

- Keep Actuals and Plan separate.
- Prefer stable data contracts over quick UI hacks.
- Keep local ownership and privacy defaults.
- Add complexity only when it unlocks clear training value.
