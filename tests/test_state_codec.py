import pytest

from loadplanner.models import CargoItem, Rules, Zone
from loadplanner.state_codec import decode_state, encode_state
from loadplanner.vehicle_data import default_vehicles


def test_roundtrip_items_rules_and_edited_vehicle():
    items = [CargoItem("SU", "Su paleti", "palet", 4, 120, 80, 150, 650, allow_on_top=False, delivery_stop=2),
             CargoItem("VAZO", "Cam vazo", "koli", 6, 50, 50, 50, 8, fragile=True, max_top_load=None,
                       segregation_group="gıda")]
    rules = Rules(mode="LIFO", allow_mixed_stop_stacking=True, min_support_ratio=0.8, rear_clearance_cm=20,
                  incompatible=(("Gıda", "kimyasal"),), separation_cm=40)
    vehicles = default_vehicles()
    vehicles["ozel"].length = 512
    vehicles["ozel"].zones.append(Zone("engel", 0, 0, 0, 10, 10, 10, True))
    vehicles["kamyon_romork"].trailer.length = 700
    vehicles["kamyon_romork"].trip_cost = 15000
    text = encode_state(items, rules, vehicles, {"panelvan": 2, "ozel": 0})
    assert len(text) < 2500
    items2, rules2, vehicles2, avail = decode_state(text)
    assert items2 == items and rules2 == rules and vehicles2 == vehicles
    assert avail == {"panelvan": 2, "ozel": 0}


def test_old_links_with_unknown_or_missing_fields_still_open():
    import base64, json, zlib
    payload = {"v": 1, "i": [{"code": "A", "name": "A", "quantity": 2, "length": 10, "width": 10, "height": 10,
                              "unit_weight": 5, "eski_alan": 1}], "r": {"mode": "LIFO"}, "vh": {}}
    text = base64.urlsafe_b64encode(zlib.compress(json.dumps(payload).encode())).decode().rstrip("=")
    items, rules, _, avail = decode_state(text)
    assert items[0].code == "A" and items[0].cold_chain is False and rules.mode == "LIFO" and avail == {}


def test_corrupt_text_raises_value_error():
    with pytest.raises(ValueError):
        decode_state("bozuk-veri")
