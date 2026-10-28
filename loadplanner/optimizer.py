"""Sezgisel 3D yerleşim (extreme-point tabanlı) ve araç karşılaştırması.

Algoritma özeti
---------------
1. Yük satırları adetlerine göre tekil yüklere (CargoUnit) açılır.
2. Her yükün boşaltma sırası (rank) kurala göre hesaplanır. En geç boşaltılacak
   yükler önce yerleştirilir; böylece kasanın önüne (kabine) yakın kalırlar.
3. Aday noktalar ("extreme point") arasında en öndeki/en alttaki uygun konum
   aranır. Her aday konum için şu SERT kısıtlar kontrol edilir:
   kasa sınırı, çakışma, yasak bölge, kapı açıklığı, taban desteği,
   istiflenebilirlik / kırılabilirlik / üst yük dayanımı, kalan ağırlık
   kapasitesi ve kapı erişimi (önüne kuralı bozan yük gelmemesi).
4. Birkaç farklı sıralama ve yönelim tercihi denenir, en iyi sonuç seçilir.
5. Sonuç ``constraints.verify_plan`` ile bağımsız olarak yeniden doğrulanır.

Bu bir sezgisel yöntemdir; en iyi (optimum) çözümü bulduğunu garanti etmez.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .constraints import (
    EPS,
    boxes_overlap_mask,
    corridor_mask,
    door_usable,
    is_bad_blocker,
    support_ok,
    unload_rank,
    verify_plan,
    zone_arrays,
)
from .models import (
    CargoItem,
    CargoUnit,
    FleetPlan,
    Placement,
    Rules,
    UnplacedUnit,
    Vehicle,
    VehiclePlan,
)


@dataclass(frozen=True)
class Strategy:
    name: str
    sort: str          # "agirlik", "hacim", "taban", "yukseklik"
    orient: str        # "ince" (X boyunca kısa kenar) veya "uzun" (X boyunca uzun kenar)
    fill: str = "duvar"  # "duvar" (önden arkaya duvar örme) veya "serit" (boyuna şeritler)


SORTS = ("agirlik", "hacim", "taban", "yukseklik")
ORIENTS = ("ince", "uzun")


def default_strategies(depth: str = "normal", mode: str = "teslimat") -> list[Strategy]:
    sorts = SORTS[:2] if depth == "hizli" else SORTS
    fills = ("duvar", "serit") if (depth == "kapsamli" or mode == "FIFO") else ("duvar",)
    return [Strategy(f"{s}/{o}/{f}", s, o, f) for f in fills for s in sorts for o in ORIENTS]


def expand_units(items: list[CargoItem]) -> list[CargoUnit]:
    units = []
    for it in items:
        for k in range(int(it.quantity)):
            units.append(CargoUnit(uid=f"{it.code}#{k + 1}", item=it, index=k))
    return units


def fits_empty(vehicle: Vehicle, item: CargoItem) -> tuple[bool, str]:
    """Yük boş araca tek başına (ölçü, kapı, ağırlık olarak) sığar mı?"""
    if item.unit_weight > vehicle.max_payload_kg + EPS:
        return False, (f"Birim ağırlık ({item.unit_weight:.0f} kg) aracın azami yük kapasitesini "
                       f"({vehicle.max_payload_kg:.0f} kg) aşıyor.")
    dims_ok = False
    for dx, dy, dz, _ in item.orientations():
        if dx <= vehicle.length + EPS and dy <= vehicle.width + EPS and dz <= vehicle.height + EPS:
            dims_ok = True
            for d in vehicle.doors:
                w, h = vehicle.door_size(d)
                across = dy if d.kind == "arka" else dx
                if across <= w + EPS and dz <= h + EPS:
                    return True, ""
    if not dims_ok:
        return False, (f"Ölçüler ({item.length:.0f}x{item.width:.0f}x{item.height:.0f} cm) izin verilen hiçbir yönde "
                       f"araç iç ölçülerine ({vehicle.length:.0f}x{vehicle.width:.0f}x{vehicle.height:.0f} cm) sığmıyor.")
    return False, "Yük kapı açıklığından geçmiyor."


class _Packer:
    def __init__(self, vehicle: Vehicle, rules: Rules, strategy: Strategy):
        self.v = vehicle
        self.rules = rules
        self.st = strategy
        self.zones, self.zone_blocks, _ = zone_arrays(vehicle, rules)
        self.block_zones = self.zones[self.zone_blocks] if self.zones.shape[0] else self.zones
        self.boxes = np.zeros((0, 6))
        self.ranks = np.zeros(0)
        self.placed: list[Placement] = []
        self.load_on: list[float] = []  # her yükün üstünden aldığı toplam ağırlık (kg)
        # cap_chain[j]: j'nin üstüne doğrudan eklenebilecek en fazla ağırlık; alttaki yüklerin
        # kalan dayanımı da paylarıyla hesaba katılır (yük aktarımı doğrusal olduğundan kesin).
        self.cap_chain = np.zeros(0)
        self.supporters: list[list[tuple[int, float]]] = []
        self.total_weight = 0.0
        self.points: set[tuple[float, float, float]] = {(0.0, 0.0, 0.0)}
        for z in self.zones:
            self._add_points(z)
        self.version = 0

    # -- aday noktalar ---------------------------------------------------------
    def _obstacles(self) -> np.ndarray:
        if self.zones.shape[0]:
            return np.vstack([self.boxes, self.zones])
        return self.boxes

    def _add_points(self, b) -> None:
        raw = [(b[3], b[1], b[2]), (b[0], b[4], b[2]), (b[0], b[1], b[5])]
        obs = self._obstacles()
        new = set()
        for p in raw:
            new.add(p)
            new.add(self._project(p, 2, obs))  # aşağı
            new.add(self._project(p, 1, obs))  # sola (Y=0)
            new.add(self._project(p, 0, obs))  # öne (X=0)
        for x, y, z in new:
            if x < self.v.length - EPS and y < self.v.width - EPS and z < self.v.height - EPS:
                self.points.add((round(x, 4), round(y, 4), round(z, 4)))

    @staticmethod
    def _project(p, axis: int, obs: np.ndarray) -> tuple[float, float, float]:
        """Noktayı verilen eksende 0'a doğru ilk engele kadar kaydırır."""
        if obs.shape[0] == 0:
            q = list(p)
            q[axis] = 0.0
            return tuple(q)
        others = [a for a in range(3) if a != axis]
        m = np.ones(obs.shape[0], dtype=bool)
        for a in others:
            m &= (obs[:, a] <= p[a] + EPS) & (obs[:, a + 3] > p[a] + EPS)
        m &= obs[:, axis + 3] <= p[axis] + EPS
        q = list(p)
        q[axis] = float(obs[m, axis + 3].max()) if m.any() else 0.0
        return tuple(q)

    def _sorted_points(self) -> list[tuple[float, float, float]]:
        if self.st.fill == "serit":
            return sorted(self.points, key=lambda p: (p[1], p[2], p[0]))
        return sorted(self.points, key=lambda p: (p[0], p[2], p[1]))

    # -- kontroller ------------------------------------------------------------
    def _check(self, item: CargoItem, rank: float, b, relax_access=False, relax_stack=False) -> tuple[bool, str, list]:
        v = self.v
        if b[3] > v.length + EPS or b[4] > v.width + EPS or b[5] > v.height + EPS:
            return False, "sinir", []
        if boxes_overlap_mask(self.boxes, b).any() or boxes_overlap_mask(self.zones, b).any():
            return False, "cakisma", []
        supp: list[tuple[int, float]] = []
        if b[2] > EPS:
            ok, areas, _ = support_ok(self.boxes, b, self.rules.min_support_ratio)
            if not ok:
                return False, "destek", []
            tot = float(areas.sum())
            supp = [(int(j), float(areas[j]) / tot) for j in np.nonzero(areas > EPS)[0]]
            if not relax_stack:
                if not item.stackable:
                    return False, "istif", []
                for j, _ in supp:
                    s = self.placed[j]
                    if not s.item.supports_load:
                        return False, "istif", []
                    if abs(self.ranks[j] - rank) > EPS:
                        if not self.rules.allow_mixed_stop_stacking or is_bad_blocker(rank, self.ranks[j], self.rules.mode):
                            return False, "istif", []
                if not self._top_load_ok(item.unit_weight, supp):
                    return False, "istif", []
        if not relax_access:
            if not self._accessible(b, rank, extra=None):
                return False, "erisim", []
            if self.rules.mode == "FIFO" and not self._others_still_accessible(b, rank):
                return False, "erisim", []
        return True, "", supp

    def _top_load_ok(self, weight: float, supp) -> bool:
        add: dict[int, float] = {}
        stack = [(j, weight * share) for j, share in supp]
        while stack:
            j, w = stack.pop()
            add[j] = add.get(j, 0.0) + w
            stack += [(k, w * sh) for k, sh in self.supporters[j]]
        for j, w in add.items():
            lim = self.placed[j].item.max_top_load
            if lim is not None and self.load_on[j] + w > lim + 1e-6:
                return False
        return True

    def _accessible(self, b, rank: float, extra) -> bool:
        """En az bir kapıdan, kurala aykırı engel olmadan erişilebilir mi?"""
        boxes, ranks = self.boxes, self.ranks
        if extra is not None:
            boxes = np.vstack([boxes, extra[0]])
            ranks = np.append(ranks, extra[1])
        for d in self.v.doors:
            if not door_usable(self.v, d, b):
                continue
            if self.block_zones.shape[0] and corridor_mask(d, b, self.block_zones).any():
                continue
            m = corridor_mask(d, b, boxes)
            if not m.any():
                return True
            br = ranks[m]
            if self.rules.mode == "FIFO":
                bad = np.abs(br - rank) > EPS
            else:
                bad = br > rank + EPS
            if not bad.any():
                return True
        return False

    def _others_still_accessible(self, b, rank: float) -> bool:
        """FIFO: yeni yük, farklı öncelikteki yerleşmiş bir yükün bütün çıkış yollarını kapatıyor mu?"""
        nb = np.array([b], dtype=float)
        for j, p in enumerate(self.placed):
            rj = float(self.ranks[j])
            if abs(rj - rank) <= EPS:
                continue
            pb = p.bounds()
            if not any(corridor_mask(d, pb, nb).any() for d in self.v.doors):
                continue
            if not self._accessible(pb, rj, extra=(b, rank)):
                return False
        return True

    def _update_cap_chain(self) -> None:
        n = len(self.placed)
        cap = np.full(n, np.inf)
        for j in sorted(range(n), key=lambda k: self.placed[k].z):
            it = self.placed[j].item
            c = np.inf if it.max_top_load is None else it.max_top_load - self.load_on[j]
            for k, sh in self.supporters[j]:
                c = min(c, cap[k] / sh if sh > EPS else np.inf)
            cap[j] = max(c, 0.0)
        self.cap_chain = cap

    # -- yerleştirme -----------------------------------------------------------
    def _orientations(self, item: CargoItem):
        ors = item.orientations()
        if self.st.orient == "ince":
            key = lambda o: (o[2], o[0], -o[1])
        else:
            key = lambda o: (o[2], -o[0], o[1])
        return sorted(ors, key=key)

    def find_position(self, item: CargoItem, rank: float, relax_access=False, relax_stack=False):
        ors = self._orientations(item)
        pts = self._sorted_points()
        if not pts:
            return None
        P = np.array(pts, dtype=float)
        obs = self._obstacles()
        # İstif kurallarına göre üzerine konulabilecek yükler (ön eleme için)
        if relax_stack:
            eligible = np.ones(len(self.placed), dtype=bool)
        elif not item.stackable:
            eligible = np.zeros(len(self.placed), dtype=bool)
        else:
            eligible = np.array([p.item.supports_load for p in self.placed], dtype=bool)
            same = np.abs(self.ranks - rank) <= EPS
            if self.rules.allow_mixed_stop_stacking and self.rules.mode != "FIFO":
                same |= self.ranks > rank + EPS  # yeni yük daha önce iner: üstte durabilir
            eligible &= same
        # Ön eleme (vektörel): kasa sınırı ve çakışma. Kalan adaylar tam kontrolden geçer.
        cand = np.zeros((len(pts), len(ors)), dtype=bool)
        for k, (dx, dy, dz, _) in enumerate(ors):
            hi = P + np.array([dx, dy, dz])
            ok = (hi[:, 0] <= self.v.length + EPS) & (hi[:, 1] <= self.v.width + EPS) & (hi[:, 2] <= self.v.height + EPS)
            if obs.shape[0] and ok.any():
                idx = np.nonzero(ok)[0]
                lo_i, hi_i = P[idx][:, None, :], hi[idx][:, None, :]
                ov = ((obs[None, :, :3] < hi_i - EPS) & (obs[None, :, 3:] > lo_i + EPS)).all(axis=2).any(axis=1)
                ok[idx[ov]] = False
            # Havadaki noktalar: temas alanı toplamı asgari destek oranının altındaysa ele.
            air = np.nonzero(ok & (P[:, 2] > EPS))[0]
            if air.size:
                if not eligible.any():
                    ok[air] = False
                else:
                    B = self.boxes
                    lo, h2 = P[air], hi[air]
                    top = np.abs(B[None, :, 5] - lo[:, None, 2]) < 1e-4
                    ox = np.clip(np.minimum(B[None, :, 3], h2[:, None, 0]) - np.maximum(B[None, :, 0], lo[:, None, 0]), 0, None)
                    oy = np.clip(np.minimum(B[None, :, 4], h2[:, None, 1]) - np.maximum(B[None, :, 1], lo[:, None, 1]), 0, None)
                    A = np.where(top, ox * oy, 0.0)
                    total = A.sum(axis=1)
                    bad = total + 1e-6 < self.rules.min_support_ratio * dx * dy
                    if not relax_stack:
                        bad |= A[:, ~eligible].sum(axis=1) > EPS
                        share = A / np.maximum(total, EPS)[:, None]
                        bad |= (item.unit_weight * share > self.cap_chain[None, :] + 1e-6).any(axis=1)
                    ok[air[bad]] = False
            cand[:, k] = ok
        for i in np.nonzero(cand.any(axis=1))[0]:
            px, py, pz = pts[i]
            for k in np.nonzero(cand[i])[0]:
                dx, dy, dz, label = ors[k]
                b = (px, py, pz, px + dx, py + dy, pz + dz)
                ok, _, supp = self._check(item, rank, b, relax_access, relax_stack)
                if ok:
                    return b, label, supp
        return None

    def place(self, unit: CargoUnit, rank: float, b, label: str, supp) -> None:
        p = Placement(uid=unit.uid, item=unit.item, x=b[0], y=b[1], z=b[2],
                      dx=b[3] - b[0], dy=b[4] - b[1], dz=b[5] - b[2], orientation=label, rank=rank)
        idx = len(self.placed)
        self.placed.append(p)
        self.boxes = np.vstack([self.boxes, np.array(b, dtype=float)])
        self.ranks = np.append(self.ranks, rank)
        self.load_on.append(0.0)
        self.supporters.append(supp)
        stack = [(j, unit.item.unit_weight * share) for j, share in supp]
        while stack:
            j, w = stack.pop()
            self.load_on[j] += w
            stack += [(k, w * sh) for k, sh in self.supporters[j]]
        self.total_weight += unit.item.unit_weight
        self._update_cap_chain()
        self.points.discard((round(b[0], 4), round(b[1], 4), round(b[2], 4)))
        self._add_points(b)
        self.version += 1
        assert idx == len(self.boxes) - 1


