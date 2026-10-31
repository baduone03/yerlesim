"""CSV içe ve dışa aktarma.

İçe aktarmada hem ',' hem ';' ayırıcı ve ondalık virgül (Türkçe Excel) desteklenir.
"""

from __future__ import annotations

import csv
import io
from typing import Optional

import pandas as pd

from .models import CARGO_TYPES, CargoItem, FleetPlan, VehiclePlan

# CSV sütun adı -> (CargoItem alanı, tip)
COLUMNS = {
    "kod": ("code", str),
    "ad": ("name", str),
    "tip": ("cargo_type", str),
    "adet": ("quantity", int),
    "boy_cm": ("length", float),
    "en_cm": ("width", float),
    "yukseklik_cm": ("height", float),
    "birim_agirlik_kg": ("unit_weight", float),
    "palet_dahil": ("includes_pallet", bool),
    "ustune_yuk_konabilir": ("allow_on_top", bool),
    "ust_azami_yuk_kg": ("max_top_load", "opt_float"),
    "dondurulebilir": ("rotatable", bool),
    "yan_yatirilabilir": ("allow_tipping", bool),
    "kirilabilir": ("fragile", bool),
    "istiflenebilir": ("stackable", bool),
    "teslimat_sirasi": ("delivery_stop", int),
    "yukleme_onceligi": ("loading_priority", int),
    "notlar": ("notes", str),
    "ayrim_grubu": ("segregation_group", str),
    "soguk_zincir": ("cold_chain", bool),
    "gonderici": ("shipper", str),
}
REQUIRED = ["kod", "adet", "boy_cm", "en_cm", "yukseklik_cm", "birim_agirlik_kg"]

_TRUE = {"1", "evet", "e", "true", "t", "yes", "y", "var", "x"}
_FALSE = {"0", "hayır", "hayir", "h", "false", "f", "no", "n", "yok", ""}

_TYPE_ALIASES = {v.lower(): k for k, v in CARGO_TYPES.items()}
_TYPE_ALIASES.update({"sandık": "sandik", "özel": "ozel", "özel yük": "ozel"})


def _to_float(s: str) -> float:
    s = str(s).strip().replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")  # 1.234,5
    else:
        s = s.replace(",", ".")
    return float(s)


def _to_bool(s: str) -> bool:
    t = str(s).strip().lower()
    if t in _TRUE:
        return True
    if t in _FALSE:
        return False
    raise ValueError(f"'{s}' evet/hayır olarak yorumlanamadı")


def parse_items_csv(data: bytes | str) -> tuple[list[CargoItem], list[str]]:
    """CSV'den yük listesi okur. (yükler, hata mesajları) döndürür.

    Hatalı satırlar listeye alınmaz ve satır numarasıyla raporlanır.
    """
    if isinstance(data, bytes):
        for enc in ("utf-8-sig", "cp1254", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    else:
        text = data
    if not text.strip():
        return [], ["CSV dosyası boş."]
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
        delim = dialect.delimiter
    except csv.Error:
        delim = ";" if text.count(";") > text.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        return [], [f"CSV'de zorunlu sütun(lar) eksik: {', '.join(missing)}. Beklenen başlıklar: {', '.join(COLUMNS)}"]

    items: list[CargoItem] = []
    errors: list[str] = []
    for line_no, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v if v is not None else "") for k, v in raw.items()}
        if not any(str(v).strip() for v in row.values()):
            continue
        kwargs = {}
        row_err = []
        for col, (attr, typ) in COLUMNS.items():
            if col not in row:
                continue
            val = str(row[col]).strip()
            try:
                if typ is str:
                    kwargs[attr] = val
                elif typ is int:
                    f = _to_float(val)
                    if f != int(f):
                        raise ValueError("tam sayı değil")
                    kwargs[attr] = int(f)
                elif typ is float:
                    kwargs[attr] = _to_float(val)
                elif typ is bool:
                    kwargs[attr] = _to_bool(val)
                elif typ == "opt_float":
                    kwargs[attr] = _to_float(val) if val else None
            except ValueError as e:
                row_err.append(f"'{col}' değeri geçersiz ({val!r}: {e})")
        if "cargo_type" in kwargs:
            t = kwargs["cargo_type"].strip().lower()
            kwargs["cargo_type"] = t if t in CARGO_TYPES else _TYPE_ALIASES.get(t, "")
            if not kwargs["cargo_type"]:
                row_err.append(f"'tip' değeri geçersiz; olası değerler: {', '.join(CARGO_TYPES)}")
        if row_err:
            errors.append(f"Satır {line_no}: " + "; ".join(row_err))
            continue
        kwargs.setdefault("name", kwargs.get("code", ""))
        if not kwargs.get("name"):
            kwargs["name"] = kwargs.get("code", "")
        items.append(CargoItem(**kwargs))
    return items, errors


def items_to_csv(items: list[CargoItem]) -> str:
    rows = []
    for it in items:
        row = {}
        for col, (attr, typ) in COLUMNS.items():
            val = getattr(it, attr)
            if typ is bool:
                val = "evet" if val else "hayır"
            elif val is None:
                val = ""
            row[col] = val
        rows.append(row)
    return pd.DataFrame(rows, columns=list(COLUMNS)).to_csv(index=False, sep=";", decimal=",")


def placements_dataframe(plan: VehiclePlan, with_vehicle: bool = False) -> pd.DataFrame:
    rows = []
    for p in sorted(plan.placements, key=lambda q: q.load_step):
        r = {}
        if with_vehicle:
            r["arac_no"] = plan.unit_no
            r["arac"] = plan.unit_name or plan.vehicle.name
            r["kasa"] = plan.vehicle.body_label
        r.update({
            "yukleme_sirasi": p.load_step,
            "bosaltma_sirasi": p.unload_step,
            "yuk_id": p.uid,
            "kod": p.item.code,
            "ad": p.item.name,
            "tip": CARGO_TYPES.get(p.item.cargo_type, p.item.cargo_type),
            "teslimat_sirasi": p.item.delivery_stop,
            "gonderici": p.item.shipper,
            "x_cm": round(p.x, 1),
            "y_cm": round(p.y, 1),
            "z_cm": round(p.z, 1),
            "uzunluk_x_cm": round(p.dx, 1),
            "genislik_y_cm": round(p.dy, 1),
            "yukseklik_z_cm": round(p.dz, 1),
            "yonelim": p.orientation,
            "agirlik_kg": p.weight,
            "notlar": p.item.notes,
        })
        rows.append(r)
    return pd.DataFrame(rows)


def fleet_placements_csv(fleet: FleetPlan) -> str:
    frames = [placements_dataframe(p, with_vehicle=True) for p in fleet.plans if p.placements]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df.to_csv(index=False, sep=";", decimal=",")


def unplaced_csv(fleet: FleetPlan) -> str:
    rows = [{"yuk_id": u.uid, "kod": u.item.code, "ad": u.item.name,
             "kapsam": "tek başına sığmıyor" if u.scope == "tekil" else "liste birlikte sığmıyor",
             "neden": u.reason} for u in fleet.unplaced]
    return pd.DataFrame(rows, columns=["yuk_id", "kod", "ad", "kapsam", "neden"]).to_csv(index=False, sep=";")
