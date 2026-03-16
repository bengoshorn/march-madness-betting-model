"""
model.py — Win probability and expected total prediction engine.
"""

from dataclasses import dataclass
from typing import Optional

import scipy.stats

import config
from fetch_stats import TeamStats


# ---------------------------------------------------------------------------
# Weight constants (must sum to 1.0 for expected_margin components)
# ---------------------------------------------------------------------------
W_ADJ_EM = 0.55
W_DEF = 0.20
W_SHOOTING = 0.10
W_TURNOVER = 0.10
W_CONTEXT = 0.05   # seed + conference

# Standard deviation of NCAAB game margins (historical, ~10.5 pts)
SIGMA = config.SIGMA

# Conference tier multipliers (major conf boost)
MAJOR_CONF_BOOST = 0.5   # points added to AdjEM difference

# Seed-based shrinkage: extreme upset (e.g. 16 over 1) gets partially regressed
SEED_DIFF_MAX = 15        # max seed gap that triggers shrinkage
SEED_SHRINK = 0.03        # fraction to shrink expected_margin toward 0 per seed unit gap


@dataclass
class GamePrediction:
    home_team: str
    away_team: str
    home_win_prob: float
    away_win_prob: float
    expected_margin: float          # positive = home team favored
    expected_total: float
    home_spread_cover_prob: float   # P(home covers book spread)
    away_spread_cover_prob: float
    over_prob: float
    under_prob: float
    book_spread_home: Optional[float]
    book_total: Optional[float]


def _seed_adjustment(seed_a: Optional[int], seed_b: Optional[int]) -> float:
    """
    Return a small shrinkage factor toward 0 for large seed mismatches
    (captures intangibles — low seeds often underperform AdjEM vs high seeds).
    Positive return = adjustment in favor of team A.
    """
    if seed_a is None or seed_b is None:
        return 0.0
    diff = seed_b - seed_a   # positive if team A is better seed
    # Shrink margin by SEED_SHRINK * |diff| toward 0
    shrinkage = SEED_SHRINK * abs(diff)
    return shrinkage * (1 if diff > 0 else -1)


def _conference_adjustment(ts_home: TeamStats, ts_away: TeamStats) -> float:
    """Small boost to teams from major conferences (SOS calibration)."""
    home_boost = MAJOR_CONF_BOOST if ts_home.is_major_conf else 0.0
    away_boost = MAJOR_CONF_BOOST if ts_away.is_major_conf else 0.0
    return home_boost - away_boost


def predict_game(
    ts_home: TeamStats,
    ts_away: TeamStats,
    book_spread_home: Optional[float] = None,
    book_total: Optional[float] = None,
) -> GamePrediction:
    """
    Predict win probability, expected total, and spread/totals cover probabilities.

    Convention: positive expected_margin → home team wins by that margin.
    """

    # --- 1. Base AdjEM differential ---
    # AdjEM is points per 100 possessions advantage; game margin ≈ AdjEM * (tempo/100)
    avg_tempo = (ts_home.adj_t + ts_away.adj_t) / 2.0
    tempo_factor = avg_tempo / 100.0

    base_margin = (ts_home.adj_em - ts_away.adj_em) * tempo_factor * W_ADJ_EM / 0.55
    # Note: We normalize so that weights make sense as additive adjustments (not multipliers)
    base_margin = (ts_home.adj_em - ts_away.adj_em) * tempo_factor

    # --- 2. Defensive edge bonus ---
    # Lower AdjD is better defense; positive diff = home defense is better
    def_diff = (ts_away.adj_d - ts_home.adj_d)   # positive = home defense better
    def_adj = def_diff * W_DEF

    # --- 3. Net shooting margin ---
    # Net shooting = (own EFG% - opp EFGD%) per team; delta favors team with larger net
    net_shooting_home = ts_home.efg_pct - ts_home.efgd_pct
    net_shooting_away = ts_away.efg_pct - ts_away.efgd_pct
    shooting_diff = net_shooting_home - net_shooting_away  # dimensionless ~[-0.15, 0.15]
    # Scale to points: roughly 1 unit of EFG% net ≈ 15 pts per 100 poss
    shooting_adj = shooting_diff * 15.0 * W_SHOOTING

    # --- 4. Ball control margin ---
    # net_to_home = TOD% (forcing turnovers) - TO% (committing turnovers) — higher is better
    net_to_home = ts_home.tod_pct - ts_home.to_pct
    net_to_away = ts_away.tod_pct - ts_away.to_pct
    to_diff = net_to_home - net_to_away  # dimensionless ~[-0.10, 0.10]
    # Scale: 1% turnover advantage ≈ ~0.7 pts per game
    to_adj = to_diff * 70.0 * W_TURNOVER

    # --- 5. Context adjustment (seed + conference) ---
    seed_adj = _seed_adjustment(ts_home.seed, ts_away.seed) * W_CONTEXT / 0.05
    conf_adj = _conference_adjustment(ts_home, ts_away) * W_CONTEXT / 0.05
    context_adj = (seed_adj + conf_adj) * W_CONTEXT

    # --- 6. Combine ---
    expected_margin = (
        base_margin * W_ADJ_EM +
        def_adj +
        shooting_adj +
        to_adj +
        context_adj
    )

    # --- 7. Win probability ---
    # P(home wins) = P(margin > 0) = CDF(margin / sigma)
    home_win_prob = float(scipy.stats.norm.cdf(expected_margin / SIGMA))
    away_win_prob = 1.0 - home_win_prob

    # --- 8. Expected total ---
    # Pythagorean-style: each team's offense vs opponent defense, scaled by tempo
    # AdjO is points per 100 possessions; AdjD is points allowed per 100 possessions
    home_pts = ts_home.adj_o * (ts_away.adj_d / 100.0) * tempo_factor
    away_pts = ts_away.adj_o * (ts_home.adj_d / 100.0) * tempo_factor
    expected_total = home_pts + away_pts

    # --- 9. Spread cover probabilities ---
    if book_spread_home is not None:
        # P(home covers) = P(margin > spread)
        # book_spread_home is negative if home is favored (e.g. -5.5 means home -5.5)
        home_spread_cover_prob = float(
            scipy.stats.norm.cdf((expected_margin - book_spread_home) / SIGMA)
        )
        away_spread_cover_prob = 1.0 - home_spread_cover_prob
    else:
        home_spread_cover_prob = home_win_prob
        away_spread_cover_prob = away_win_prob

    # --- 10. Over/under probabilities ---
    if book_total is not None:
        # Assume total follows normal dist with same sigma (rough approximation)
        total_sigma = SIGMA * 1.4   # totals have more variance than side
        over_prob = float(1.0 - scipy.stats.norm.cdf((book_total - expected_total) / total_sigma))
        under_prob = 1.0 - over_prob
    else:
        over_prob = 0.5
        under_prob = 0.5

    return GamePrediction(
        home_team=ts_home.name,
        away_team=ts_away.name,
        home_win_prob=home_win_prob,
        away_win_prob=away_win_prob,
        expected_margin=expected_margin,
        expected_total=expected_total,
        home_spread_cover_prob=home_spread_cover_prob,
        away_spread_cover_prob=away_spread_cover_prob,
        over_prob=over_prob,
        under_prob=under_prob,
        book_spread_home=book_spread_home,
        book_total=book_total,
    )
