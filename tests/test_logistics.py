"""Lojistik gerçekliğine yönelik testler: kasa tipleri, soğuk zincir, karma filo, römork, LDM, sabitleme."""

import pytest

from loadplanner.constraints import verify_plan
from loadplanner.models import CargoItem, Door, Placement, Rules, Vehicle, doors_for_body
from loadplanner.optimizer import compare_vehicles, expand_units, plan_fleet, plan_single_vehicle
from loadplanner.vehicle_data import default_vehicles, get_default_vehicle


def veh(L=400, W=200, H=200, payload=5000, body="kapali", **kw):
    return Vehicle(id=kw.pop("id", "t"), name=kw.pop("name", "Test"), category="t", length=L, width=W, height=H,
                   max_payload_kg=payload, doors=doors_for_body(body, L), body_type=body, is_demo=False, **kw)


def item(code, qty=1, L=100, W=100, H=100, kg=50, **kw):
    return CargoItem(code=code, name=code, quantity=qty, length=L, width=W, height=H, unit_weight=kg, **kw)


def test_curtainsider_allows_side_access_that_box_body_does_not():
    first = item("D1", delivery_stop=1, allow_on_top=False)
    second = item("D2", delivery_stop=2, allow_on_top=False)
    # 1. durağın yükü içeride, 2. durağınki kapı önünde: arka kapıdan ihlal, tenteliden yandan erişilir.
    pls = [Placement("D1#1", first, 0, 0, 0, 100, 100, 100, "", 0),
           Placement("D2#1", second, 100, 0, 0, 100, 100, 100, "", 0)]
    box = veh(L=200, W=100, H=100, body="kapali")
    curtain = veh(L=200, W=100, H=100, body="tenteli")
    assert any(v.category == "ERİŞİM" for v in verify_plan(box, pls, Rules())[0])
    assert not any(v.category == "ERİŞİM" for v in verify_plan(curtain, pls, Rules())[0])


def test_curtainsider_middle_pallet_still_blocked_from_sides():
    # 3 sıra yan yana: ortadaki yük iki yandan da kapalı, arkadan da kapalıysa erişilemez.
    a = item("A", delivery_stop=1, allow_on_top=False)
    b = item("B", delivery_stop=2, allow_on_top=False)
    pls = [Placement("A#1", a, 0, 100, 0, 100, 100, 100, "", 0),          # ortada, en içte (1. durak)
           Placement("B#1", b, 0, 0, 0, 100, 100, 100, "", 0),            # sol
           Placement("B#2", b, 0, 200, 0, 100, 100, 100, "", 0),          # sağ
           Placement("B#3", b, 100, 100, 0, 100, 100, 100, "", 0)]        # arkasında
    curtain = veh(L=200, W=300, H=100, body="tenteli")
    assert any(v.category == "ERİŞİM" for v in verify_plan(curtain, pls, Rules())[0])


def test_cold_chain_only_in_refrigerated_vehicle():
    cold = item("ET", cold_chain=True)
    p = plan_single_vehicle(veh(body="kapali"), expand_units([cold]), Rules())
    assert not p.placements and "frigorifik" in p.unplaced[0].reason and p.unplaced[0].scope == "tekil"
    p2 = plan_single_vehicle(veh(body="frigorifik"), expand_units([cold]), Rules())
    assert len(p2.placements) == 1 and p2.is_valid
    viol, _ = verify_plan(veh(body="kapali"), [Placement("ET#1", cold, 0, 0, 0, 100, 100, 100, "", 0)], Rules())
    assert any(v.category == "SOĞUK ZİNCİR" for v in viol)


def test_cold_items_go_to_frigo_in_mixed_fleet():
    v = default_vehicles()
    items = [item("ET", qty=4, L=120, W=80, H=150, kg=500, cold_chain=True, allow_on_top=False),
             item("KURU", qty=4, L=120, W=80, H=150, kg=500, allow_on_top=False)]
    vl = [v["frigo_dorse"], v["orta_kamyonet"]]
    res = compare_vehicles(vl, items, Rules(), availability={"frigo_dorse": 1, "orta_kamyonet": 2})
    best = res[0]
    assert best.metrics["unplaced_count"] == 0 and best.metrics["error_count"] == 0
    for plan in best.plans:
        for q in plan.placements:
            if q.item.cold_chain:
                assert plan.vehicle.refrigerated


