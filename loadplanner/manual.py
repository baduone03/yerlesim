"""Yerleşimin elle düzenlenmesi.

Her işlemden sonra plan ``refresh_fleet`` ile algoritmadan bağımsız olarak
yeniden doğrulanır; elle yapılan hatalı bir değişiklik ihlal olarak görünür,
engellenmez. (Kullanıcı sahadaki gerçeği algoritmadan iyi bilebilir; sistem
yalnızca sonucunu açıkça gösterir.)
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .constraints import EPS, support_ok, unload_rank
from .models import CargoItem, CargoUnit, FleetPlan, Placement, Rules, UnplacedUnit, VehiclePlan
from .optimizer import Strategy, _Packer, finalize_plan, fleet_metrics

REMOVED_REASON = "Kullanıcı tarafından araçtan çıkarıldı."


def _find(plan: VehiclePlan, uid: str) -> Placement:
    for p in plan.placements:
        if p.uid == uid:
            return p
    raise KeyError(f"'{uid}' bu araçta bulunamadı.")


def _orientation(item: CargoItem, label: str) -> tuple[float, float, float, str]:
    for o in item.orientations():
        if o[3] == label:
            return o
    raise ValueError(f"'{item.code}' için '{label}' yönelimine izin verilmiyor.")


def refresh_fleet(fleet: FleetPlan, rules: Rules) -> FleetPlan:
    """Bütün araç planlarını yeniden doğrular ve özet metrikleri günceller."""
    for p in fleet.plans:
        finalize_plan(p, rules, detailed_sequence=True)
    units = [CargoUnit(p.uid, p.item, 0) for plan in fleet.plans for p in plan.placements]
    units += [CargoUnit(u.uid, u.item, 0) for u in fleet.unplaced]
    manual = fleet.metrics.get("manual_edits", 0)
    fleet.metrics = fleet_metrics(fleet, units)
    fleet.metrics["manual_edits"] = manual
    return fleet


def _touch(fleet: FleetPlan) -> None:
    fleet.metrics["manual_edits"] = fleet.metrics.get("manual_edits", 0) + 1


def move_placement(fleet: FleetPlan, vehicle_idx: int, uid: str, x: float, y: float, z: float,
                   orientation: Optional[str], rules: Rules) -> None:
    p = _find(fleet.plans[vehicle_idx], uid)
    if orientation:
        p.dx, p.dy, p.dz, p.orientation = _orientation(p.item, orientation)
    p.x, p.y, p.z = float(x), float(y), float(z)
    _touch(fleet)
    refresh_fleet(fleet, rules)


def swap_placements(fleet: FleetPlan, vehicle_idx: int, uid_a: str, uid_b: str, rules: Rules) -> None:
    """İki yükün konumlarını (en küçük köşelerini) değiştirir; her yük kendi ölçüsünü korur."""
    plan = fleet.plans[vehicle_idx]
    a, b = _find(plan, uid_a), _find(plan, uid_b)
    (a.x, a.y, a.z), (b.x, b.y, b.z) = (b.x, b.y, b.z), (a.x, a.y, a.z)
    _touch(fleet)
    refresh_fleet(fleet, rules)


def remove_placement(fleet: FleetPlan, vehicle_idx: int, uid: str, rules: Rules) -> None:
    plan = fleet.plans[vehicle_idx]
    p = _find(plan, uid)
    plan.placements.remove(p)
    fleet.unplaced.append(UnplacedUnit(p.uid, p.item, REMOVED_REASON, "birlikte"))
    _touch(fleet)
    refresh_fleet(fleet, rules)


def add_unplaced(fleet: FleetPlan, vehicle_idx: int, uid: str, x: float, y: float, z: float,
                 orientation: str, rules: Rules) -> None:
    u = next((u for u in fleet.unplaced if u.uid == uid), None)
    if u is None:
        raise KeyError(f"'{uid}' yerleştirilemeyen yükler listesinde yok.")
    dx, dy, dz, label = _orientation(u.item, orientation)
    fleet.unplaced.remove(u)
    fleet.plans[vehicle_idx].placements.append(
        Placement(u.uid, u.item, float(x), float(y), float(z), dx, dy, dz, label, unload_rank(u.item, rules.mode)))
    _touch(fleet)
    refresh_fleet(fleet, rules)


def suggest_position(plan: VehiclePlan, item: CargoItem, rules: Rules,
                     exclude_uid: Optional[str] = None) -> Optional[tuple[float, float, float, str]]:
    """Mevcut yerleşimi bozmadan, bütün kurallara uyan ilk konumu önerir. Yoksa None."""
    pk = _Packer(plan.vehicle, rules, Strategy("elle", "agirlik", "ince"))
    others = [p for p in plan.placements if p.uid != exclude_uid]
    for p in sorted(others, key=lambda q: q.z):
        b = p.bounds()
        supp = []
        if p.z > EPS and pk.boxes.shape[0]:
            _, areas, _ = support_ok(pk.boxes, b, 0.0)
            tot = float(areas.sum())
            if tot > EPS:
                supp = [(int(j), float(areas[j]) / tot) for j in np.nonzero(areas > EPS)[0]]
        pk.place(CargoUnit(p.uid, p.item, 0), unload_rank(p.item, rules.mode), b, p.orientation, supp)
    res = pk.find_position(item, unload_rank(item, rules.mode))
    if res is None:
        return None
    b, label, _ = res
    return b[0], b[1], b[2], label