def _sort_key(strategy: Strategy, mode: str):
    def key(u: CargoUnit):
        it = u.item
        r = unload_rank(it, mode)
        base = 0 if it.supports_load else 1
        if strategy.sort == "agirlik":
            s = (-it.unit_weight, -it.unit_volume_cm3)
        elif strategy.sort == "hacim":
            s = (-it.unit_volume_cm3, -it.unit_weight)
        elif strategy.sort == "taban":
            s = (-(it.length * it.width), -it.height)
        else:
            s = (-it.height, -(it.length * it.width))
        return (-r, base, *s, it.code, u.index)
    return key


def pack_vehicle(vehicle: Vehicle, units: list[CargoUnit], rules: Rules, strategy: Strategy,
                 diagnose: bool = True) -> VehiclePlan:
    """Tek araç, tek strateji. Doğrulama yapılmadan ham plan döndürür.

    ``diagnose=False`` ise yer bulunamayan yükler için ayrıntılı neden analizi yapılmaz (hızlı).
    """
    pk = _Packer(vehicle, rules, strategy)
    unplaced: list[tuple[CargoUnit, str]] = []
    fail_cache: dict[tuple[str, int], str] = {}
    for u in sorted(units, key=_sort_key(strategy, rules.mode)):
        it = u.item
        ok, why = fits_empty(vehicle, it)
        if not ok:
            unplaced.append((u, "tekil:" + why))
            continue
        if pk.total_weight + it.unit_weight > vehicle.max_payload_kg + EPS:
            unplaced.append((u, "agirlik"))
            continue
        ck = (it.code, pk.version)
        if ck in fail_cache:
            unplaced.append((u, fail_cache[ck]))
            continue
        rank = unload_rank(it, rules.mode)
        res = pk.find_position(it, rank)
        if res is None:
            fail_cache[ck] = "yer"
            unplaced.append((u, "yer"))
            continue
        pk.place(u, rank, *res)

    plan = VehiclePlan(vehicle=vehicle, placements=pk.placed, strategy=strategy.name)
    diag: dict[str, str] = {}
    for u, code in unplaced:
        if code.startswith("tekil:"):
            plan.unplaced.append(UnplacedUnit(u.uid, u.item, code[6:], "tekil"))
            continue
        if code == "agirlik":
            reason = (f"Kalan ağırlık kapasitesi yetersiz: araçtaki yük {pk.total_weight:.0f} kg, "
                      f"bu yük {u.item.unit_weight:.0f} kg, kapasite {vehicle.max_payload_kg:.0f} kg.")
        else:
            if not diagnose:
                reason = NO_SPACE
            else:
                if u.item.code not in diag:
                    diag[u.item.code] = _diagnose(pk, u.item, unload_rank(u.item, rules.mode))
                reason = diag[u.item.code]
        plan.unplaced.append(UnplacedUnit(u.uid, u.item, reason, "birlikte"))
    return plan


