"""
fetch_stats.py — Scrape team stats from Bart Torvik (T-Rank) and merge seed info.
"""

import json
import re
import io
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

import requests
import pandas as pd


TRANK_URL = "https://barttorvik.com/trank.php?year=2026&csv=1"
SEEDS_FILE = Path(__file__).parent / "seeds.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAJOR_CONFERENCES = {
    "ACC", "Big 12", "Big East", "Big Ten", "Pac-12", "SEC", "American",
}


@dataclass
class TeamStats:
    name: str
    adj_em: float = 0.0     # Adjusted Efficiency Margin (points per 100 possessions)
    adj_o: float = 100.0    # Adjusted Offensive Efficiency
    adj_d: float = 100.0    # Adjusted Defensive Efficiency (lower = better)
    adj_t: float = 68.0     # Adjusted Tempo (possessions per game)
    efg_pct: float = 0.50   # Effective FG% (offense)
    efgd_pct: float = 0.50  # Opponent EFG% allowed (defense)
    to_pct: float = 0.18    # Turnover rate (offense — lower = better)
    tod_pct: float = 0.18   # Opponent turnover rate forced (higher = better)
    orb_pct: float = 0.30   # Offensive rebound rate
    ftr: float = 0.30       # Free throw rate
    conference: str = "Unknown"
    seed: Optional[int] = None
    is_major_conf: bool = False


# ---------------------------------------------------------------------------
# Team name normalization
# ---------------------------------------------------------------------------

# Manual overrides: Torvik name → Odds API name
_NAME_MAP = {
    "Connecticut": "UConn",
    "UConn": "UConn",
    "Miami FL": "Miami (FL)",
    "Miami (FL)": "Miami (FL)",
    "NC State": "NC State",
    "North Carolina State": "NC State",
    "Saint Mary's": "Saint Mary's",
    "St. Mary's": "Saint Mary's",
    "Saint Mary's (CA)": "Saint Mary's",
    "Mississippi": "Ole Miss",
    "Ole Miss": "Ole Miss",
    "Louisiana State": "LSU",
    "LSU": "LSU",
    "Southern California": "USC",
    "USC": "USC",
    "Texas Christian": "TCU",
    "TCU": "TCU",
    "McNeese": "McNeese State",
    "McNeese State": "McNeese State",
    "Pitt": "Pittsburgh",
    "Pittsburgh": "Pittsburgh",
    "VCU": "VCU",
    "Penn State": "Penn State",
    "UNC": "North Carolina",
    "North Carolina": "North Carolina",
}


