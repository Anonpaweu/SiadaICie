"""
CLI tool for PKP Seat Optimizer — visual verification and optimization.

Usage:
    python -m backend.app.cli --from-file backend/tests/fixtures/koluszki_opole.txt
    python -m backend.app.cli --scenario classic
    python -m backend.app.cli --scenario overlap
    python -m backend.app.cli --scenario standing
"""
import argparse
import asyncio
import io
import sys
import time
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from backend.app.engine.availability_parser import (
    availability_map_to_matrix,
    parse_availability_text,
)
from backend.app.optimizer.solver import OptimizerConfig, parse_profile, solve_top_strategies
from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
from backend.app.scraper.mock_provider import (
    scenario_classic_b_c_block,
    scenario_disconnected_units,
    scenario_overlapping_windows_and_proximity,
    scenario_standing_corridor_fallback,
)

# Force UTF-8 output to avoid Windows cp1250 encoding issues
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
console = Console(force_terminal=True, force_jupyter=False)


SCENARIOS = {
    "classic": {
        "name": "Scenariusz 1: Klasyczny blok B-C (Wagon 10)",
        "loader": scenario_classic_b_c_block,
        "has_config": False,
    },
    "overlap": {
        "name": "Scenariusz 2: Zakładka wagonów 8 -> 6 -> 7",
        "loader": scenario_overlapping_windows_and_proximity,
        "has_config": False,
    },
    "standing": {
        "name": "Scenariusz 3: Stanie na korytarzu (C-D 100% zajęte)",
        "loader": scenario_standing_corridor_fallback,
        "has_config": False,
    },
    "disconnected": {
        "name": "Scenariusz 4: Podwójny skład EZT (brak przejścia 8<->9)",
        "loader": scenario_disconnected_units,
        "has_config": True,
    },
}


def draw_availability_table(
    availability_matrix: dict,
    station_names: list[str],
    chosen_paths: list[dict] | None = None,
) -> Table:
    """Draw the seat availability matrix as a Rich table."""
    num_segments = len(station_names) - 1
    segment_labels = [f"{station_names[i]}-{station_names[i+1]}" for i in range(num_segments)]

    table = Table(
        title="Macierz Dostępności Miejsc",
        box=box.ROUNDED,
        show_lines=True,
    )

    table.add_column("Miejsce", style="bold cyan", justify="center")
    for label in segment_labels:
        table.add_column(label, justify="center", width=12)

    chosen_cells: set[tuple[str, int]] = set()
    if chosen_paths:
        for step in chosen_paths:
            for seg_idx in range(step["from_idx"], step["to_idx"]):
                chosen_cells.add((step["seat_uid"], seg_idx))

    for seat_uid, segments in sorted(
        availability_matrix.items(),
        key=lambda item: (
            int(item[0].split("_")[0]) if item[0].split("_")[0].isdigit() else 99,
            int(item[0].split("_")[1]) if len(item[0].split("_")) > 1 and item[0].split("_")[1].isdigit() else 999,
        ),
    ):
        row_values = []
        for seg_idx, val in enumerate(segments):
            if (seat_uid, seg_idx) in chosen_cells:
                row_values.append("[bold blue on white]WYBRANE[/]")
            elif val == 1:
                row_values.append("[green]WOLNE[/]")
            else:
                row_values.append("[red]ZAJĘTE[/]")
        table.add_row(f"[bold]{seat_uid}[/]", *row_values)

    return table


def draw_strategy_card(strategy, station_names: list[str], rank: int) -> Panel:
    """Draw a single strategy as a Rich panel with purchase instructions."""
    text = Text()

    text.append(f"Strategia #{rank}: ", style="bold yellow")
    text.append(f"{strategy.title}\n", style="bold white")
    text.append(f"   Wynik: {strategy.total_score:.1f} pkt", style="dim")
    text.append(f" | Przesiadki: {strategy.transfers_count}", style="dim")
    text.append(f" | Stanie: {strategy.total_standing_min} min\n", style="dim")
    text.append(f"   Wagony: {strategy.carriages_used}\n\n", style="dim")

    for step in strategy.steps:
        if step.is_standing:
            tag = "[KORYTARZ]"
            style = "bold red"
            desc = f"KORYTARZ ({step.duration_min} min)"
        else:
            tag = "[MIEJSCE]"
            style = "bold green"
            compartment_str = f" (Przedział {step.compartment})" if getattr(step, "compartment", None) else ""
            desc = f"Wagon {step.carriage_no}, Miejsce {step.seat_no}{compartment_str} ({step.duration_min} min)"

        text.append(f"   {tag} ", style=style)
        text.append(f"{step.from_station_name} -> {step.to_station_name}: ", style="bold")
        text.append(f"{desc}\n", style=style)

        if step.transfer_instruction:
            text.append(f"      -> {step.transfer_instruction}\n", style="italic dim")

        if getattr(step, "alternative_seats_in_same_carriage", None):
            alts = ", ".join(f"m{a}" for a in step.alternative_seats_in_same_carriage)
            text.append(f"      -> Inne wolne w tym wagonie: {alts}\n", style="dim cyan")

    instructions = getattr(strategy, "purchase_instructions", [])
    if instructions:
        text.append("\n   Plan Zakupu Biletów i Przesiadek:\n", style="bold yellow")
        for instr in instructions:
            text.append(f"      * {instr}\n", style="yellow")

    return Panel(text, border_style="cyan", expand=True)


