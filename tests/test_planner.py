import pytest

from loadplanner.constraints import rule_conflicts, verify_plan
from loadplanner.models import CargoItem, Door, Placement, Rules, Vehicle, Zone
from loadplanner.optimizer import compare_vehicles, expand_units, plan_fleet, plan_single_vehicle
from loadplanner.validation import validate_item, validate_items


def box_vehicle(L=400, W=200, H=200, payload=3000, doors=None, zones=None):
    return Vehicle(id="t", name="Test aracı", category="test", length=L, width=W, height=H,
                   max_payload_kg=payload, doors=doors or [Door("arka")], zones=zones or [], is_demo=False)


def item(code, qty=1, L=100, W=100, H=100, kg=50, **kw):
    return CargoItem(code=code, name=code, quantity=qty, length=L, width=W, height=H, unit_weight=kg, **kw)


def plan(vehicle, items, rules=None):
    return plan_single_vehicle(vehicle, expand_units(items), rules or Rules())


def assert_physically_valid(p):
    cats = {v.category for v in p.errors}
    assert not cats & {"SINIR", "ÇAKIŞMA", "YASAK BÖLGE", "DESTEK", "İSTİF", "İSTİF DAYANIMI", "AĞIRLIK"}, p.errors


def test_single_box_placed():
    p = plan(box_vehicle(), [item("K", L=60, W=40, H=40, kg=10, cargo_type="koli")])
    assert len(p.placements) == 1 and not p.unplaced
    pl = p.placements[0]
    assert (pl.x, pl.y, pl.z) == (0, 0, 0)
    assert p.is_valid


def test_multiple_pallets_no_overlap():
    v = box_vehicle(L=800, W=245, H=250, payload=10000)
    p = plan(v, [item("P", qty=12, L=120, W=80, H=140, kg=400, cargo_type="palet", allow_on_top=False)])
    assert len(p.placements) == 12
    assert_physically_valid(p)
    # Bağımsız çakışma kontrolü
    pls = p.placements
    for i in range(len(pls)):
        for j in range(i + 1, len(pls)):
            a, b = pls[i], pls[j]
            sep = a.x1 <= b.x or b.x1 <= a.x or a.y1 <= b.y or b.y1 <= a.y or a.z1 <= b.z or b.z1 <= a.z
            assert sep, (a.uid, b.uid)


def test_item_exceeding_vehicle_volume_is_reported_as_single():
    p = plan(box_vehicle(), [item("BUYUK", L=500, W=150, H=150, rotatable=False)])
    assert not p.placements
    assert p.unplaced[0].scope == "tekil"
    assert "sığmıyor" in p.unplaced[0].reason


def test_weight_capacity_exceeded():
    v = box_vehicle(payload=1000)
    p = plan(v, [item("A", qty=3, L=50, W=50, H=50, kg=400)])
    assert len(p.placements) == 2
    assert len(p.unplaced) == 1 and p.unplaced[0].scope == "birlikte"
    assert "ağırlık" in p.unplaced[0].reason.lower()
    assert sum(x.weight for x in p.placements) <= 1000

    heavy = plan(v, [item("H", kg=1500)])
    assert heavy.unplaced[0].scope == "tekil"


def test_no_stacking_on_non_stackable_or_fragile():
    # Taban alanına yalnız bir yük sığıyor; ikincisi ancak üstte olabilir.
    v = box_vehicle(L=100, W=100, H=200)
    for kw in ({"allow_on_top": False}, {"fragile": True}):
        p = plan(v, [item("A", qty=2, **kw)])
        assert len(p.placements) == 1, kw
        assert "istif" in p.unplaced[0].reason.lower()

    ok = plan(v, [item("A", qty=2, max_top_load=100)])
    assert len(ok.placements) == 2 and ok.is_valid

    limited = plan(v, [item("A", qty=2, max_top_load=10)])
    assert len(limited.placements) == 1