def _normalize_name(name: str) -> str:
    """Return a normalized team name compatible with The Odds API."""
    name = name.strip()
    if name in _NAME_MAP:
        return _NAME_MAP[name]
    # Strip common suffixes that the API drops
    for suffix in [" University", " College", " State University"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


# ---------------------------------------------------------------------------
# CSV parsing helpers
# ---------------------------------------------------------------------------

# Torvik CSV columns vary by year; define expected columns with fallback indices
_COL_ALIASES = {
    "team": ["Team", "team"],
    "adj_em": ["AdjEM", "Adj EM", "AdjOE-AdjDE"],
    "adj_o": ["AdjOE", "AdjO", "Adj OE"],
    "adj_d": ["AdjDE", "AdjD", "Adj DE"],
    "adj_t": ["AdjTE", "AdjT", "Adj T"],
    "efg": ["EFG%", "EFG", "eFG%"],
    "efgd": ["EFGD%", "EFGD", "Opp eFG%", "Opp EFG%"],
    "to": ["TO%", "TO Pct", "TOR"],
    "tod": ["TOD%", "Opp TO%", "TORD"],
    "orb": ["OR%", "ORB%", "ORB"],
    "ftr": ["FTR", "FT Rate"],
    "conf": ["Conf", "Conference", "conf"],
}


def _find_col(df: pd.DataFrame, aliases: list[str]) -> Optional[str]:
    for a in aliases:
        if a in df.columns:
            return a
    return None


def _safe_float(val, default: float) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Seed loading
# ---------------------------------------------------------------------------

def _load_seed_map() -> dict[str, int]:
    """Return {normalized_team_name: seed_number}."""
    if not SEEDS_FILE.exists():
        return {}
    with open(SEEDS_FILE) as f:
        raw: dict = json.load(f)
    seed_map: dict[str, int] = {}
    for seed_str, teams in raw.items():
        seed = int(seed_str)
        for t in teams:
            seed_map[_normalize_name(t)] = seed
    return seed_map


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

def fetch_stats() -> dict[str, TeamStats]:
    """Fetch Bart Torvik team stats and return dict keyed by normalized name."""
    print("  Fetching Bart Torvik stats...")
    resp = requests.get(TRANK_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()

    # Torvik CSV sometimes has a BOM or extra whitespace
    text = resp.text.lstrip("\ufeff").strip()

    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]

    # Map column names
    col = {k: _find_col(df, v) for k, v in _COL_ALIASES.items()}

    if col["team"] is None:
        raise ValueError(f"Cannot find team column in Torvik CSV. Columns: {list(df.columns)}")

    seed_map = _load_seed_map()
    stats: dict[str, TeamStats] = {}

    for _, row in df.iterrows():
        raw_name = str(row[col["team"]])
        norm_name = _normalize_name(raw_name)

        conf = str(row[col["conf"]]).strip() if col["conf"] else "Unknown"
        is_major = conf in MAJOR_CONFERENCES

        ts = TeamStats(
            name=norm_name,
            adj_em=_safe_float(row[col["adj_em"]], 0.0) if col["adj_em"] else 0.0,
            adj_o=_safe_float(row[col["adj_o"]], 100.0) if col["adj_o"] else 100.0,
            adj_d=_safe_float(row[col["adj_d"]], 100.0) if col["adj_d"] else 100.0,
            adj_t=_safe_float(row[col["adj_t"]], 68.0) if col["adj_t"] else 68.0,
            efg_pct=_safe_float(row[col["efg"]], 0.50) if col["efg"] else 0.50,
            efgd_pct=_safe_float(row[col["efgd"]], 0.50) if col["efgd"] else 0.50,
            to_pct=_safe_float(row[col["to"]], 0.18) if col["to"] else 0.18,
            tod_pct=_safe_float(row[col["tod"]], 0.18) if col["tod"] else 0.18,
            orb_pct=_safe_float(row[col["orb"]], 0.30) if col["orb"] else 0.30,
            ftr=_safe_float(row[col["ftr"]], 0.30) if col["ftr"] else 0.30,
            conference=conf,
            seed=seed_map.get(norm_name),
            is_major_conf=is_major,
        )

        stats[norm_name] = ts
        # Also index by raw name for fallback lookup
        if raw_name != norm_name:
            stats[raw_name] = ts

    print(f"  Loaded {len([v for v in stats.values() if v.name == list(stats.keys())[0] or True])} team entries ({len(set(id(v) for v in stats.values()))} unique teams).")
    return stats


def lookup_team(stats: dict[str, TeamStats], api_name: str) -> Optional[TeamStats]:
    """
    Find a TeamStats by The Odds API team name.
    Falls back to fuzzy substring matching if exact match fails.
    """
    if api_name in stats:
        return stats[api_name]

    norm = _normalize_name(api_name)
    if norm in stats:
        return stats[norm]

    # Fuzzy: find longest key that is a substring of api_name or vice versa
    api_lower = api_name.lower()
    best: Optional[TeamStats] = None
    best_len = 0
    for key, ts in stats.items():
        key_lower = key.lower()
        if key_lower in api_lower or api_lower in key_lower:
            if len(key) > best_len:
                best = ts
                best_len = len(key)
    return best


if __name__ == "__main__":
    stats = fetch_stats()
    sample = list(stats.items())[:5]
    for name, ts in sample:
        print(f"{name}: AdjEM={ts.adj_em:.2f}, AdjO={ts.adj_o:.1f}, AdjD={ts.adj_d:.1f}, Seed={ts.seed}")
