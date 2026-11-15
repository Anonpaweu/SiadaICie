"""
Live Train CLI Test Tool.

Accepts Station A, Station B, travel date, optional train number,
runs real Playwright / cache scanning, prints the formatted availability map ("wX mY/mZ"),
and outputs the optimal seating and transfer strategy.
"""
import argparse
import asyncio
import logging
import sys
from datetime import date

from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
from backend.app.engine.availability_parser import (
    format_availability_map,
    availability_map_to_matrix,
)
from backend.app.optimizer.solver import solve_top_strategies, OptimizerConfig

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("cli_live_test")


async def main_async(args: argparse.Namespace):
    from_stn = args.from_station.strip()
    to_stn = args.to_station.strip()
    travel_date = args.date.strip()
    train_no = args.train.strip()
    dep_time = args.dep_time.strip() if args.dep_time else None
    headless = not args.show_browser

    print("\n" + "=" * 60)
    print("  PKP Intercity Live Seat & Transfer Optimizer")
    print(f"  Trasa: {from_stn} -> {to_stn}")
    print(f"  Data:  {travel_date} | Pociąg: {train_no} | Godz: {dep_time or 'wg rozkładu'}")
    print(f"  Podgląd przeglądarki: {'WŁĄCZONY (Widoczne okno)' if not headless else 'WYŁĄCZONY (Tło)'}")
    print("=" * 60 + "\n")

    def on_progress(msg: str):
        print(f"[SCAN] {msg}")

    scanner = LiveTrainScanner(headless=headless, on_progress=on_progress)

    try:
        avail_map = await scanner.scan_full_train(
            from_station=from_stn,
            to_station=to_stn,
            travel_date=travel_date,
            train_number=train_no,
            departure_time=dep_time,
        )
    except Exception as e:
        print(f"\n[BŁĄD SKANERA] Nie udało się przeprowadzić pełnego skanowania: {e}")
        return 1

    formatted_text = format_availability_map(avail_map)
    print("\n" + "-" * 40)
    print("POBRANA ROZPISKA WOLNYCH MIEJSC:")
    print("-" * 40)
    print(formatted_text if formatted_text else "(Brak wolnych miejsc we wszystkich wagonach)")
    print("-" * 40 + "\n")

    matrix, meta, station_names, segment_durations = availability_map_to_matrix(avail_map)
    if not matrix:
        print("[INFO] Brak dostępnych miejsc do wyznaczenia ścieżki.")
        return 0

    config = OptimizerConfig(prefer_window=args.prefer_window)
    strategies = solve_top_strategies(matrix, meta, station_names, segment_durations, config=config)

    if not strategies:
        print("[INFO] Nie udało się wyznaczyć rekomendacji podróży.")
        return 0

    best = strategies[0]
    print(f"REKOMENDACJA #1: {best.title}")
    print(f"Liczba przesiadek: {best.transfers_count}")
    print(f"Wagony: {best.carriages_used}")
    print("\nKROKI PODRÓŻY:")
    for step in best.steps:
        seat_desc = f"Wagon {step.carriage_no}, Miejsce {step.seat_no}"
        if step.compartment:
            seat_desc += f" (Przedział {step.compartment})"
        print(f"  * {step.from_station_name} -> {step.to_station_name} ({step.duration_min} min): {seat_desc}")
        if step.transfer_Instruction:
            print(f"    -> {step.transfer_Instruction}")

    if best.purchase_instructions:
        print("\nPLAN ZAKUPU BILETÓW:")
        for instr in best.purchase_instructions:
            print(f"  [+] {instr}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Live test for PKP seat scraper and optimizer.")
    parser.add_argument("--from", dest="from_station", default="Koluszki", help="Stacja początkowa")
    parser.add_argument("--to", dest="to_station", default="Opole Główne", help="Stacja końcowa")
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"), help="Data podróży YYYY-MM-DD")
    parser.add_argument("--train", default="IC 16104", help="Numer pociągu (np. IC 16104)")
    parser.add_argument("--dep-time", default=None, help="Godzina odjazdu HH:MM")
    parser.add_argument("--show-browser", action="store_true", help="Otwórz widoczne okno przeglądarki Chromium")
    parser.add_argument("--prefer-window", action="store_true", help="Preferuj miejsca przy oknie")

    args = parser.parse_args()
    code = asyncio.run(main_async(args))
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
