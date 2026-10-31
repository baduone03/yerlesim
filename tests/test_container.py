"""Konteyner (FCL/LCL) ve VGM testleri."""

import pytest

from loadplanner.constraints import verify_plan
from loadplanner.container import NO_SHIPPER, lcl_breakdown, shipper_split_notes, vgm_summary
from loadplanner.models import CargoItem, Placement, Rules
from loadplanner.optimizer import compare_vehicles, expand_units, plan_fleet, plan_single_vehicle
from loadplanner.state_codec import decode_extra, decode_state, encode_state
from loadplanner.validation import validate_vehicle
from loadplanner.vehicle_data import default_vehicles


def containers():
    return [v for v in default_vehicles().values() if v.is_container]


def item(code, qty=1, L=100, W=100, H=100, kg=50, **kw):
    return CargoItem(code=code, name=code, quantity=qty, length=L, width=W, height=H, unit_weight=kg, **kw)


def test_container_profiles_are_valid():
    cs = containers()
    assert {v.id for v in cs} == {"k20dc", "k40dc", "k40hc", "k45hc", "k20rf", "k40rf"}
    for v in cs:
        assert not validate_vehicle(v), v.id
        assert v.max_payload_kg == pytest.approx(v.max_gross_kg - v.tare_kg)
        assert [d.kind for d in v.doors] == ["arka"]
    assert {v.id for v in cs if v.refrigerated} == {"k20rf", "k40rf"}


def test_fcl_picks_smallest_fitting_container():
    small = [item("P", qty=10, L=120, W=80, H=120, kg=500, allow_on_top=False)]
    res = compare_vehicles(containers(), small, Rules(), availability={v.id: 2 for v in containers()})
    assert res[0].vehicle.id == "k20dc" and res[0].n_vehicles == 1
    # 250 cm yükseklik DC kapısından (228) geçmez; HC gerekir.
    tall = [item("T", qty=4, L=120, W=100, H=250, kg=300, allow_on_top=False)]
    res2 = compare_vehicles(containers(), tall, Rules(), availability={v.id: 2 for v in containers()})
    assert res2[0].vehicle.id in ("k40hc", "k45hc") and res2[0].metrics["unplaced_count"] == 0


def test_reefer_not_recommended_for_dry_cargo_but_used_for_cold():
    dry = [item("D", qty=30, L=120, W=80, H=120, kg=300, allow_on_top=False)]
    res = compare_vehicles(containers(), dry, Rules(), availability={v.id: 2 for v in containers()})
    assert not any(p.vehicle.refrigerated for p in res[0].plans)
    cold = [item("C", qty=10, L=120, W=80, H=120, kg=300, allow_on_top=False, cold_chain=True)]
    res2 = compare_vehicles(containers(), cold, Rules(), availability={v.id: 2 for v in containers()})
    assert all(p.vehicle.refrigerated for p in res2[0].plans) and res2[0].metrics["unplaced_count"] == 0


def test_shipper_rule_no_cross_stacking_and_contiguous_blocks():
    v = default_vehicles()["k20dc"]
    items = [item("A", qty=6, L=60, W=40, H=40, kg=10, max_top_load=100, shipper="Aslan"),
             item("B", qty=6, L=60, W=40, H=40, kg=10, max_top_load=100, shipper="Bora")]
    rules = Rules(keep_shipper_together=True)
    p = plan_single_vehicle(v, expand_units(items), rules)
    assert len(p.placements) == 12 and p.is_valid
    # Üst üste duran yükler aynı göndericiye ait
    for a in p.placements:
        for b in p.placements:
            if abs(a.z - b.z1) < 1e-6 and a.x < b.x1 and b.x < a.x1 and a.y < b.y1 and b.y < a.y1:
                assert a.item.shipper == b.item.shipper
    # Bir arada: her göndericinin yükleri birbirine temas eden tek bir grup oluşturur.
    def touching(a, b):
        sep = [max(b.x - a.x1, a.x - b.x1), max(b.y - a.y1, a.y - b.y1), max(b.z - a.z1, a.z - b.z1)]
        return max(sep) <= 1e-6 and sorted(sep)[1] < -1e-6  # en az bir yüzey teması
    for name in ("Aslan", "Bora"):
        group = [q for q in p.placements if q.item.shipper == name]
        seen, todo = {group[0].uid}, [group[0]]
        while todo:
            cur = todo.pop()
            for q in group:
                if q.uid not in seen and touching(cur, q):
                    seen.add(q.uid)
                    todo.append(q)
        assert len(seen) == len(group), name
    # Elle verilen çapraz istif doğrulamada yakalanır
    a, b = items
    pls = [Placement("A#1", a, 0, 0, 0, 60, 40, 40, "", 0), Placement("B#1", b, 0, 0, 40, 60, 40, 40, "", 0)]
    assert any(x.category == "GÖNDERİCİ" for x in verify_plan(v, pls, rules)[0])
    assert not any(x.category == "GÖNDERİCİ" for x in verify_plan(v, pls, Rules())[0])


