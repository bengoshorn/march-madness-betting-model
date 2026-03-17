"""
fetch_odds.py — Pull FanDuel odds from The Odds API for NCAAB tournament games.
"""

import requests
from dataclasses import dataclass, field
from typing import Optional
import config


@dataclass
class MarketOdds:
    home_odds: Optional[int] = None   # American odds
    away_odds: Optional[int] = None
    home_spread: Optional[float] = None
    away_spread: Optional[float] = None
    spread_home_odds: Optional[int] = None
    spread_away_odds: Optional[int] = None
    total_line: Optional[float] = None
    over_odds: Optional[int] = None
    under_odds: Optional[int] = None


@dataclass
class GameOdds:
    game_id: str
    home_team: str
    away_team: str
    commence_time: str
    odds: MarketOdds = field(default_factory=MarketOdds)


BASE_URL = "https://api.the-odds-api.com/v4"


def get_active_ncaab_key() -> str:
    """Find the active NCAAB sport key (may differ during tournament)."""
    resp = requests.get(
        f"{BASE_URL}/sports",
        params={"apiKey": config.ODDS_API_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    sports = resp.json()

    # Prefer tournament-specific key if active, else fall back to general NCAAB
    tournament_keys = [s["key"] for s in sports if "ncaab" in s["key"].lower() and s.get("active")]
    if not tournament_keys:
        return config.SPORT_KEY
    # Prefer keys with "tournament" or "march" in the name
    for key in tournament_keys:
        if "tournament" in key or "march" in key:
            return key
    return tournament_keys[0]


def _parse_fanduel_outcomes(outcomes: list, home_team: str, away_team: str, market_key: str, m: MarketOdds):
    """Parse FanDuel outcome entries into the MarketOdds object."""
    for outcome in outcomes:
        name = outcome.get("name", "")
        price = outcome.get("price")  # American odds int
        point = outcome.get("point")  # spread or total line

        if market_key == "h2h":
            if name == home_team:
                m.home_odds = price
            elif name == away_team:
                m.away_odds = price

        elif market_key == "spreads":
            if name == home_team:
                m.home_spread = point
                m.spread_home_odds = price
            elif name == away_team:
                m.away_spread = point
                m.spread_away_odds = price

        elif market_key == "totals":
            if name == "Over":
                m.total_line = point
                m.over_odds = price
            elif name == "Under":
                m.under_odds = price


def fetch_odds(sport_key: str = None, date: str = None) -> list[GameOdds]:
    """
    Fetch FanDuel odds for NCAAB games.

    Args:
        sport_key: The Odds API sport key (defaults to config.SPORT_KEY).
        date: Optional date string 'YYYY-MM-DD'. When provided, only games
              starting between midnight and 11:59 PM ET on that date are
              returned. Defaults to all upcoming games.
    """
    if sport_key is None:
        sport_key = config.SPORT_KEY

    params = {
        "apiKey": config.ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "bookmakers": "fanduel",
        "oddsFormat": "american",
    }

    if date:
        # ET is UTC-4 during March (EDT). Bracket games tip off from ~noon ET onward.
        # Use a window of midnight–midnight UTC+4 to be safe and catch all tip times.
        params["commenceTimeFrom"] = f"{date}T00:00:00Z"
        params["commenceTimeTo"]   = f"{date}T23:59:59Z"

    resp = requests.get(
        f"{BASE_URL}/sports/{sport_key}/odds/",
        params=params,
        timeout=15,
    )

    if resp.status_code == 422:
        # Sport key may be wrong — try auto-detecting
        print("  [warn] Sport key lookup failed, attempting auto-detect...")
        sport_key = get_active_ncaab_key()
        resp = requests.get(
            f"{BASE_URL}/sports/{sport_key}/odds/",
            params={**params},
            timeout=15,
        )

    resp.raise_for_status()
    data = resp.json()

    games: list[GameOdds] = []
    for event in data:
        home = event["home_team"]
        away = event["away_team"]
        game = GameOdds(
            game_id=event["id"],
            home_team=home,
            away_team=away,
            commence_time=event.get("commence_time", ""),
        )

        # Find FanDuel bookmaker entry
        for bookmaker in event.get("bookmakers", []):
            if bookmaker["key"] != "fanduel":
                continue
            for market in bookmaker.get("markets", []):
                key = market["key"]
                _parse_fanduel_outcomes(market.get("outcomes", []), home, away, key, game.odds)
            break  # only need FanDuel

        games.append(game)

    return games


if __name__ == "__main__":
    games = fetch_odds()
    print(f"Fetched {len(games)} games.")
    for g in games:
        print(f"  {g.away_team} @ {g.home_team}")
        o = g.odds
        print(f"    ML:  away={o.away_odds}  home={o.home_odds}")
        print(f"    SPR: away={o.away_spread}({o.spread_away_odds})  home={o.home_spread}({o.spread_home_odds})")
        print(f"    TOT: line={o.total_line}  over={o.over_odds}  under={o.under_odds}")