def test_verify_detects_manual_stacking_violation():
    v = box_vehicle(L=100, W=100, H=200)
    frag = item("F", fragile=True)
    top = item("T")
    pls = [Placement("F#1", frag, 0, 0, 0, 100, 100, 100, "", 0),
           Placement("T#1", top, 0, 0, 100, 100, 100, 100, "", 0)]
    viol, _ = verify_plan(v, pls, Rules())
    assert any(x.category == "İSTİF" and x.severity == "hata" for x in viol)


def test_verify_detects_overlap_and_out_of_bounds():
    v = box_vehicle()
    a = item("A")
    pls = [Placement("A#1", a, 0, 0, 0, 100, 100, 100, "", 0),
           Placement("A#2", a, 50, 50, 0, 100, 100, 100, "", 0),
           Placement("A#3", a, 350, 0, 0, 100, 100, 100, "", 0)]
    cats = [x.category for x in verify_plan(v, pls, Rules())[0]]
    assert "ÇAKIŞMA" in cats and "SINIR" in cats


def test_delivery_order_access_and_violation_detection():
    v = box_vehicle(L=200, W=100, H=100)
    first = item("D1", delivery_stop=1, allow_on_top=False)
    second = item("D2", delivery_stop=2, allow_on_top=False)
    p = plan(v, [first, second])
    assert p.is_valid, p.errors
    by = {x.item.code: x for x in p.placements}
    # Arka kapı X=L tarafında: ilk teslim edilecek yük kapıya yakın olmalı.
    assert by["D1"].x > by["D2"].x
    assert by["D1"].unload_step < by["D2"].unload_step
    assert by["D2"].load_step < by["D1"].load_step

    # Ters yerleşim elle verilirse erişim ihlali tespit edilmeli.
    bad = [Placement("D1#1", first, 0, 0, 0, 100, 100, 100, "", 0),
           Placement("D2#1", second, 100, 0, 0, 100, 100, 100, "", 0)]
    viol, _ = verify_plan(v, bad, Rules())
    acc = [x for x in viol if x.category == "ERİŞİM"]
    assert acc and "D2#1" in acc[0].uids


def test_side_by_side_different_stops_ok():
    v = box_vehicle(L=100, W=200, H=100)
    pls = [Placement("D1#1", item("D1", delivery_stop=1), 0, 0, 0, 100, 100, 100, "", 0),
           Placement("D2#1", item("D2", delivery_stop=2), 0, 100, 0, 100, 100, 100, "", 0)]
    viol, _ = verify_plan(v, pls, Rules())
    assert not [x for x in viol if x.category == "ERİŞİM"]


def test_side_door_gives_access():
    v = box_vehicle(L=200, W=100, H=100, doors=[Door("arka"), Door("yan_sag", x_start=0, x_end=100)])
    pls = [Placement("D1#1", item("D1", delivery_stop=1), 0, 0, 0, 100, 100, 100, "", 0),
           Placement("D2#1", item("D2", delivery_stop=2), 100, 0, 0, 100, 100, 100, "", 0)]
    viol, _ = verify_plan(v, pls, Rules())
    assert not [x for x in viol if x.category == "ERİŞİM"]


