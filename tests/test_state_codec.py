import pytest

from loadplanner.models import CargoItem, Rules, Zone
from loadplanner.state_codec import decode_state, encode_state
from loadplanner.vehicle_data import default_vehicles


def test_roundtrip_items_rules_and_edited_vehicle():
    items = [CargoItem("SU", "Su paleti", "palet", 4, 120, 80, 150, 650, allow_on_top=False, delivery_stop=2),
             CargoItem("VAZO", "Cam vazo", "koli", 6, 50, 50, 50, 8, fragile=True, max_top_load=None)]
    rules = Rules(mode="LIFO", allow_mixed_stop_stacking=True, min_support_ratio=0.8, rear_clearance_cm=20)
    vehicles = default_vehicles()
    vehicles["ozel"].length = 512
    vehicles["ozel"].zones.append(Zone("engel", 0, 0, 0, 10, 10, 10, True))
    text = encode_state(items, rules, vehicles)
    assert len(text) < 2000
    items2, rules2, vehicles2 = decode_state(text)
    assert items2 == items and rules2 == rules and vehicles2 == vehicles


def test_corrupt_text_raises_value_error():
    with pytest.raises(ValueError):
        decode_state("bozuk-veri")