def test_default_top_load_prevents_pallet_on_carton():
    v = veh(L=100, W=100, H=300)
    carton = item("KOLI", L=100, W=100, H=50, kg=20)            # üst yük sınırı girilmedi
    pallet = item("PALET", L=100, W=100, H=100, kg=650, cargo_type="palet")
    p = plan_single_vehicle(v, expand_units([carton, pallet]), Rules())
    by = {q.item.code: q for q in p.placements}
    # Ağır palet koli üstüne konamaz; koli palet üstüne konabilir (palet kendi ağırlığı kadar taşır).
    assert by["PALET"].z == 0 and by["KOLI"].z == 100 and p.is_valid
    same = plan_single_vehicle(v, expand_units([item("K", qty=2, L=100, W=100, H=50, kg=20)]), Rules())
    assert len(same.placements) == 2  # aynı yükten biri diğerinin üstüne konabilir
    heavy = [Placement("K#1", carton, 0, 0, 0, 100, 100, 50, "", 0),
             Placement("P#1", pallet, 0, 0, 50, 100, 100, 100, "", 0)]
    assert any(x.category == "İSTİF DAYANIMI" for x in verify_plan(v, heavy, Rules())[0])


def test_truck_and_trailer_counts_as_one_vehicle():
    v = get_default_vehicle("kamyon_romork")
    items = [item("P", qty=30, L=120, W=80, H=150, kg=600, allow_on_top=False, cargo_type="palet")]
    f = plan_fleet(v, items, Rules(), max_vehicles=2)
    assert f.n_vehicles == 1 and len(f.plans) == 2
    assert {p.vehicle.body_label for p in f.plans} == {"Kamyon kasası", "Römork"}
    assert all(p.unit_no == 1 for p in f.plans) and f.metrics["unplaced_count"] == 0
    assert all(p.is_valid for p in f.plans)
    # Kamyon kasası arka kapısız: yükler yandan erişilebilir olmalı (tenteli).
    truck = next(p for p in f.plans if p.vehicle.body_label == "Kamyon kasası")
    assert truck.placements and not any(x.category in ("KAPI", "ERİŞİM") for x in truck.violations)


def test_min_cost_picks_cheapest_complete_option():
    small = veh(L=300, W=200, H=200, payload=3000, id="kucuk", name="Küçük", trip_cost=4000)
    big = veh(L=900, W=245, H=250, payload=15000, id="buyuk", name="Büyük", trip_cost=9000)
    items = [item("A", qty=10, L=100, W=100, H=100, kg=200, allow_on_top=False)]
    # 10 yük: büyük araç 1 sefer (9000) / küçük araç 2 sefer (8000) / ya da karma
    res = compare_vehicles([small, big], items, Rules(), objective="min_maliyet",
                           availability={"kucuk": 3, "buyuk": 1})
    best = res[0]
    assert best.metrics["unplaced_count"] == 0
    assert best.metrics["total_cost"] == min(f.metrics["total_cost"] for f in res if f.metrics["unplaced_count"] == 0)
    assert best.metrics["total_cost"] == 8000
    res2 = compare_vehicles([small, big], items, Rules(), objective="min_arac", availability={"kucuk": 3, "buyuk": 1})
    assert res2[0].n_vehicles == 1 and res2[0].vehicle.id == "buyuk"


def test_mixed_fleet_when_one_type_is_not_enough():
    trailer = veh(L=1360, W=248, H=270, payload=24000, id="tir", name="Tır")
    van = veh(L=420, W=205, H=210, payload=3500, id="kamyonet", name="Kamyonet")
    items = [item("E", qty=36, L=120, W=80, H=150, kg=450, allow_on_top=False, cargo_type="palet")]
    res = compare_vehicles([trailer, van], items, Rules(), availability={"tir": 1, "kamyonet": 3})
    best = res[0]
    assert best.mixed and best.n_vehicles == 2 and best.metrics["unplaced_count"] == 0
    assert "Tır" in best.label and "Kamyonet" in best.label