def main():
    parser = argparse.ArgumentParser(description="PKP Seat Optimizer — CLI")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        default=None,
        help="Wybierz scenariusz testowy",
    )
    parser.add_argument(
        "--from-file",
        dest="from_file",
        default=None,
        help="Wczytaj plik tekstowy z rozpiską miejsc (np. backend/tests/fixtures/koluszki_opole.txt)",
    )
    parser.add_argument("--from", dest="from_station", default=None, help="Stacja początkowa (tryb live)")
    parser.add_argument("--to", dest="to_station", default=None, help="Stacja końcowa (tryb live)")
    parser.add_argument("--date", default=None, help="Data podróży YYYY-MM-DD (tryb live)")
    parser.add_argument("--train", default="IC 3520", help="Numer pociągu (tryb live)")
    parser.add_argument("--dep-time", default=None, help="Godzina odjazdu HH:MM")
    parser.add_argument("--class", dest="ticket_class", type=int, choices=[1, 2], default=2, help="Klasa wagonu (1 lub 2)")
    parser.add_argument(
        "--profile",
        choices=["BALANCED", "MINIMAL_WALKING", "MINIMAL_TRANSFERS"],
        default="BALANCED",
        help="Profil optymalizacji",
    )

    args = parser.parse_args()
    profile = parse_profile(args.profile)

    if args.from_file:
        file_path = Path(args.from_file)
        if not file_path.exists():
            console.print(f"[bold red]Błąd: Plik nie istnieje: {file_path}[/]")
            sys.exit(1)

        console.print(f"\n[bold magenta]=== Wczytano plik: {file_path} ===[/]\n")
        raw_text = file_path.read_text(encoding="utf-8")
        avail_map = parse_availability_text(raw_text)
        avail, meta, names, durations = availability_map_to_matrix(avail_map)
        config = OptimizerConfig(profile=profile)

    elif args.from_station and args.to_station:
        from_stn = args.from_station.strip()
        to_stn = args.to_station.strip()
        travel_date = args.date or time.strftime("%Y-%m-%d")
        train_no = args.train

        console.print(f"\n[bold magenta]=== Skanowanie na żywo: {from_stn} -> {to_stn} ({train_no}, Klasa {args.ticket_class}) ===[/]\n")
        scanner = LiveTrainScanner(headless=True, on_progress=lambda m: console.print(f"[dim]{m}[/]"))
        avail_map = asyncio.run(
            scanner.scan_full_train(
                from_station=from_stn,
                to_station=to_stn,
                travel_date=travel_date,
                train_number=train_no,
                departure_time=args.dep_time,
                ticket_class=args.ticket_class,
            )
        )
        avail, meta, names, durations = availability_map_to_matrix(avail_map)
        config = OptimizerConfig(profile=profile)

    elif args.scenario:
        scenario = SCENARIOS[args.scenario]
        console.print(f"\n[bold magenta]=== {scenario['name']} ===[/]\n")
        if scenario["has_config"]:
            avail, meta, names, durations, config = scenario["loader"]()
            config.profile = profile
        else:
            avail, meta, names, durations = scenario["loader"]()
            config = OptimizerConfig(profile=profile)
    else:
        scenario = SCENARIOS["classic"]
        console.print(f"\n[bold magenta]=== {scenario['name']} (Domyślny) ===[/]\n")
        avail, meta, names, durations = scenario["loader"]()
        config = OptimizerConfig(profile=profile)

    start = time.perf_counter()
    strategies = solve_top_strategies(avail, meta, names, durations, config=config)
    elapsed = time.perf_counter() - start

    chosen_paths = None
    if strategies:
        chosen_paths = [
            {
                "seat_uid": s.seat_uid,
                "from_idx": s.from_station_idx,
                "to_idx": s.to_station_idx,
            }
            for s in strategies[0].steps
            if not s.is_standing
        ]

    table = draw_availability_table(avail, names, chosen_paths)
    console.print(table)
    console.print()

    console.print("[bold]Top Strategie Podróży:[/]\n")
    for strategy in strategies:
        panel = draw_strategy_card(strategy, names, strategy.rank)
        console.print(panel)

    console.print(f"\n[dim]Czas obliczeń: {elapsed*1000:.1f} ms[/]\n")


if __name__ == "__main__":
    main()
