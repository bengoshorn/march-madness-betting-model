"""
ev_calculator.py — EV and Kelly Criterion bet sizing logic.
"""

from dataclasses import dataclass
from typing import Optional

import config


@dataclass
class BetRecommendation:
    game: str           # "Away @ Home"
    market: str         # "Moneyline", "Spread", "Over", "Under"
    side: str           # team name or "Over"/"Under"
    model_prob: float
    implied_prob: float
    ev_pct: float
    kelly_f: float
    bet_size: float     # dollars
    american_odds: int


# ---------------------------------------------------------------------------
# Odds conversions
# ---------------------------------------------------------------------------

def american_to_implied(odds: int) -> float:
    """Convert American odds to implied win probability (no vig removed)."""
    if odds > 0:
        return 100.0 / (odds + 100.0)
    else:
        return abs(odds) / (abs(odds) + 100.0)


def american_to_decimal(odds: int) -> float:
    """Convert American odds to decimal odds (profit + stake per $1 stake)."""
    if odds > 0:
        return (odds / 100.0) + 1.0
    else:
        return (100.0 / abs(odds)) + 1.0


# ---------------------------------------------------------------------------
# EV calculation
# ---------------------------------------------------------------------------

def calculate_ev(model_prob: float, american_odds: int) -> float:
    """
    Return EV as a fraction of stake (e.g. 0.05 = 5% EV).

    EV = model_prob * decimal_profit - (1 - model_prob)
    where decimal_profit = decimal_odds - 1
    """
    decimal = american_to_decimal(american_odds)
    profit = decimal - 1.0
    ev = model_prob * profit - (1.0 - model_prob)
    return ev


# ---------------------------------------------------------------------------
# Kelly Criterion
# ---------------------------------------------------------------------------

def kelly_fraction(model_prob: float, american_odds: int) -> float:
    """
    Full Kelly fraction: f = (b*p - q) / b
    where b = decimal_odds - 1, p = win prob, q = 1 - p.
    Returns 0 if negative (don't bet).
    """
    b = american_to_decimal(american_odds) - 1.0
    p = model_prob
    q = 1.0 - p
    if b <= 0:
        return 0.0
    f = (b * p - q) / b
    return max(f, 0.0)


def size_bet(model_prob: float, american_odds: int, bankroll: float) -> tuple[float, float]:
    """
    Return (kelly_f, bet_size_dollars) using fractional Kelly with hard cap.
    """
    kf = kelly_fraction(model_prob, american_odds)
    raw_bet = bankroll * config.KELLY_FRACTION * kf
    max_bet = bankroll * config.MAX_BET_PCT
    bet = min(raw_bet, max_bet)
    return kf, round(bet, 2)


# ---------------------------------------------------------------------------
# Main EV evaluation entry point
# ---------------------------------------------------------------------------

def evaluate_bets(game_label: str, prediction, game_odds) -> list[BetRecommendation]:
    """
    Given a GamePrediction and GameOdds, return all bets that pass MIN_EV_THRESHOLD.
    """
    from model import GamePrediction
    from fetch_odds import GameOdds

    recs: list[BetRecommendation] = []
    bankroll = config.BANKROLL

    def _check(market: str, side: str, model_prob: float, odds: Optional[int]):
        if odds is None:
            return
        ev = calculate_ev(model_prob, odds)
        if ev < config.MIN_EV_THRESHOLD:
            return
        implied = american_to_implied(odds)
        kf, bet = size_bet(model_prob, odds, bankroll)
        if bet <= 0:
            return
        recs.append(BetRecommendation(
            game=game_label,
            market=market,
            side=side,
            model_prob=model_prob,
            implied_prob=implied,
            ev_pct=ev,
            kelly_f=kf,
            bet_size=bet,
            american_odds=odds,
        ))

    o = game_odds.odds

    # Moneyline
    _check("Moneyline", prediction.home_team, prediction.home_win_prob, o.home_odds)
    _check("Moneyline", prediction.away_team, prediction.away_win_prob, o.away_odds)

    # Spread
    _check("Spread", f"{prediction.home_team} {o.home_spread:+.1f}" if o.home_spread is not None else prediction.home_team,
           prediction.home_spread_cover_prob, o.spread_home_odds)
    _check("Spread", f"{prediction.away_team} {o.away_spread:+.1f}" if o.away_spread is not None else prediction.away_team,
           prediction.away_spread_cover_prob, o.spread_away_odds)

    # Totals
    _check("Over", f"Over {o.total_line}", prediction.over_prob, o.over_odds)
    _check("Under", f"Under {o.total_line}", prediction.under_prob, o.under_odds)

    return recs