def test_fifo_lifo_violation_and_conflicts():
    v = box_vehicle(L=200, W=100, H=100)
    early = item("E", loading_priority=1, allow_on_top=False)
    late = item("L", loading_priority=2, allow_on_top=False)
    # LIFO: ilk yüklenen (E) en içte olmalı -> geçerli plan
    p = plan(v, [early, late], Rules(mode="LIFO"))
    assert p.is_valid and len(p.placements) == 2
    by = {x.item.code: x for x in p.placements}
    assert by["E"].x < by["L"].x

    # FIFO: tek arka kapılı, tek şeritlik araçta iki farklı öncelik birbirinin önüne geçer
    pls = [Placement("E#1", early, 0, 0, 0, 100, 100, 100, "", 0),
           Placement("L#1", late, 100, 0, 0, 100, 100, 100, "", 0)]
    viol, _ = verify_plan(v, pls, Rules(mode="FIFO"))
    assert any(x.category == "SIRA (FIFO)" for x in viol)
    pf = plan(v, [early, late], Rules(mode="FIFO"))
    assert len(pf.placements) == 1 and pf.is_valid
    assert "erişim" in pf.unplaced[0].reason.lower()
    assert any(c.category == "KURAL ÇELİŞKİSİ" for c in rule_conflicts([early, late], Rules(mode="FIFO"), v))

    # FIFO yan yana şeritlerle mümkün
    wide = box_vehicle(L=200, W=200, H=100)
    pw = plan(wide, [item("E", qty=2, loading_priority=1, allow_on_top=False),
                     item("L", qty=2, loading_priority=2, allow_on_top=False)], Rules(mode="FIFO"))
    assert len(pw.placements) == 4 and pw.is_valid, (pw.errors, pw.unplaced)

    # LIFO ile teslimat sırası çelişkisi bildirilmeli
    a = item("A", loading_priority=2, delivery_stop=2)
    b = item("B", loading_priority=1, delivery_stop=1)
    assert any(c.category == "KURAL ÇELİŞKİSİ" for c in rule_conflicts([a, b], Rules(mode="LIFO")))


def test_multi_stop_plan_is_accessible():
    v = box_vehicle(L=600, W=240, H=240, payload=8000)
    items = [item(f"S{s}", qty=4, L=120, W=80, H=110, kg=300, cargo_type="palet",
                  delivery_stop=s, max_top_load=300) for s in (1, 2, 3)]
    p = plan(v, items)
    assert len(p.placements) == 12 and p.is_valid, p.errors
    seq = sorted(p.placements, key=lambda x: x.unload_step)
    assert [x.item.delivery_stop for x in seq] == sorted(x.item.delivery_stop for x in seq)


def test_rotation_makes_item_fit():
    v = box_vehicle(L=100, W=200, H=100)
    rot = plan(v, [item("R", L=200, W=100, H=50, rotatable=True)])
    assert len(rot.placements) == 1
    pl = rot.placements[0]
    assert (pl.dx, pl.dy) == (100, 200) and "90" in pl.orientation
    norot = plan(v, [item("R", L=200, W=100, H=50, rotatable=False)])
    assert not norot.placements and norot.unplaced[0].scope == "tekil"


def test_combination_that_cannot_travel_together():
    # Her biri tek başına sığar, ikisi birlikte sığmaz.
    v = box_vehicle(L=300, W=200, H=200)
    p = plan(v, [item("A", L=200, W=200, H=200, kg=100), item("B", L=200, W=200, H=200, kg=100)])
    assert len(p.placements) == 1
    assert p.unplaced[0].scope == "birlikte"
    fleet = plan_fleet(v, [item("A", L=200, W=200, H=200, kg=100), item("B", L=200, W=200, H=200, kg=100)], Rules())
    assert fleet.n_vehicles == 2 and not fleet.unplaced


def test_forbidden_zone_respected():
    v = box_vehicle(L=200, W=100, H=100, zones=[Zone("engel", 0, 0, 0, 100, 100, 100, True)],
                    doors=[Door("arka")])
    p = plan(v, [item("A", qty=2)])
    assert len(p.placements) == 1 and p.placements[0].x == 100
    assert p.is_valid


def test_empty_list_and_invalid_input():
    errors, _ = validate_items([])
    assert errors and "boş" in errors[0]
    bad = CargoItem(code="X", name="X", quantity=0, length=-5, width=0, height=10, unit_weight=-1)
    e, _ = validate_item(bad)
    text = " ".join(e)
    assert "adet" in text and "negatif" in text and "sıfır" in text and "ağırlık" in text
    e2, _ = validate_item(CargoItem(code="Y", name="Y", quantity=1.5, length=10, width=10, height=10, unit_weight=1))
    assert any("tam sayı" in x for x in e2)