def test_ldm_metric():
    v = veh(L=1360, W=248, H=270, payload=24000)
    p = plan_single_vehicle(v, expand_units([item("E", qty=3, L=120, W=80, H=100, kg=300, allow_on_top=False)]), Rules())
    assert p.metrics["ldm"] == pytest.approx(3 * 120 * 80 / 240 / 100)


def test_center_partial_load_moves_weight_toward_middle():
    v = veh(L=1000, W=240, H=250, payload=20000)
    items = [item("AGIR", qty=6, L=120, W=80, H=100, kg=2000, allow_on_top=False)]
    normal = plan_fleet(v, items, Rules())
    centered = plan_fleet(v, items, Rules(center_partial_load=True))
    cx_n = normal.plans[0].metrics["cog"][0]
    cx_c = centered.plans[0].metrics["cog"][0]
    assert abs(cx_c - 500) < abs(cx_n - 500)
    assert centered.plans[0].is_valid
    # Önde boşluk kaldığı için sabitleme uyarısı verilmeli; normal planda ağırlık dağılımı uyarısı
    assert any(w.category == "SABİTLEME" and "önünde" in w.message for w in centered.plans[0].warnings)
    assert any(w.category == "AĞIRLIK DAĞILIMI" for w in normal.plans[0].warnings)


def test_lateral_gap_warning():
    v = veh(L=240, W=245, H=200)
    # 120x100 yükler yan yana 2 tane = 200 cm; 45 cm yanal boşluk kalır
    p = plan_single_vehicle(v, expand_units([item("I", qty=2, L=120, W=100, H=100, kg=100, rotatable=False,
                                                  allow_on_top=False)]), Rules())
    assert any(w.category == "SABİTLEME" and "yan" in w.message for w in p.warnings)


def test_split_stops_reported_and_reduced_when_possible():
    v = veh(L=300, W=100, H=100, payload=10000)
    # Araç 3 yük alır. Normal plan: Araç 1 = S3,S3,S2 / Araç 2 = S2,S1 -> 2. durak bölünür.
    # Bölmeden de 2 araç yeter: Araç 1 = S3,S3 / Araç 2 = S2,S2,S1.
    items = [item("S1", qty=1, delivery_stop=1, allow_on_top=False),
             item("S2", qty=2, delivery_stop=2, allow_on_top=False),
             item("S3", qty=2, delivery_stop=3, allow_on_top=False)]
    f = plan_fleet(v, items, Rules(), max_vehicles=3)
    assert f.n_vehicles == 2 and f.metrics["split_stops"] == 1 and f.notes
    kept = plan_fleet(v, items, Rules(), max_vehicles=3, keep_stops=True)
    assert kept.metrics["unplaced_count"] == 0 and kept.n_vehicles == 2 and kept.metrics["split_stops"] == 0
    best = compare_vehicles([v], items, Rules(), availability={"t": 3})[0]
    assert best.n_vehicles == 2 and best.metrics["split_stops"] == 0 and not best.notes

    # Bölünme kaçınılmazsa (3 durak x 2 yük, 2 araç) ek araç kullanılmaz, yalnız uyarı verilir.
    items2 = [item(f"T{s}", qty=2, delivery_stop=s, allow_on_top=False) for s in (1, 2, 3)]
    best2 = compare_vehicles([v], items2, Rules(), availability={"t": 3})[0]
    assert best2.n_vehicles == 2 and best2.notes


def test_all_demo_profiles_are_valid_and_plan():
    from loadplanner.validation import validate_vehicle

    items = [item("P", qty=4, L=120, W=80, H=110, kg=300, cargo_type="palet", max_top_load=300),
             item("K", qty=10, L=40, W=30, H=30, kg=8, delivery_stop=2, max_top_load=40)]
    for vid, v in default_vehicles().items():
        assert not validate_vehicle(v), vid
        f = plan_fleet(v, items, Rules(), max_vehicles=3)
        assert f.metrics["unplaced_count"] == 0 or vid == "panelvan", vid
        for p in f.plans:
            assert p.is_valid, (vid, p.errors)
