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
from typing import Callable, Optional

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
    segregation_conflict_mask,
    zone_arrays,
)
from .models import (
    LDM_REFERENCE_WIDTH_CM,
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


def _st(name: str) -> Strategy:
    s, o, f = name.split("/")
    return Strategy(name, s, o, f)


# Strateji setleri, 6 farklı yük listesi x 4 araç üzerinde 16 stratejinin tamamıyla
# karşılaştırılarak seçildi: "normal" set, tam aramaya göre toplam 1 yük fazla
# dışarıda bırakırken 4 kat hızlıdır. ("duvar": kabinden kapıya duvar örme,
# "serit": boyuna şeritler; kalabalık listelerde şerit belirgin daha iyi.)
SCREEN_SET = ("hacim/uzun/duvar", "yukseklik/uzun/serit")
NORMAL_SET = ("hacim/uzun/duvar", "taban/ince/duvar", "yukseklik/ince/duvar", "agirlik/uzun/serit")


def default_strategies(depth: str = "normal", mode: str = "teslimat") -> list[Strategy]:
    if depth == "kapsamli":
        return [Strategy(f"{s}/{o}/{f}", s, o, f) for f in ("duvar", "serit") for s in SORTS for o in ORIENTS]
    names = SCREEN_SET if depth == "hizli" else NORMAL_SET
    return [_st(n) for n in names]


def expand_units(items: list[CargoItem]) -> list[CargoUnit]:
    units = []
    for it in items:
        for k in range(int(it.quantity)):
            units.append(CargoUnit(uid=f"{it.code}#{k + 1}", item=it, index=k))
    return units


def fits_empty(vehicle: Vehicle, item: CargoItem) -> tuple[bool, str]:
    """Yük boş kasaya tek başına (soğuk zincir, ölçü, kapı, ağırlık olarak) sığar mı?"""
    if item.cold_chain and not vehicle.refrigerated:
        return False, "Soğuk zincir gerektiren yük; araç frigorifik değil."
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


def fits_vehicle(vehicle: Vehicle, item: CargoItem) -> tuple[bool, str]:
    """Araç (kamyon + varsa römork) kasalarından en az birine tek başına sığar mı?"""
    reasons = []
    for body in vehicle.bodies:
        ok, why = fits_empty(body, item)
        if ok:
            return True, ""
        reasons.append(why)
    return False, reasons[0]


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
    def _incompat_mask(self, item: CargoItem) -> np.ndarray:
        if not self.rules.incompatible or not item.group_key:
            return np.zeros(len(self.placed), dtype=bool)
        return np.array([self.rules.are_incompatible(item, p.item) for p in self.placed], dtype=bool)

    def _check(self, item: CargoItem, rank: float, b, relax_access=False, relax_stack=False,
               relax_seg=False, incompat=None) -> tuple[bool, str, list]:
        v = self.v
        if b[3] > v.length + EPS or b[4] > v.width + EPS or b[5] > v.height + EPS:
            return False, "sinir", []
        if boxes_overlap_mask(self.boxes, b).any() or boxes_overlap_mask(self.zones, b).any():
            return False, "cakisma", []
        if not relax_seg:
            if incompat is None:
                incompat = self._incompat_mask(item)
            if incompat.any() and segregation_conflict_mask(self.boxes[incompat], b, self.rules.separation_cm).any():
                return False, "ayrim", []
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
            if self.load_on[j] + w > self.placed[j].item.top_load_limit + 1e-6:
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
            c = it.top_load_limit - self.load_on[j]
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

    def find_position(self, item: CargoItem, rank: float, relax_access=False, relax_stack=False, relax_seg=False):
        ors = self._orientations(item)
        incompat = self._incompat_mask(item)
        pts = self._sorted_points()
        sep = self.rules.separation_cm
        if sep > EPS and incompat.any() and not relax_seg:
            # Ayrım mesafesi kadar öteye geçici aday noktalar (normal noktalar kutu yüzeylerindedir).
            extra = set()
            for bx in self.boxes[incompat]:
                for p in ((bx[3] + sep, bx[1], 0.0), (bx[3] + sep, 0.0, 0.0), (bx[0], bx[4] + sep, 0.0), (0.0, bx[4] + sep, 0.0)):
                    if p[0] < self.v.length - EPS and p[1] < self.v.width - EPS:
                        extra.add((round(p[0], 4), round(p[1], 4), 0.0))
            key = (lambda q: (q[1], q[2], q[0])) if self.st.fill == "serit" else (lambda q: (q[0], q[2], q[1]))
            pts = sorted(set(pts) | extra, key=key)
        if not pts:
            return None
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
        # Aday noktalar öncelik sırasıyla parça parça işlenir; iyi konum genellikle
        # ilk parçalarda bulunduğundan bütün noktaları süzmek gerekmez.
        CHUNK = 128
        for start in range(0, len(pts), CHUNK):
            chunk = pts[start:start + CHUNK]
            res = self._search_chunk(item, rank, chunk, ors, obs, eligible, incompat,
                                     relax_access, relax_stack, relax_seg)
            if res is not None:
                return res
        return None

    def _search_chunk(self, item, rank, pts, ors, obs, eligible, incompat, relax_access, relax_stack, relax_seg):
        P = np.array(pts, dtype=float)
        # Yalnız bu noktaların çevresindeki kutular çakışabilir veya destek olabilir.
        reach = np.max(np.array([o[:3] for o in ors]), axis=0)
        lo_r, hi_r = P.min(axis=0), P.max(axis=0) + reach
        if obs.shape[0]:
            obs = obs[(obs[:, 3] > lo_r[0]) & (obs[:, 0] < hi_r[0]) & (obs[:, 4] > lo_r[1]) & (obs[:, 1] < hi_r[1])
                      & (obs[:, 5] > lo_r[2]) & (obs[:, 2] < hi_r[2])]
        B_all = self.boxes
        near = np.nonzero((B_all[:, 3] > lo_r[0]) & (B_all[:, 0] < hi_r[0]) & (B_all[:, 4] > lo_r[1])
                          & (B_all[:, 1] < hi_r[1]) & (B_all[:, 5] >= lo_r[2] - 1e-4)
                          & (B_all[:, 5] <= P[:, 2].max() + 1e-4))[0]
        B, elig_near, cap_near = B_all[near], eligible[near], self.cap_chain[near]
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
                if not elig_near.any():
                    ok[air] = False
                else:
                    lo, h2 = P[air], hi[air]
                    top = np.abs(B[None, :, 5] - lo[:, None, 2]) < 1e-4
                    ox = np.clip(np.minimum(B[None, :, 3], h2[:, None, 0]) - np.maximum(B[None, :, 0], lo[:, None, 0]), 0, None)
                    oy = np.clip(np.minimum(B[None, :, 4], h2[:, None, 1]) - np.maximum(B[None, :, 1], lo[:, None, 1]), 0, None)
                    A = np.where(top, ox * oy, 0.0)
                    total = A.sum(axis=1)
                    bad = total + 1e-6 < self.rules.min_support_ratio * dx * dy
                    if not relax_stack:
                        bad |= A[:, ~elig_near].sum(axis=1) > EPS
                        share = A / np.maximum(total, EPS)[:, None]
                        bad |= (item.unit_weight * share > cap_near[None, :] + 1e-6).any(axis=1)
                    ok[air[bad]] = False
            cand[:, k] = ok
        for i in np.nonzero(cand.any(axis=1))[0]:
            px, py, pz = pts[i]
            for k in np.nonzero(cand[i])[0]:
                dx, dy, dz, label = ors[k]
                b = (px, py, pz, px + dx, py + dy, pz + dz)
                ok, _, supp = self._check(item, rank, b, relax_access, relax_stack, relax_seg, incompat)
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
    if pk.rules.incompatible and pk.find_position(item, rank, relax_seg=True) is not None:
        return (f"Boş alan var ancak ayrım kuralı ('{item.segregation_group}' grubu, uyumsuz yüklerle en az "
                f"{pk.rules.separation_cm:.0f} cm mesafe ve istif yasağı) nedeniyle kullanılamıyor.")
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
    floor_area = sum(p.dx * p.dy for p in plan.placements if p.z < EPS)
    m = {
        "placed_count": len(plan.placements),
        "unplaced_count": len(plan.unplaced),
        "placed_volume_m3": vol / 1e6,
        "placed_weight_kg": w,
        "volume_util": vol / v.volume_cm3 if v.volume_cm3 else 0.0,
        "weight_util": w / v.max_payload_kg if v.max_payload_kg else 0.0,
        "used_length_cm": max((p.x1 for p in plan.placements), default=0.0),
        # Yükleme metresi: kaplanan taban alanının 2,40 m standart genişliğe bölünmesi
        "ldm": floor_area / LDM_REFERENCE_WIDTH_CM / 100.0,
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
    """Tek bir kasa için stratejileri dener, en iyisini döndürür."""
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


def plan_unit(vehicle: Vehicle, units: list[CargoUnit], rules: Rules, strategies: Optional[list[Strategy]],
              objective: str, diagnose: bool, unit_no: int) -> tuple[list[VehiclePlan], list[CargoUnit]]:
    """Bir aracı (kamyon + varsa römork) doldurur. (kasa planları, kalan yükler)."""
    plans: list[VehiclePlan] = []
    remaining = units
    for body in vehicle.bodies:
        plan = plan_single_vehicle(body, remaining, rules, strategies, objective, diagnose)
        plan.unit_no, plan.unit_name, plan.unit_cost = unit_no, vehicle.name, vehicle.trip_cost
        plans.append(plan)
        placed = {p.uid for p in plan.placements}
        remaining = [u for u in remaining if u.uid not in placed]
    return plans, remaining


def center_partial_load(plan: VehiclePlan, rules: Rules) -> float:
    """Kısmi yükü kapıya doğru kaydırarak ağırlık merkezini kasa ortasına yaklaştırır.

    Bütün yükler aynı miktar kaydırılır (yerleşim düzeni ve erişim korunur).
    Kaydırma yeni bir ihlal doğurursa daha küçük adım denenir, olmazsa geri alınır.
    Kaydırılan mesafeyi (cm) döndürür.
    """
    pls = plan.placements
    total = sum(p.weight for p in pls)
    if total <= 0:
        return 0.0
    v = plan.vehicle
    cx = sum(p.weight * (p.x + p.dx / 2) for p in pls) / total
    clearance = rules.rear_clearance_cm if any(d.kind == "arka" for d in v.doors) else 0.0
    room = v.length - clearance - max(p.x1 for p in pls)
    delta = min(v.length / 2 - cx, room)
    if delta < 10:
        return 0.0
    base_errors = len([x for x in verify_plan(v, pls, rules)[0] if x.severity == "hata"])
    for step in (delta, delta / 2, delta / 4):
        step = float(int(step))  # tam santimetre
        if step < 10:
            break
        for p in pls:
            p.x += step
        errors = len([x for x in verify_plan(v, pls, rules)[0] if x.severity == "hata"])
        if errors <= base_errors:
            return step
        for p in pls:
            p.x -= step
    return 0.0


def _finish_fleet(fleet: FleetPlan, units: list[CargoUnit], rules: Rules) -> FleetPlan:
    """Ara listeleri temizler, (isteğe bağlı) kısmi yükü ortalar, son doğrulamayı yapar."""
    for p in fleet.plans:
        p.unplaced = []
        if rules.center_partial_load and p.placements:
            center_partial_load(p, rules)
        # Seçilen planlar için okunur (tek tek) yükleme sırası hesaplanır.
        finalize_plan(p, rules, detailed_sequence=True)
    fleet.notes = split_stop_notes(fleet)
    fleet.metrics = fleet_metrics(fleet, units)
    return fleet


def split_stops(fleet: FleetPlan) -> dict[int, list[int]]:
    """Birden fazla araca bölünmüş teslimat durakları: {durak: [araç no, ...]}."""
    where: dict[int, set[int]] = {}
    for p in fleet.plans:
        for q in p.placements:
            where.setdefault(q.item.delivery_stop, set()).add(p.unit_no)
    return {s: sorted(u) for s, u in sorted(where.items()) if len(u) > 1}


def split_stop_notes(fleet: FleetPlan) -> list[str]:
    return [f"{s}. durağın yükleri {len(u)} araca bölündü (Araç {', '.join(map(str, u))}). "
            "Teslimatta her araç bu durağa uğramalı." for s, u in split_stops(fleet).items()]


def plan_fleet(vehicle: Vehicle, items: list[CargoItem], rules: Rules, max_vehicles: int = 5,
               strategies: Optional[list[Strategy]] = None, objective: str = "min_arac",
               keep_stops: bool = False) -> FleetPlan:
    """Aynı tip araçtan gerektiği kadar (en fazla ``max_vehicles``) kullanarak planlar.

    ``keep_stops=True``: bir araçta yarım kalan teslimat duraklarının yükleri o
    araçtan çıkarılıp sonraki araca bırakılır (müşteri yükü araçlara bölünmesin).
    """
    units = expand_units(items)
    fleet = FleetPlan(vehicle=vehicle)
    remaining: list[CargoUnit] = []
    for u in units:
        ok, why = fits_vehicle(vehicle, u.item)
        if ok:
            remaining.append(u)
        else:
            fleet.unplaced.append(UnplacedUnit(u.uid, u.item, why, "tekil"))
    last_plans, last_units, last_diag = None, remaining, False
    unit_no = 0
    while remaining and unit_no < max_vehicles:
        last = unit_no == max_vehicles - 1
        plans, rest = plan_unit(vehicle, remaining, rules, strategies, objective, last, unit_no + 1)
        if keep_stops and rest and not last:
            placed_stops = {q.item.delivery_stop for p in plans for q in p.placements}
            partial = placed_stops & {u.item.delivery_stop for u in rest}
            whole = [u for u in remaining if u.item.delivery_stop in placed_stops - partial]
            if partial and whole:
                plans2, rest2 = plan_unit(vehicle, whole, rules, strategies, objective, False, unit_no + 1)
                if any(p.placements for p in plans2):
                    placed2 = {q.uid for p in plans2 for q in p.placements}
                    plans, rest = plans2, [u for u in remaining if u.uid not in placed2]
        last_plans, last_units, last_diag = plans, remaining, last
        if not any(p.placements for p in plans):
            break
        fleet.plans += plans
        remaining = rest
        unit_no += 1
    if remaining:
        if last_plans is not None and not last_diag:
            last_plans, _ = plan_unit(vehicle, last_units, rules, strategies, objective, True, unit_no)
        reasons = {x.uid: x.reason for x in last_plans[-1].unplaced} if last_plans else {}
        limit_hit = unit_no >= max_vehicles
        prefix = f"{max_vehicles} araç sınırına ulaşıldı. " if limit_hit else "Boş araçta bile yerleşmiyor (kurallar nedeniyle). "
        for u in remaining:
            fleet.unplaced.append(UnplacedUnit(u.uid, u.item, prefix + reasons.get(u.uid, NO_SPACE), "birlikte"))
    return _finish_fleet(fleet, units, rules)


def fleet_metrics(fleet: FleetPlan, units: list[CargoUnit]) -> dict:
    vol = sum(p.metrics["placed_volume_m3"] for p in fleet.plans)
    w = sum(p.metrics["placed_weight_kg"] for p in fleet.plans)
    cap_vol = sum(p.vehicle.volume_cm3 for p in fleet.plans)
    cap_w = sum(p.vehicle.max_payload_kg for p in fleet.plans)
    total_w = sum(u.item.unit_weight for u in units)
    total_vol = sum(u.item.unit_volume_cm3 for u in units) / 1e6
    costs = {p.unit_no: p.unit_cost for p in fleet.plans}
    total_cost = sum(costs.values()) if costs and all(c is not None for c in costs.values()) else None
    v = fleet.vehicle
    single = not fleet.mixed
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
        "capacity_volume_m3": cap_vol / 1e6,
        "volume_util": vol * 1e6 / cap_vol if cap_vol else 0.0,
        "weight_util": w / cap_w if cap_w else 0.0,
        "ldm": sum(p.metrics.get("ldm", 0.0) for p in fleet.plans),
        "total_cost": total_cost,
        "split_stops": len(split_stops(fleet)),
        "error_count": sum(p.metrics["error_count"] for p in fleet.plans),
        "access_violations": sum(p.metrics["access_violations"] for p in fleet.plans),
        "stacked_count": sum(p.metrics["stacked_count"] for p in fleet.plans),
        "min_by_weight": math.ceil(total_w / v.total_payload_kg) if single and v.total_payload_kg else None,
        "min_by_volume": math.ceil(total_vol * 1e6 / v.total_volume_cm3) if single and v.total_volume_cm3 else None,
    }


def fleet_score(fleet: FleetPlan, objective: str) -> tuple:
    m = fleet.metrics
    cap = round(m["capacity_volume_m3"], 1)
    cost = m["total_cost"] if m["total_cost"] is not None else math.inf
    base = (m["unplaced_count"], m["error_count"])
    if objective == "min_maliyet":
        return base + (cost, m["n_vehicles"], cap)
    if objective == "max_hacim":
        return base + (-round(m["volume_util"], 3), m["n_vehicles"], cost)
    if objective == "kolay_bosaltma":
        return base + (m["access_violations"], m["split_stops"], m["n_vehicles"], m["stacked_count"], cap)
    return base + (m["n_vehicles"], cost, cap, m["split_stops"])


def screen_strategies(mode: str) -> list[Strategy]:
    """Ön eleme için hızlı strateji seti."""
    return default_strategies("hizli", mode)


def _best_fleet(vehicle: Vehicle, items: list[CargoItem], rules: Rules, n: int, strategies, objective: str) -> FleetPlan:
    """Tek tip filo; duraklar araçlara bölündüyse durakları bölmeyen varyantı da dener."""
    f = plan_fleet(vehicle, items, rules, n, strategies, objective)
    if f.metrics["split_stops"] and f.n_vehicles > 1:
        g = plan_fleet(vehicle, items, rules, n, strategies, objective, keep_stops=True)
        better = (g.metrics["unplaced_count"], g.metrics["error_count"], g.n_vehicles, g.metrics["split_stops"]) < \
                 (f.metrics["unplaced_count"], f.metrics["error_count"], f.n_vehicles, f.metrics["split_stops"])
        if better:
            return g
    return f


def plan_mixed_fleet(vehicles: list[Vehicle], availability: dict[str, int], items: list[CargoItem], rules: Rules,
                     objective: str, screen: list[Strategy], full: list[Strategy]) -> Optional[FleetPlan]:
    """Eldeki farklı tip araçlarla açgözlü (greedy) karma filo planı.

    Her adımda kalan yükün tamamını taşıyabilen araçlardan en uygunu (maliyet
    biliniyorsa en ucuz, değilse en küçük) seçilir; hiçbiri tamamını alamıyorsa
    kalan yükün en büyük kısmını (maliyete oranla) taşıyan araç seçilir.
    """
    usable = [v for v in vehicles if availability.get(v.id, 0) > 0]
    if len(usable) < 2:
        return None
    units = expand_units(items)
    fleet = FleetPlan(vehicle=usable[0], mixed=True)
    remaining = []
    for u in units:
        if any(fits_vehicle(v, u.item)[0] for v in usable):
            remaining.append(u)
        else:
            fleet.unplaced.append(UnplacedUnit(u.uid, u.item, fits_vehicle(usable[0], u.item)[1] +
                                               " (eldeki hiçbir araca tek başına sığmıyor)", "tekil"))
    counts = {v.id: availability.get(v.id, 0) for v in usable}
    use_cost = objective == "min_maliyet" and all(v.trip_cost is not None for v in usable)
    unit_no = 0
    while remaining:
        rem_w = sum(u.item.unit_weight for u in remaining) or 1.0
        rem_v = sum(u.item.unit_volume_cm3 for u in remaining) or 1.0
        cands = []
        for v in usable:
            if counts[v.id] <= 0:
                continue
            plans, rest = plan_unit(v, remaining, rules, screen, objective, False, 0)
            if len(rest) == len(remaining):
                continue
            rest_ids = {u.uid for u in rest}
            pw = sum(u.item.unit_weight for u in remaining if u.uid not in rest_ids)
            pv = sum(u.item.unit_volume_cm3 for u in remaining if u.uid not in rest_ids)
            cov = 0.5 * (pw / rem_w + pv / rem_v)
            cost = v.trip_cost if use_cost else None
            cands.append((v, not rest, cov, cost))
        if not cands:
            break
        complete = [c for c in cands if c[1]]
        if complete:
            key = (lambda c: (c[3], c[0].total_volume_cm3)) if use_cost else (lambda c: (c[0].total_volume_cm3,))
            chosen = min(complete, key=key)[0]
        else:
            key = (lambda c: (-c[2] / max(c[3], 1e-9), c[0].total_volume_cm3)) if use_cost else \
                  (lambda c: (-round(c[2], 3), c[0].total_volume_cm3))
            chosen = min(cands, key=key)[0]
        plans, rest = plan_unit(chosen, remaining, rules, full, objective, False, unit_no + 1)
        if not any(p.placements for p in plans):
            break
        fleet.plans += plans
        remaining = rest
        counts[chosen.id] -= 1
        unit_no += 1
    for u in remaining:
        fleet.unplaced.append(UnplacedUnit(u.uid, u.item, "Eldeki araç adetleri yetmedi veya yük kalan araçlara "
                                                          "kurallara uygun yerleşmedi.", "birlikte"))
    if len({p.unit_name for p in fleet.plans}) < 2:
        return None  # tek tip araç kullanıldı; tek tip seçeneklerle aynı
    return _finish_fleet(fleet, units, rules)


def compare_vehicles(vehicles: list[Vehicle], items: list[CargoItem], rules: Rules, objective: str = "min_arac",
                     max_vehicles: int = 5, depth: str = "normal",
                     availability: Optional[dict[str, int]] = None, allow_mixed: bool = True,
                     progress: Optional[Callable[[float, str], None]] = None) -> list[FleetPlan]:
    """Araç seçeneklerini karşılaştırır.

    1. Bütün araç tipleri hızlı strateji setiyle taranır.
    2. En iyi 3 seçenek (kapsamlı aramada hepsi) tam strateji setiyle yeniden planlanır.
    3. Tek tip araç bütün yükü tek araçla taşıyamıyorsa eldeki araçlarla karma filo denenir.
    """
    avail = {v.id: (availability or {}).get(v.id, max_vehicles) for v in vehicles}
    screen = screen_strategies(rules.mode)
    full = default_strategies(depth, rules.mode)
    report = progress or (lambda frac, text: None)
    active = [v for v in vehicles if avail[v.id] > 0]
    fleets = []
    for i, v in enumerate(active):
        report(0.6 * i / max(len(active), 1), f"Ön eleme: {v.name}")
        f = plan_fleet(v, items, rules, avail[v.id], screen, objective)
        f.score = fleet_score(f, objective)
        fleets.append(f)
    fleets.sort(key=lambda f: f.score)
    k = len(fleets) if depth == "kapsamli" else 3
    refined = []
    for i, f in enumerate(fleets[:k]):
        report(0.6 + 0.3 * i / max(min(k, len(fleets)), 1), f"Ayrıntılı yerleşim: {f.vehicle.name}")
        g = _best_fleet(f.vehicle, items, rules, avail[f.vehicle.id], full, objective)
        g.score = fleet_score(g, objective)
        refined.append(g)
    fleets = refined + fleets[k:]
    best = min(fleets, key=lambda f: f.score) if fleets else None
    needs_mixed = best is None or best.n_vehicles > 1 or best.metrics["unplaced_count"] > 0 or objective == "min_maliyet"
    if allow_mixed and needs_mixed and len(active) > 1:
        report(0.9, "Karma filo deneniyor")
        mf = plan_mixed_fleet(vehicles, avail, items, rules, objective, screen, full)
        if mf is not None:
            mf.score = fleet_score(mf, objective)
            fleets.append(mf)
    fleets.sort(key=lambda f: f.score)
    if fleets:
        fleets[0].explanation = explain_choice(fleets, objective)
    report(1.0, "Tamamlandı")
    return fleets


def fmt_num(x: float, digits: int = 0) -> str:
    """Türkçe sayı biçimi: binlik ayırıcı nokta, ondalık virgül."""
    s = f"{x:,.{digits}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def _cost_text(m: dict) -> str:
    return f"{fmt_num(m['total_cost'])} TL" if m.get("total_cost") is not None else "girilmedi"


def explain_choice(fleets: list[FleetPlan], objective: str) -> str:
    from .models import OBJECTIVES

    best = fleets[0]
    m = best.metrics
    parts = [f"Hedef: {OBJECTIVES.get(objective, objective)}."]
    if m["unplaced_count"] == 0:
        parts.append(f"Tüm {m['total_units']} yük {best.label} ile yerleştirildi.")
    else:
        parts.append(f"{m['total_units']} yükten {m['unplaced_count']} tanesi hiçbir denemede yerleştirilemedi; "
                     "bu seçenek en az yerleşmeyen yükü bırakıyor.")
    parts.append(f"Hacim kullanımı %{100 * m['volume_util']:.0f}, ağırlık kapasitesi kullanımı %{100 * m['weight_util']:.0f}, "
                 f"yükleme metresi {fmt_num(m['ldm'], 1)} LDM.")
    if m.get("total_cost") is not None:
        parts.append(f"Sefer maliyeti: {_cost_text(m)}.")
    elif objective == "min_maliyet":
        parts.append("Bazı araçların sefer maliyeti girilmediği için maliyete göre sıralama yapılamadı; "
                     "araç sayısı ve kapasiteye göre sıralandı.")
    if best.mixed:
        parts.append("Bu bir karma filo önerisidir (eldeki farklı araç tipleri birlikte kullanıldı).")
    if m["error_count"]:
        parts.append(f"DİKKAT: planda {m['error_count']} kural ihlali var.")
    others = [f for f in fleets[1:] if f.metrics["unplaced_count"] == 0]
    if others:
        o = others[0]
        alt = f"En yakın alternatif: {o.label} (hacim %{100 * o.metrics['volume_util']:.0f}"
        if o.metrics.get("total_cost") is not None:
            alt += f", {_cost_text(o.metrics)}"
        parts.append(alt + ").")
    if any(p.vehicle.is_demo for p in best.plans):
        parts.append("Not: araç ölçüleri DEMO değerdir; gerçek araç verisiyle doğrulayın.")
    return " ".join(parts)
