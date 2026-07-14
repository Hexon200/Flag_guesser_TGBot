# Flag Atlas Design Rationale

The TMA uses an atlas/travel-poster direction: paper tones, map-grid texture, route colors, and a serif display face make flags feel like a collection journey instead of a generic quiz dashboard. The same tokens drive quiz, matching, duel, profile, and leaderboard screens so future polish should extend the map/poster language rather than drifting into unrelated arcade or default web-app styling.

Tradeoffs:

- Adaptive difficulty needs enough answer history per user before it becomes meaningful; new users fall back to tier/category pools with light random weighting.
- The historical flags pack is not enabled because the current dataset only contains modern country flags; adding it should start with a trustworthy historical flag dataset.
- Quick match now provides a real queue and duel room handoff, but full synchronized best-of-N gameplay still needs round-state APIs on top of the existing WebSocket/replay foundation.
