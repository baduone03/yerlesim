from pathlib import Path

from loadplanner.io_csv import parse_items_csv
from loadplanner.manual import swap_placements
from loadplanner.models import Rules
from loadplanner.optimizer import plan_fleet
from loadplanner.pdf_report import build_loading_pdf
from loadplanner.vehicle_data import get_default_vehicle

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ornek_yukler.csv"


def test_pdf_is_generated_for_valid_and_edited_plans():
    items, _ = parse_items_csv(EXAMPLE.read_bytes())
    rules = Rules()
    fleet = plan_fleet(get_default_vehicle("buyuk_kamyonet"), items, rules)
    data = build_loading_pdf(fleet, rules)
    assert data[:5] == b"%PDF-" and len(data) > 5000
    swap_placements(fleet, 0, "SND-1#1", "PLT-B#1", rules)
    assert fleet.plans[0].errors
    assert build_loading_pdf(fleet, rules)[:5] == b"%PDF-"


def test_pdf_multi_vehicle_with_unplaced():
    items, _ = parse_items_csv(EXAMPLE.read_bytes())
    fleet = plan_fleet(get_default_vehicle("panelvan"), items, Rules(), max_vehicles=2)
    assert fleet.n_vehicles == 2 and fleet.unplaced
    assert build_loading_pdf(fleet, Rules())[:5] == b"%PDF-"
