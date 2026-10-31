"""Uygulama girdilerini (yükler, kurallar, değiştirilmiş araçlar) kısa bir metne çevirir.

Streamlit oturum belleği sayfa yenilenince sıfırlanır. Bu metin sayfa adresine
(URL sorgu parametresi) yazılarak girdilerin yenilemeden sonra geri yüklenmesi
ve bağlantı ile paylaşılması sağlanır.
"""

from __future__ import annotations

import base64
import json
import zlib
from dataclasses import asdict, fields
from typing import Optional

from .models import AxleConfig, CargoItem, Door, Rules, Vehicle, Zone
from .vehicle_data import default_vehicles

VERSION = 1


def _plain(o):
    """NumPy/pandas sayı ve mantık tiplerini JSON'a uygun Python tiplerine çevirir
    (tablo düzenleyiciden gelen değerler np.int64 / np.bool_ olabilir)."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"{type(o).__name__} JSON'a çevrilemez")


def _only_fields(cls, d: dict) -> dict:
    """Bilinmeyen alanları atar (eski/yeni sürüm bağlantıları da açılabilsin)."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


def _vehicle_from_dict(d: dict) -> Vehicle:
    d = _only_fields(Vehicle, d)
    d["doors"] = [Door(**_only_fields(Door, x)) for x in d.get("doors", [])]
    d["zones"] = [Zone(**_only_fields(Zone, x)) for x in d.get("zones", [])]
    d["axles"] = AxleConfig(**_only_fields(AxleConfig, d.get("axles", {})))
    if d.get("trailer"):
        d["trailer"] = _vehicle_from_dict(d["trailer"])
    return Vehicle(**d)


def encode_state(items: list[CargoItem], rules: Rules, vehicles: dict[str, Vehicle],
                 availability: Optional[dict[str, int]] = None) -> str:
    defaults = default_vehicles()
    changed = {k: asdict(v) for k, v in vehicles.items() if k not in defaults or v != defaults[k]}
    payload = {"v": VERSION, "i": [asdict(i) for i in items], "r": asdict(rules), "vh": changed}
    if availability:
        payload["fa"] = availability
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_plain).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def decode_state(text: str) -> tuple[list[CargoItem], Rules, dict[str, Vehicle], dict[str, int]]:
    """(yükler, kurallar, araçlar, araç adetleri). Bozuk metinde ValueError fırlatır."""
    try:
        pad = "=" * (-len(text) % 4)
        payload = json.loads(zlib.decompress(base64.urlsafe_b64decode(text + pad)).decode("utf-8"))
        if payload.get("v") != VERSION:
            raise ValueError("desteklenmeyen sürüm")
        items = [CargoItem(**_only_fields(CargoItem, d)) for d in payload.get("i", [])]
        rules = Rules(**_only_fields(Rules, payload.get("r", {})))
        vehicles = default_vehicles()
        for k, d in payload.get("vh", {}).items():
            vehicles[k] = _vehicle_from_dict(d)
        availability = {str(k): int(v) for k, v in payload.get("fa", {}).items()}
    except (ValueError, TypeError, KeyError, AttributeError, zlib.error, json.JSONDecodeError) as e:
        raise ValueError(f"Kayıtlı veri okunamadı: {e}") from e
    return items, rules, vehicles, availability