NO_SPACE = "Araçta bu yük için yeterli ve desteklenen boş alan kalmadı (yükler birlikte sığmıyor)."


def _diagnose(pk: _Packer, item: CargoItem, rank: float) -> str:
    if pk.find_position(item, rank, relax_access=True) is not None:
        return ("Boş alan var ancak orada boşaltma sırası/kapı erişimi bozuluyor "
                "(daha geç boşaltılacak yükler önünü kapatıyor).")
    if pk.find_position(item, rank, relax_stack=True) is not None:
        return "Boş alan yalnızca istif kurallarına aykırı konumlarda (kırılabilir/istiflenemez yük, üst yük sınırı veya karışık istif)."
    if pk.find_position(item, rank, relax_access=True, relax_stack=True) is not None:
        return "Boş alan yalnızca hem istif hem erişim kurallarına aykırı konumlarda."
    return NO_SPACE


# ----------------------------------------------------------------------------
# Metrikler ve plan seçimi
# ----------------------------------------------------------------------------

def compute_metrics(plan: VehiclePlan, wa: Optional[dict] = None) -> dict:
    v = plan.vehicle
    vol = sum(p.volume for p in plan.placements)
    w = sum(p.weight for p in plan.placements)
    m = {
        "placed_count": len(plan.placements),
        "unplaced_count": len(plan.unplaced),
        "placed_volume_m3": vol / 1e6,
        "placed_weight_kg": w,
        "volume_util": vol / v.volume_cm3 if v.volume_cm3 else 0.0,
        "weight_util": w / v.max_payload_kg if v.max_payload_kg else 0.0,
        "used_length_cm": max((p.x1 for p in plan.placements), default=0.0),
        "stacked_count": sum(1 for p in plan.placements if p.z > EPS),
        "max_height_cm": max((p.z1 for p in plan.placements), default=0.0),
        "error_count": len(plan.errors),
        "warning_count": len(plan.warnings),
        "access_violations": sum(1 for x in plan.violations if x.category in ("ERİŞİM", "SIRA (FIFO)", "KAPI")),
        "cog": None,
        "axle": None,
    }
    if wa:
        m["cog"] = wa.get("cog")
        m["axle"] = wa.get("axle")
    return m


