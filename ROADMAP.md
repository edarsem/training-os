# Training OS Roadmap

This roadmap tracks **product evolution and delivery order**.
For setup and current usage, see `README.md`.

## Current state

- [x] Calendar-first training log (sessions + day notes)
- [x] Weekly plan as separate intent layer
- [x] Weekly stats and 20-week analysis page
- [x] FIT import pipeline with fallback parser
- [x] Local-only runtime data strategy (`backend/data`)

## Next & soon

Goal: improve coaching-ready data quality.

- [ ] Add explicit session `title` and `description` fields (for future Strava sync)
- [ ] Add structured strength tags at model/API level (not only notes metadata)
- [ ] Add block summaries for last 4/8/12 weeks
- [ ] Add per-sport trend cards (run, bike, swim, strength)

## LLM integration

Goal: assistant-ready analysis and suggestions.

- [ ] LLM client abstraction in backend
- [ ] Weekly analysis endpoint using prompt files
- [ ] UI panel for AI coaching summary
- [ ] Prompt version selector + evaluation notes
