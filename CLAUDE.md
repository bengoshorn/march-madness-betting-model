# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the model

```bash
python main.py                          # interactive bankroll prompt
python main.py --bankroll 500           # skip prompt, set bankroll directly
python main.py --min-ev 0.02 --verbose  # lower EV threshold + show game previews
python main.py --sport-key basketball_ncaab_tournament  # override sport key during tournament
python main.py --no-prompt              # non-interactive mode (uses config.py defaults)
```

Each individual module can be run standalone to test it in isolation:

```bash
python fetch_odds.py   # prints all games + raw FanDuel lines
python fetch_stats.py  # prints first 5 teams from Torvik CSV
```

## Architecture

The pipeline is strictly linear: `fetch_odds` → `fetch_stats` → `model` → `ev_calculator`, all orchestrated by `main.py`. Each stage is independent — no module imports another except where the data flows downstream.

**Data flow:**
1. `fetch_odds.py` → returns `list[GameOdds]` (FanDuel lines for each game, all three markets)
2. `fetch_stats.py` → returns `dict[str, TeamStats]` (Torvik stats keyed by normalized team name)
3. `model.py` → `predict_game(ts_home, ts_away, ...)` → `GamePrediction` (win probs, expected margin/total, cover probs)
4. `ev_calculator.py` → `evaluate_bets(label, prediction, game_odds)` → `list[BetRecommendation]`

**Key design decisions:**

- `config.py` is the single source of truth for all tunable parameters. `main.py` mutates `config.BANKROLL` and `config.MIN_EV_THRESHOLD` at runtime if the user provides CLI overrides — all downstream modules read from `config` directly, so this propagates automatically.

- Team name matching is the primary fragility point. The Odds API and Torvik use different names for many teams. `fetch_stats.py` handles this in two layers: a manual `_NAME_MAP` dict for known mismatches, then a fuzzy substring fallback in `lookup_team()`. When a team is skipped in output, the name mismatch is the likely cause — add it to `_NAME_MAP`.

- `model.py` uses a 5-factor weighted sum to produce `expected_margin` (in points), then passes it through `scipy.stats.norm.cdf(margin / SIGMA)` to get win probability. The weights `W_ADJ_EM`, `W_DEF`, `W_SHOOTING`, `W_TURNOVER`, `W_CONTEXT` at the top of `model.py` are the primary levers for model tuning. `SIGMA = 10.5` (in `config.py`) controls how sharply probabilities move with margin — lower values make the model more confident.

- Expected total uses a Pythagorean-style formula: `(AdjO_home × AdjD_away/100 + AdjO_away × AdjD_home/100) × tempo_factor`. Totals cover probability uses `SIGMA * 1.4` because game totals have wider variance than sides.

- Kelly bet sizing applies `KELLY_FRACTION` (25%) to the full Kelly output, then caps at `MAX_BET_PCT` (20%) of bankroll. Both live in `config.py`.

## Updating for the tournament

- **`seeds.json`** must be updated once the bracket is announced. Format: `{"1": ["Team A", "Team B", ...], "2": [...]}` — 4 teams per seed line. Seeds feed into the context adjustment in `model.py` (`_seed_adjustment`).
- **`SPORT_KEY`** in `config.py` may need to change from `basketball_ncaab` to a tournament-specific key (e.g. `basketball_ncaab_tournament`) once the tournament begins. Use `--sport-key` flag to test without editing `config.py`.
- The Torvik URL in `fetch_stats.py` is hardcoded to `year=2026` — update this each season.

## Git workflow

After every meaningful unit of work — a bug fix, a new feature, a config change, a refactor — commit and push immediately to `bengoshorn/march-madness-betting-model`. Do not batch multiple unrelated changes into one commit. The goal is that GitHub always reflects the current working state of the project so nothing is ever lost.

Commit message format:
- Subject line: imperative mood, ≤72 chars, describes *what* changed (e.g. `Fix team name mismatch for UConn in _NAME_MAP`)
- Body (if needed): one or two lines explaining *why*, not restating the diff

Push after every commit:
```bash
git add <specific files>
git commit -m "subject line"
git push
```

Never use `git add -A` or `git add .` — always stage specific files by name to avoid accidentally committing secrets or junk files.