def _plan_score(plan: VehiclePlan, objective: str) -> tuple:
    m = plan.metrics
    lat = 0.0
    if m.get("cog"):
        lat = abs(m["cog"][1] - plan.vehicle.width / 2)
    base = (m["unplaced_count"], m["error_count"])
    if objective == "kolay_bosaltma":
        return base + (m["stacked_count"], round(m["used_length_cm"], -1), lat)
    return base + (round(m["used_length_cm"], -1), m["warning_count"], lat)


def finalize_plan(plan: VehiclePlan, rules: Rules, detailed_sequence: bool = False) -> VehiclePlan:
    plan.violations, wa = verify_plan(plan.vehicle, plan.placements, rules, detailed_sequence)
    plan.metrics = compute_metrics(plan, wa)
    return plan


def plan_single_vehicle(vehicle: Vehicle, units: list[CargoUnit], rules: Rules,
                        strategies: Optional[list[Strategy]] = None, objective: str = "min_arac",
                        diagnose: bool = True) -> VehiclePlan:
    strategies = strategies or default_strategies(mode=rules.mode)
    best, best_st = None, None
    for st in strategies:
        plan = finalize_plan(pack_vehicle(vehicle, units, rules, st, diagnose=False), rules)
        if best is None or _plan_score(plan, objective) < _plan_score(best, objective):
            best, best_st = plan, st
    if diagnose and best.unplaced:
        # Aynı strateji deterministiktir; nedenleri ayrıntılı analizle yeniden üret.
        best = finalize_plan(pack_vehicle(vehicle, units, rules, best_st, diagnose=True), rules)
    return best


