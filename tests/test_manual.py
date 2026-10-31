from loadplanner.manual import add_unplaced, move_placement, remove_placement, suggest_position, swap_placements
from loadplanner.models import CargoItem, Door, Rules, Vehicle
from loadplanner.optimizer import plan_fleet


def vehicle():
    return Vehicle(id="t", name="Test", category="t", length=300, width=100, height=100,
                   max_payload_kg=5000, doors=[Door("arka")], is_demo=False)


def fleet_two_stops():
    items = [CargoItem("D1", "D1", "koli", 1, 100, 100, 100, 50, allow_on_top=False, delivery_stop=1),
             CargoItem("D2", "D2", "koli", 1, 100, 100, 100, 50, allow_on_top=False, delivery_stop=2)]
    return plan_fleet(vehicle(), items, Rules())


def test_swap_creates_access_violation_and_swap_back_fixes_it():
    rules = Rules()
    f = fleet_two_stops()
    assert f.plans[0].is_valid
    swap_placements(f, 0, "D1#1", "D2#1", rules)
    assert any(v.category == "ERİŞİM" for v in f.plans[0].errors)
    assert f.metrics["error_count"] > 0 and f.metrics["manual_edits"] == 1
    swap_placements(f, 0, "D1#1", "D2#1", rules)
    assert f.plans[0].is_valid and f.metrics["error_count"] == 0


def test_move_out_of_bounds_and_overlap_are_reported():
    rules = Rules()
    f = fleet_two_stops()
    move_placement(f, 0, "D1#1", 250, 0, 0, None, rules)
    cats = {v.category for v in f.plans[0].errors}
    assert "SINIR" in cats
    other = next(p for p in f.plans[0].placements if p.uid == "D2#1")
    move_placement(f, 0, "D1#1", other.x + 10, 0, 0, None, rules)
    assert "ÇAKIŞMA" in {v.category for v in f.plans[0].errors}


def test_remove_then_readd_with_suggestion():
    rules = Rules()
    f = fleet_two_stops()
    remove_placement(f, 0, "D1#1", rules)
    assert len(f.plans[0].placements) == 1 and f.metrics["unplaced_count"] == 1
    u = f.unplaced[0]
    pos = suggest_position(f.plans[0], u.item, rules)
    assert pos is not None
    add_unplaced(f, 0, u.uid, *pos, rules)
    assert len(f.plans[0].placements) == 2 and not f.unplaced and f.plans[0].is_valid


def test_suggest_returns_none_when_full():
    rules = Rules()
    items = [CargoItem("A", "A", "koli", 3, 100, 100, 100, 10)]
    f = plan_fleet(vehicle(), items, Rules())
    extra = CargoItem("B", "B", "koli", 1, 100, 100, 100, 10)
    assert suggest_position(f.plans[0], extra, rules) is None
