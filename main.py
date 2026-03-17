"""
main.py — CLI entry point for the March Madness betting model.

Usage:
    python main.py
    python main.py --bankroll 500
    python main.py --sport-key basketball_ncaab_tournament
"""

import argparse
import datetime
import sys

from tabulate import tabulate

import config
from fetch_odds import fetch_odds, GameOdds
from fetch_stats import fetch_stats, lookup_team, TeamStats
from model import predict_game, GamePrediction
from ev_calculator import evaluate_bets, BetRecommendation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_odds(odds: int) -> str:
    if odds is None:
        return "N/A"
    return f"+{odds}" if odds > 0 else str(odds)


def _format_prob(p: float) -> str:
    return f"{p * 100:.1f}%"


def _format_ev(ev: float) -> str:
    sign = "+" if ev >= 0 else ""
    return f"{sign}{ev * 100:.1f}%"


def _print_game_preview(game: GameOdds, pred: GamePrediction):
    print(f"\n  {'─' * 60}")
    print(f"  {game.away_team} @ {game.home_team}")
    print(f"  Predicted margin: {pred.home_team} by {pred.expected_margin:+.1f} pts")
    print(f"  Expected total:   {pred.expected_total:.1f} pts")
    print(f"  Win probs:  {game.home_team} {_format_prob(pred.home_win_prob)}  |  "
          f"{game.away_team} {_format_prob(pred.away_win_prob)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="March Madness Betting Model")
    parser.add_argument("--bankroll", type=float, default=None, help="Override bankroll ($)")
    parser.add_argument("--sport-key", type=str, default=None, help="Override NCAAB sport key")
    parser.add_argument("--min-ev", type=float, default=None, help="Override minimum EV threshold (e.g. 0.03)")
    parser.add_argument("--date", type=str, default=None, help="Date to pull lines for (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--no-prompt", action="store_true", help="Skip interactive prompts")
    parser.add_argument("--verbose", action="store_true", help="Show game previews for all matchups")
    args = parser.parse_args()

    # --- Bankroll ---
    if args.bankroll is not None:
        config.BANKROLL = args.bankroll
    elif not args.no_prompt:
        try:
            user_input = input(f"\nEnter bankroll (default ${config.BANKROLL:.0f}): ").strip()
            if user_input:
                config.BANKROLL = float(user_input)
        except (EOFError, ValueError):
            pass

    # --- Date ---
    game_date = args.date
    if game_date is None and not args.no_prompt:
        today = datetime.date.today().isoformat()
        try:
            user_input = input(f"Enter date to pull lines for (default {today}, format YYYY-MM-DD): ").strip()
            if user_input:
                datetime.date.fromisoformat(user_input)  # validate format
                game_date = user_input
            else:
                game_date = today
        except ValueError:
            print(f"  [warn] Invalid date format — using today ({today}).")
            game_date = today
    elif game_date is None:
        game_date = datetime.date.today().isoformat()

    if args.min_ev is not None:
        config.MIN_EV_THRESHOLD = args.min_ev

    print(f"\n{'='*65}")
    print(f"  MARCH MADNESS BETTING MODEL — {game_date}")
    print(f"  Bankroll: ${config.BANKROLL:,.2f}  |  Kelly: {config.KELLY_FRACTION*100:.0f}%  |  Min EV: {config.MIN_EV_THRESHOLD*100:.0f}%")
    print(f"{'='*65}")

    # --- Fetch odds ---
    print(f"\n[1/3] Fetching FanDuel odds for {game_date}...")
    sport_key = args.sport_key or config.SPORT_KEY
    try:
        games = fetch_odds(sport_key, date=game_date)
    except Exception as e:
        print(f"  ERROR fetching odds: {e}")
        sys.exit(1)

    if not games:
        print(f"  No games found for {game_date}. Try a different date or check the sport key.")
        print("  Tip: Try --sport-key basketball_ncaab_tournament during the tournament.")
        sys.exit(0)

    print(f"  Found {len(games)} game(s) with FanDuel lines.")

    # --- Fetch stats ---
    print("\n[2/3] Fetching Bart Torvik team stats...")
    try:
        stats = fetch_stats()
    except Exception as e:
        print(f"  ERROR fetching stats: {e}")
        sys.exit(1)

    # --- Run model ---
    print(f"\n[3/3] Running model on {len(games)} game(s)...")

    all_bets: list[BetRecommendation] = []
    skipped = []

    for game in games:
        ts_home = lookup_team(stats, game.home_team)
        ts_away = lookup_team(stats, game.away_team)

        if ts_home is None or ts_away is None:
            missing = []
            if ts_home is None:
                missing.append(game.home_team)
            if ts_away is None:
                missing.append(game.away_team)
            skipped.append(f"{game.away_team} @ {game.home_team} (missing stats: {', '.join(missing)})")
            continue

        pred = predict_game(
            ts_home=ts_home,
            ts_away=ts_away,
            book_spread_home=game.odds.home_spread,
            book_total=game.odds.total_line,
        )

        if args.verbose:
            _print_game_preview(game, pred)

        game_label = f"{game.away_team} @ {game.home_team}"
        bets = evaluate_bets(game_label, pred, game)
        all_bets.extend(bets)

    # --- Results ---
    print(f"\n{'='*65}")
    print("  BET RECOMMENDATIONS")
    print(f"{'='*65}")

    if skipped:
        print("\n  [!] Skipped (no stat match):")
        for s in skipped:
            print(f"      {s}")

    if not all_bets:
        print(f"\n  No bets found with EV >= {config.MIN_EV_THRESHOLD*100:.0f}%.")
        print("  The market may be efficient today, or try lowering --min-ev.")
    else:
        # Sort by EV descending
        all_bets.sort(key=lambda b: b.ev_pct, reverse=True)

        table_data = []
        total_action = 0.0
        for b in all_bets:
            table_data.append([
                b.game,
                b.market,
                b.side,
                _format_odds(b.american_odds),
                _format_prob(b.model_prob),
                _format_prob(b.implied_prob),
                _format_ev(b.ev_pct),
                f"${b.bet_size:.2f}",
            ])
            total_action += b.bet_size

        headers = ["Game", "Market", "Side", "Odds", "Model%", "Implied%", "EV%", "Bet ($)"]
        print()
        print(tabulate(table_data, headers=headers, tablefmt="rounded_outline"))
        print(f"\n  Total recommended action: ${total_action:,.2f}")
        print(f"  Bets found: {len(all_bets)}")

    print(f"\n{'='*65}\n")


if __name__ == "__main__":
    main()
