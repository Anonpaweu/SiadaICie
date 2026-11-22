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

from backend.app.engine.availability_parser import (
    availability_map_to_matrix,
    format_availability_map,
)
from backend.app.optimizer.solver import OptimizerConfig, parse_profile, solve_top_strategies
from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("cli_live_test")


async def main_async(args: argparse.Namespace):
    from_stn = args.from_station.strip()
    to_stn = args.to_station.strip()
    travel_date = args.date.strip()
    train_no = args.train.strip()
    dep_time = args.dep_time.strip() if args.dep_time else None

    print("\n" + "=" * 60)
    print("  PKP Seat Optimizer - Live Scanner")
    print(f"  Trasa: {from_stn} -> {to_stn}")
    print(f"  Data:  {travel_date} | Pociąg: {train_no} | Klasa: {args.ticket_class} | Godz: {dep_time or 'wg rozkładu'}")
    print("=" * 60 + "\n")

    def on_progress(msg: str):
        print(f"[SCAN] {msg}")

    scanner = LiveTrainScanner(headless=True, on_progress=on_progress)

    try:
        avail_map = await scanner.scan_full_train(
            from_station=from_stn,
            to_station=to_stn,
            travel_date=travel_date,
            train_number=train_no,
            departure_time=dep_time,
            ticket_class=args.ticket_class,
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

    config = OptimizerConfig(profile=parse_profile(args.profile))
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
        if step.transfer_instruction:
            print(f"    -> {step.transfer_instruction}")

    if best.purchase_instructions:
        print("\nPLAN ZAKUPU BILETÓW:")
        for instr in best.purchase_instructions:
            print(f"  [+] {instr}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Live test for PKP Seat Optimizer.")
    parser.add_argument("--from", dest="from_station", default="Koluszki", help="Stacja początkowa")
    parser.add_argument("--to", dest="to_station", default="Opole Główne", help="Stacja końcowa")
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"), help="Data podróży YYYY-MM-DD")
    parser.add_argument("--train", default="IC 16104", help="Numer pociągu (np. 16104)")
    parser.add_argument("--dep-time", default=None, help="Godzina odjazdu HH:MM")
    parser.add_argument("--class", dest="ticket_class", type=int, choices=[1, 2], default=2, help="Klasa wagonu (1 lub 2)")
    parser.add_argument(
        "--profile",
        choices=["BALANCED", "MINIMAL_WALKING", "MINIMAL_TRANSFERS"],
        default="BALANCED",
        help="Profil optymalizacji",
    )

    args = parser.parse_args()
    code = asyncio.run(main_async(args))
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