def test_lcl_breakdown_wm_and_minimum():
    items = [item("A", qty=10, L=100, W=100, H=50, kg=100, shipper="Aslan"),   # 5 m³, 1 t -> 5 W/M
             item("B", qty=2, L=50, W=50, H=50, kg=1500, shipper="Bora"),       # 0,25 m³, 3 t -> 3 W/M
             item("C", qty=1, L=50, W=50, H=40, kg=50, shipper="Ceren"),        # 0,1 m³, 0,05 t -> asgari 1
             item("D", qty=1, L=50, W=50, H=40, kg=50)]                         # gönderici yok
    rows = {r.shipper: r for r in lcl_breakdown(items, None, rate_per_wm=40.0, min_wm=1.0)}
    assert rows["Aslan"].wm == pytest.approx(5.0) and rows["Aslan"].charge == pytest.approx(200.0)
    assert rows["Bora"].wm == pytest.approx(3.0)
    assert rows["Ceren"].wm == pytest.approx(1.0) and rows["Ceren"].charge == pytest.approx(40.0)
    assert NO_SHIPPER in rows
    assert all(r.charge is None for r in lcl_breakdown(items, None, rate_per_wm=None))


def test_vgm_summary_and_shipper_split():
    v = default_vehicles()["k20dc"]
    items = [item("A", qty=20, L=120, W=80, H=200, kg=800, allow_on_top=False, shipper="Aslan")]
    fleet = plan_fleet(v, items, Rules(), max_vehicles=3)
    assert fleet.n_vehicles == 2 and fleet.metrics["unplaced_count"] == 0
    rows = vgm_summary(fleet)
    assert len(rows) == 2
    for r, p in zip(rows, fleet.plans):
        assert r.gross_kg == pytest.approx(2200 + sum(q.weight for q in p.placements)) and r.ok
    assert shipper_split_notes(fleet) and any("Aslan" in n for n in fleet.notes)
    # Karayolu aracında VGM satırı yok
    assert vgm_summary(plan_fleet(default_vehicles()["on_teker"], items[:1], Rules())) == []


def test_container_has_no_axle_info_but_road_vehicle_does():
    items = [item("A", qty=2, kg=500)]
    cont = plan_single_vehicle(default_vehicles()["k20dc"], expand_units(items), Rules())
    road = plan_single_vehicle(default_vehicles()["on_teker"], expand_units(items), Rules())
    assert not any(x.category == "AKS" for x in cont.violations)
    assert any(x.category == "AKS" for x in road.violations)


def test_extra_preferences_roundtrip():
    text = encode_state([item("A", shipper="Aslan")], Rules(keep_shipper_together=True), default_vehicles(),
                        {"k20dc": 2}, {"tm": "lcl", "lcl": {"rate": 45.0, "currency": "EUR", "min_wm": 1.0}})
    items, rules, _, avail = decode_state(text)
    assert items[0].shipper == "Aslan" and rules.keep_shipper_together and avail == {"k20dc": 2}
    assert decode_extra(text) == {"tm": "lcl", "lcl": {"rate": 45.0, "currency": "EUR", "min_wm": 1.0}}
    assert decode_extra("bozuk") == {}
