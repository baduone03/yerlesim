"""Uygulama girdilerini (yükler, kurallar, değiştirilmiş araçlar) kısa bir metne çevirir.

Streamlit oturum belleği sayfa yenilenince sıfırlanır. Bu metin sayfa adresine
(URL sorgu parametresi) yazılarak girdilerin yenilemeden sonra geri yüklenmesi
ve bağlantı ile paylaşılması sağlanır.
"""

from __future__ import annotations

import base64
import json
import zlib
from dataclasses import asdict

from .models import AxleConfig, CargoItem, Door, Rules, Vehicle, Zone
from .vehicle_data import default_vehicles

VERSION = 1


def _vehicle_from_dict(d: dict) -> Vehicle:
    d = dict(d)
    d["doors"] = [Door(**x) for x in d.get("doors", [])]
    d["zones"] = [Zone(**x) for x in d.get("zones", [])]
    d["axles"] = AxleConfig(**d.get("axles", {}))
    return Vehicle(**d)


def encode_state(items: list[CargoItem], rules: Rules, vehicles: dict[str, Vehicle]) -> str:
    defaults = default_vehicles()
    changed = {k: asdict(v) for k, v in vehicles.items() if k not in defaults or v != defaults[k]}
    payload = {"v": VERSION, "i": [asdict(i) for i in items], "r": asdict(rules), "vh": changed}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def decode_state(text: str) -> tuple[list[CargoItem], Rules, dict[str, Vehicle]]:
    """Geçersiz veya bozuk metinde ValueError fırlatır."""
    try:
        pad = "=" * (-len(text) % 4)
        payload = json.loads(zlib.decompress(base64.urlsafe_b64decode(text + pad)).decode("utf-8"))
        if payload.get("v") != VERSION:
            raise ValueError("desteklenmeyen sürüm")
        items = [CargoItem(**d) for d in payload.get("i", [])]
        rules = Rules(**payload.get("r", {}))
        vehicles = default_vehicles()
        for k, d in payload.get("vh", {}).items():
            vehicles[k] = _vehicle_from_dict(d)
    except (ValueError, TypeError, KeyError, zlib.error, json.JSONDecodeError) as e:
        raise ValueError(f"Kayıtlı veri okunamadı: {e}") from e
    return items, rules, vehicles
