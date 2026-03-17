"""
fetch_stats.py — Scrape team stats from Bart Torvik (T-Rank) and merge seed info.

Torvik's CSV has no header row. Column positions (verified March 2026):
  0: Team name
  1: AdjOE  (offensive efficiency, pts per 100 poss)
  2: AdjDE  (defensive efficiency, pts per 100 poss — lower is better)
  3: Win probability
  7: EFG%   (offense, in percent e.g. 54.7)
  8: EFGD%  (opponent EFG% allowed, in percent)
 11: TO%    (turnover rate offense, in percent)
 12: TOD%   (opponent turnover rate forced, in percent)
 13: OR%    (offensive rebound rate, in percent)
 15: AdjT   (adjusted tempo, possessions per game)
AdjEM is computed as AdjOE - AdjDE (not a separate column).

Bot protection: Torvik returns a JS verification page on GET.
Fix: use a Session, GET first to get the cookie, then POST with js_test_submitted=1.
"""

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import requests


TRANK_URL = "https://barttorvik.com/trank.php?year=2026&csv=1"
SEEDS_FILE = Path(__file__).parent / "seeds.json"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Verified column positions for the Torvik headerless CSV (2026)
_COL_TEAM  = 0
_COL_ADJO  = 1
_COL_ADJD  = 2
_COL_EFG   = 7
_COL_EFGD  = 8
_COL_TO    = 11
_COL_TOD   = 12
_COL_ORB   = 13
_COL_ADJT  = 15


@dataclass
class TeamStats:
    name: str
    adj_em: float = 0.0     # AdjOE - AdjDE (pts per 100 poss, positive = better)
    adj_o: float = 100.0    # Adjusted Offensive Efficiency
    adj_d: float = 100.0    # Adjusted Defensive Efficiency (lower = better)
    adj_t: float = 68.0     # Adjusted Tempo (possessions per game)
    efg_pct: float = 0.50   # Effective FG% offense (decimal, e.g. 0.547)
    efgd_pct: float = 0.50  # Opponent EFG% allowed (decimal)
    to_pct: float = 0.18    # Turnover rate offense (decimal — lower is better)
    tod_pct: float = 0.18   # Opponent turnover rate forced (decimal — higher is better)
    orb_pct: float = 0.30   # Offensive rebound rate (decimal)
    seed: Optional[int] = None


# ---------------------------------------------------------------------------
# Team name normalization
# ---------------------------------------------------------------------------

# Torvik name → Odds API name (add entries here when a team is skipped)
_NAME_MAP = {
    "Connecticut": "UConn",
    "UConn": "UConn",
    "Miami FL": "Miami (FL)",
    "North Carolina State": "NC State",
    "St. Mary's": "Saint Mary's",
    "Saint Mary's (CA)": "Saint Mary's",
    "Mississippi": "Ole Miss",
    "Louisiana State": "LSU",
    "Southern California": "USC",
    "Texas Christian": "TCU",
    "McNeese": "McNeese State",
    "Pitt": "Pittsburgh",
    "UNC": "North Carolina",
    "Col. of Charleston": "College of Charleston",
    "S.F. Austin": "Stephen F. Austin",
    "Abilene Chr.": "Abilene Christian",
    "UCSB": "UC Santa Barbara",
    "UNCW": "UNC Wilmington",
    "UTSA": "UT San Antonio",
    "UTEP": "UT El Paso",
    "FIU": "Florida International",
    "FAU": "Florida Atlantic",
    "UAB": "UAB",
    "UNLV": "UNLV",
    "BYU": "BYU",
    "TCU": "TCU",
    "SMU": "SMU",
    "UCF": "UCF",
    "USF": "South Florida",
    "VCU": "VCU",
}


def _normalize_name(name: str) -> str:
    """Return a normalized team name compatible with The Odds API."""
    name = name.strip().strip('"')
    if name in _NAME_MAP:
        return _NAME_MAP[name]
    for suffix in [" University", " College", " State University"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val, default: float) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


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
# HTTP fetch with bot-protection bypass
# ---------------------------------------------------------------------------

