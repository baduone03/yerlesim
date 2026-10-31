from pathlib import Path

from loadplanner.io_csv import fleet_placements_csv, items_to_csv, parse_items_csv
from loadplanner.models import CargoItem, Rules
from loadplanner.optimizer import plan_fleet
from loadplanner.vehicle_data import get_default_vehicle

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ornek_yukler.csv"


def test_example_csv_parses():
    items, errors = parse_items_csv(EXAMPLE.read_bytes())
    assert not errors
    assert [i.code for i in items] == ["PLT-A", "PLT-B", "KOL-1", "SND-1"]
    assert items[1].fragile and items[1].max_top_load is None


def test_semicolon_and_decimal_comma():
    text = "kod;adet;boy_cm;en_cm;yukseklik_cm;birim_agirlik_kg\nA;2;60,5;40;40;12,25\n"
    items, errors = parse_items_csv(text)
    assert not errors and items[0].length == 60.5 and items[0].unit_weight == 12.25


def test_bad_rows_and_missing_columns_reported():
    items, errors = parse_items_csv("kod,adet,boy_cm,en_cm,yukseklik_cm,birim_agirlik_kg\nA,iki,10,10,10,5\nB,1,10,10,10,5\n")
    assert [i.code for i in items] == ["B"]
    assert errors and "Satır 2" in errors[0]
    items, errors = parse_items_csv("kod,adet\nA,1\n")
    assert not items and "zorunlu" in errors[0]


def test_roundtrip_and_export():
    items = [CargoItem("A", "Koli A", "koli", 3, 60, 40, 40, 10, max_top_load=50, delivery_stop=2)]
    back, errors = parse_items_csv(items_to_csv(items))
    assert not errors and back == items
    fleet = plan_fleet(get_default_vehicle("orta_kamyonet"), items, Rules())
    csv_text = fleet_placements_csv(fleet)
    assert csv_text.splitlines()[0].startswith("arac_no;arac;kasa;yukleme_sirasi")
    assert len(csv_text.strip().splitlines()) == 4
