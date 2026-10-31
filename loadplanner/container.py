"""Konteyner taşımacılığı: VGM (brüt ağırlık) özeti ve LCL gönderici dökümü.

VGM burada bir ÖN HESAPTIR: konteyner darası + yüklerin beyan edilen ağırlığı.
Resmi VGM, SOLAS'a göre tartımla (Yöntem 1) veya bütün paket, ambalaj, palet ve
istif/sabitleme malzemesinin ağırlıklarının toplanmasıyla (Yöntem 2) beyan edilir.

LCL ücreti W/M (weight or measurement) esasına göre hesaplanır:
ücrete esas miktar = max(hacim m³, ağırlık ton); en az ``min_wm`` kadar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import CargoItem, FleetPlan

NO_SHIPPER = "(gönderici girilmemiş)"


@dataclass
class VgmRow:
    unit_no: int
    container: str
    tare_kg: float
    cargo_kg: float
    gross_kg: float
    max_gross_kg: Optional[float]

    @property
    def ok(self) -> bool:
        return self.max_gross_kg is None or self.gross_kg <= self.max_gross_kg + 1e-6


def vgm_summary(fleet: FleetPlan, dunnage_kg: float = 0.0) -> list[VgmRow]:
    """Her konteyner için yaklaşık brüt ağırlık. Konteyner olmayan araçlar atlanır."""
    rows = []
    for p in fleet.plans:
        v = p.vehicle
        if not v.is_container:
            continue
        cargo = sum(q.weight for q in p.placements) + (dunnage_kg if p.placements else 0.0)
        tare = float(v.tare_kg or 0.0)
        rows.append(VgmRow(p.unit_no, p.unit_name or v.name, tare, cargo, tare + cargo, v.max_gross_kg))
    return rows


@dataclass
class LclRow:
    shipper: str
    units: int
    weight_kg: float
    cbm: float
    wm: float  # ücrete esas miktar (W/M)
    charge: Optional[float]
    containers: str  # yükün bulunduğu konteyner(ler)
    unplaced: int


def lcl_breakdown(items: list[CargoItem], fleet: Optional[FleetPlan], rate_per_wm: Optional[float],
                  min_wm: float = 1.0) -> list[LclRow]:
    """Göndericiye göre CBM, ağırlık, W/M ve ücret dökümü.

    Hacim ve ağırlık beyan edilen yük listesinden hesaplanır (yerleşimden bağımsız).
    ``fleet`` verilirse her göndericinin hangi konteynerlerde olduğu ve yerleşmeyen adedi eklenir.
    """
    groups: dict[str, list[CargoItem]] = {}
    for it in items:
        groups.setdefault(it.shipper.strip() or NO_SHIPPER, []).append(it)
    where: dict[str, set[int]] = {}
    unplaced: dict[str, int] = {}
    if fleet is not None:
        for p in fleet.plans:
            for q in p.placements:
                where.setdefault(q.item.shipper.strip() or NO_SHIPPER, set()).add(p.unit_no)
        for u in fleet.unplaced:
            key = u.item.shipper.strip() or NO_SHIPPER
            unplaced[key] = unplaced.get(key, 0) + 1
    rows = []
    for shipper, its in sorted(groups.items()):
        units = sum(int(i.quantity) for i in its)
        kg = sum(float(i.unit_weight) * int(i.quantity) for i in its)
        cbm = sum(i.unit_volume_cm3 * int(i.quantity) for i in its) / 1e6
        wm = max(cbm, kg / 1000.0, min_wm)
        charge = round(wm * rate_per_wm, 2) if rate_per_wm is not None else None
        cont = ", ".join(f"Konteyner {n}" for n in sorted(where.get(shipper, set()))) or "-"
        rows.append(LclRow(shipper, units, kg, cbm, wm, charge, cont, unplaced.get(shipper, 0)))
    return rows


def shipper_split_notes(fleet: FleetPlan) -> list[str]:
    """Birden fazla konteynere/araca bölünen göndericiler."""
    where: dict[str, set[int]] = {}
    for p in fleet.plans:
        for q in p.placements:
            if q.item.shipper.strip():
                where.setdefault(q.item.shipper.strip(), set()).add(p.unit_no)
    return [f"Gönderici {s}: yükleri {len(u)} konteynere/araca bölündü ({', '.join(map(str, sorted(u)))} no)."
            for s, u in sorted(where.items()) if len(u) > 1]