def _fetch_csv_text() -> str:
    """
    Bypass Torvik's JS verification page.
    Step 1: GET to receive the verification cookie.
    Step 2: POST with js_test_submitted=1 to get the actual CSV.
    """
    session = requests.Session()
    session.headers.update(_HEADERS)

    # Step 1 — prime the session cookie
    session.get(TRANK_URL, timeout=20)

    # Step 2 — submit the JS verification form
    resp = session.post(TRANK_URL, data={"js_test_submitted": "1"}, timeout=20)
    resp.raise_for_status()

    text = resp.text.lstrip("\ufeff").strip()

    # Sanity check: if we still got HTML, the bypass failed
    if text.lstrip().startswith("<"):
        raise RuntimeError(
            "Torvik returned HTML instead of CSV — bot protection bypass failed. "
            "Try again or check if the URL has changed."
        )
    return text


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------

def fetch_stats() -> dict[str, TeamStats]:
    """Fetch Bart Torvik team stats and return dict keyed by normalized team name."""
    print("  Fetching Bart Torvik stats...")
    text = _fetch_csv_text()

    # No header row — use integer column positions
    df = pd.read_csv(
        io.StringIO(text),
        header=None,
        on_bad_lines="skip",
    )

    seed_map = _load_seed_map()
    stats: dict[str, TeamStats] = {}

    for _, row in df.iterrows():
        raw_name = str(row[_COL_TEAM]).strip().strip('"')
        if not raw_name or raw_name.lower() == "nan":
            continue

        norm_name = _normalize_name(raw_name)

        adj_o = _safe_float(row[_COL_ADJO], 100.0)
        adj_d = _safe_float(row[_COL_ADJD], 100.0)
        adj_em = adj_o - adj_d  # efficiency margin in pts per 100 poss

        # EFG%, TO%, OR% are stored as percentages (e.g. 54.7) — convert to decimals
        ts = TeamStats(
            name=norm_name,
            adj_em=adj_em,
            adj_o=adj_o,
            adj_d=adj_d,
            adj_t=_safe_float(row[_COL_ADJT], 68.0),
            efg_pct=_safe_float(row[_COL_EFG], 50.0) / 100.0,
            efgd_pct=_safe_float(row[_COL_EFGD], 50.0) / 100.0,
            to_pct=_safe_float(row[_COL_TO], 18.0) / 100.0,
            tod_pct=_safe_float(row[_COL_TOD], 18.0) / 100.0,
            orb_pct=_safe_float(row[_COL_ORB], 30.0) / 100.0,
            seed=seed_map.get(norm_name),
        )

        stats[norm_name] = ts
        # Also index by raw name as fallback
        if raw_name != norm_name:
            stats[raw_name] = ts

    unique = len(set(id(v) for v in stats.values()))
    print(f"  Loaded {unique} teams from Bart Torvik.")
    return stats


def lookup_team(stats: dict[str, TeamStats], api_name: str) -> Optional[TeamStats]:
    """
    Find TeamStats by The Odds API team name.
    Tries: exact match → normalized match → fuzzy substring match.
    If a team is skipped in main.py output, add its name to _NAME_MAP.
    """
    if api_name in stats:
        return stats[api_name]

    norm = _normalize_name(api_name)
    if norm in stats:
        return stats[norm]

    # Fuzzy: longest key that is a substring of api_name or vice versa
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
    # Show a few known teams to sanity-check values
    for name in ["Duke", "Florida", "Houston", "Auburn", "UConn"]:
        ts = lookup_team(stats, name)
        if ts:
            print(f"{ts.name}: AdjEM={ts.adj_em:.1f}, AdjO={ts.adj_o:.1f}, AdjD={ts.adj_d:.1f}, Tempo={ts.adj_t:.1f}, Seed={ts.seed}")
        else:
            print(f"{name}: NOT FOUND")