def plan_fleet(vehicle: Vehicle, items: list[CargoItem], rules: Rules, max_vehicles: int = 5,
               strategies: Optional[list[Strategy]] = None, objective: str = "min_arac") -> FleetPlan:
    """Aynı tip araçtan gerektiği kadar (en fazla ``max_vehicles``) kullanarak planlar."""
    units = expand_units(items)
    fleet = FleetPlan(vehicle=vehicle)
    remaining: list[CargoUnit] = []
    for u in units:
        ok, why = fits_empty(vehicle, u.item)
        if ok:
            remaining.append(u)
        else:
            fleet.unplaced.append(UnplacedUnit(u.uid, u.item, why, "tekil"))
    last_plan, last_units, last_diag = None, remaining, False
    while remaining and len(fleet.plans) < max_vehicles:
        last = len(fleet.plans) == max_vehicles - 1
        plan = plan_single_vehicle(vehicle, remaining, rules, strategies, objective, diagnose=last)
        last_plan, last_units, last_diag = plan, remaining, last
        if not plan.placements:
            break
        fleet.plans.append(plan)
        placed = {p.uid for p in plan.placements}
        remaining = [u for u in remaining if u.uid not in placed]
    if remaining:
        if last_plan is not None and not last_diag:
            last_plan = plan_single_vehicle(vehicle, last_units, rules, strategies, objective, diagnose=True)
        reasons = {x.uid: x.reason for x in last_plan.unplaced} if last_plan else {}
        limit_hit = len(fleet.plans) >= max_vehicles
        prefix = f"{max_vehicles} araç sınırına ulaşıldı. " if limit_hit else "Boş araçta bile yerleşmiyor (kurallar nedeniyle). "
        for u in remaining:
            fleet.unplaced.append(UnplacedUnit(u.uid, u.item, prefix + reasons.get(u.uid, NO_SPACE), "birlikte"))
    # Ara araçların "yerleşmeyen" listeleri sonraki araçlara aktarıldı; temizle.
    # Seçilen planlar için okunur (tek tek) yükleme sırası hesaplanır.
    for p in fleet.plans:
        p.unplaced = []
        finalize_plan(p, rules, detailed_sequence=True)
    fleet.metrics = fleet_metrics(fleet, units)
    return fleet