def test_compare_vehicles_picks_feasible_smallest():
    small = box_vehicle(L=100, W=100, H=100, payload=500)
    small.id, small.name = "kucuk", "Küçük"
    big = box_vehicle(L=400, W=200, H=200, payload=3000)
    big.id, big.name = "buyuk", "Büyük"
    huge = box_vehicle(L=1000, W=250, H=250, payload=20000)
    huge.id, huge.name = "dev", "Dev"
    items = [item("A", qty=4, L=100, W=100, H=100, kg=200, allow_on_top=False)]
    res = compare_vehicles([small, big, huge], items, Rules(), objective="min_arac")
    assert res[0].vehicle.id == "buyuk" and res[0].n_vehicles == 1
    assert res[0].explanation


@pytest.mark.parametrize("vid", ["panelvan", "orta_kamyonet", "yari_romork"])
def test_demo_vehicles_produce_valid_plans(vid):
    from loadplanner.vehicle_data import get_default_vehicle

    v = get_default_vehicle(vid)
    items = [item("P", qty=3, L=120, W=80, H=100, kg=200, cargo_type="palet", delivery_stop=2, max_top_load=200),
             item("K", qty=10, L=40, W=30, H=30, kg=8, delivery_stop=1, max_top_load=40)]
    f = plan_fleet(v, items, Rules())
    for p in f.plans:
        assert p.is_valid, (vid, p.errors)


def test_top_load_limit_propagates_down_the_stack():
    # Alttaki yük en fazla 60 kg taşıyabilir: üstüne 2 adet 25 kg'lık yük (50 kg) olur, 3. olmaz.
    v = box_vehicle(L=50, W=50, H=500)
    base = item("ALT", L=50, W=50, H=50, kg=100, max_top_load=60)
    top = item("UST", qty=3, L=50, W=50, H=50, kg=25, max_top_load=1000)
    p = plan(v, [base, top])
    assert len(p.placements) == 3 and p.is_valid
    assert len(p.unplaced) == 1 and "istif" in p.unplaced[0].reason.lower()


def test_segregation_groups_not_stacked_and_separated():
    v = box_vehicle(L=400, W=100, H=200, payload=5000)
    food = item("GIDA", qty=2, L=100, W=100, H=50, kg=20, segregation_group="Gıda", max_top_load=500)
    chem = item("DET", qty=2, L=100, W=100, H=50, kg=20, segregation_group="kimyasal", max_top_load=500)
    rules = Rules(incompatible=(("gıda", "kimyasal"),), separation_cm=50)
    p = plan(v, [food, chem], rules)
    assert len(p.placements) == 4 and p.is_valid, (p.errors, p.unplaced)
    f = [x for x in p.placements if x.item.code == "GIDA"]
    c = [x for x in p.placements if x.item.code == "DET"]
    gap = min(max(b.x - a.x1, a.x - b.x1) for a in f for b in c)
    assert gap >= 50

    # Mesafe sağlanamıyorsa yük yerleşmez ve nedeni ayrım kuralıdır.
    short = box_vehicle(L=220, W=100, H=200, payload=5000)
    p2 = plan(short, [item("GIDA", L=100, W=100, H=50, segregation_group="gıda"),
                      item("DET", L=100, W=100, H=50, segregation_group="kimyasal")], rules)
    assert len(p2.placements) == 1 and "ayrım" in p2.unplaced[0].reason.lower()

    # Elle verilen ihlal doğrulamada yakalanır (üst üste).
    stacked = [Placement("G#1", food, 0, 0, 0, 100, 100, 50, "", 0),
               Placement("D#1", chem, 0, 0, 50, 100, 100, 50, "", 0)]
    assert any(x.category == "AYRIM" for x in verify_plan(v, stacked, Rules(incompatible=(("gıda", "kimyasal"),)))[0])