def fleet_metrics(fleet: FleetPlan, units: list[CargoUnit]) -> dict:
    v = fleet.vehicle
    n = max(fleet.n_vehicles, 1)
    vol = sum(p.metrics["placed_volume_m3"] for p in fleet.plans)
    w = sum(p.metrics["placed_weight_kg"] for p in fleet.plans)
    total_w = sum(u.item.unit_weight for u in units)
    total_vol = sum(u.item.unit_volume_cm3 for u in units) / 1e6
    return {
        "n_vehicles": fleet.n_vehicles,
        "total_units": len(units),
        "placed_count": sum(p.metrics["placed_count"] for p in fleet.plans),
        "unplaced_count": len(fleet.unplaced),
        "unplaced_single": sum(1 for x in fleet.unplaced if x.scope == "tekil"),
        "total_weight_kg": total_w,
        "total_volume_m3": total_vol,
        "placed_weight_kg": w,
        "placed_volume_m3": vol,
        "volume_util": vol * 1e6 / (v.volume_cm3 * n),
        "weight_util": w / (v.max_payload_kg * n),
        "error_count": sum(p.metrics["error_count"] for p in fleet.plans),
        "access_violations": sum(p.metrics["access_violations"] for p in fleet.plans),
        "stacked_count": sum(p.metrics["stacked_count"] for p in fleet.plans),
        "min_by_weight": math.ceil(total_w / v.max_payload_kg) if v.max_payload_kg else None,
        "min_by_volume": math.ceil(total_vol * 1e6 / v.volume_cm3) if v.volume_cm3 else None,
    }


def fleet_score(fleet: FleetPlan, objective: str) -> tuple:
    m = fleet.metrics
    cap = fleet.vehicle.volume_cm3 * max(fleet.n_vehicles, 1)
    base = (m["unplaced_count"], m["error_count"])
    if objective == "max_hacim":
        return base + (-round(m["volume_util"], 3), m["n_vehicles"], cap)
    if objective == "kolay_bosaltma":
        return base + (m["access_violations"], m["n_vehicles"], m["stacked_count"], cap)
    return base + (m["n_vehicles"], cap, -m["volume_util"])


def compare_vehicles(vehicles: list[Vehicle], items: list[CargoItem], rules: Rules, objective: str = "min_arac",
                     max_vehicles: int = 5, depth: str = "normal") -> list[FleetPlan]:
    strategies = default_strategies(depth, rules.mode)
    fleets = []
    for v in vehicles:
        f = plan_fleet(v, items, rules, max_vehicles, strategies, objective)
        f.score = fleet_score(f, objective)
        fleets.append(f)
    fleets.sort(key=lambda f: f.score)
    if fleets:
        fleets[0].explanation = explain_choice(fleets, objective)
    return fleets


def explain_choice(fleets: list[FleetPlan], objective: str) -> str:
    from .models import OBJECTIVES

    best = fleets[0]
    m = best.metrics
    parts = [f"Hedef: {OBJECTIVES.get(objective, objective)}."]
    if m["unplaced_count"] == 0:
        parts.append(f"Tüm {m['total_units']} yük {m['n_vehicles']} adet {best.vehicle.name} ile yerleştirildi.")
    else:
        parts.append(f"{m['total_units']} yükten {m['unplaced_count']} tanesi hiçbir denemede yerleştirilemedi; "
                     "bu araç en az yerleşmeyen yükü bırakan seçenek.")
    parts.append(f"Hacim kullanımı %{100 * m['volume_util']:.0f}, ağırlık kapasitesi kullanımı %{100 * m['weight_util']:.0f}.")
    if m["error_count"]:
        parts.append(f"DİKKAT: planda {m['error_count']} kural ihlali var.")
    others = [f for f in fleets[1:] if f.metrics["unplaced_count"] == 0]
    if others:
        o = others[0]
        parts.append(f"En yakın alternatif: {o.vehicle.name} ({o.metrics['n_vehicles']} araç, "
                     f"hacim %{100 * o.metrics['volume_util']:.0f}).")
    if best.vehicle.is_demo:
        parts.append("Not: araç ölçüleri DEMO değerdir; gerçek araç verisiyle doğrulayın.")
    return " ".join(parts)
