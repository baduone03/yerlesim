"""Kullanıcı girdisi doğrulamaları. Tüm mesajlar Türkçedir."""

from __future__ import annotations

import math

from .models import BODY_TYPES, CARGO_TYPES, DOOR_KINDS, CargoItem, Vehicle

MAX_DIM_CM = 2000.0  # bir yük boyutu için makul üst sınır (20 m)
MAX_QTY = 2000


def _is_number(v) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def validate_item(item: CargoItem) -> tuple[list[str], list[str]]:
    """(hatalar, uyarılar) döndürür. Hata varsa yük planlamaya alınmamalıdır."""
    errors: list[str] = []
    warnings: list[str] = []
    label = item.code or item.name or "(adsız yük)"

    if not str(item.code).strip() and not str(item.name).strip():
        errors.append("Yük adı veya ürün kodu boş olamaz.")
    if item.cargo_type not in CARGO_TYPES:
        errors.append(f"{label}: geçersiz yük tipi '{item.cargo_type}'.")

    q = item.quantity
    if not _is_number(q) or float(q) != int(float(q)):
        errors.append(f"{label}: adet tam sayı olmalıdır.")
    elif int(q) < 1:
        errors.append(f"{label}: adet en az 1 olmalıdır (girilen: {q}).")
    elif int(q) > MAX_QTY:
        errors.append(f"{label}: adet en fazla {MAX_QTY} olabilir.")

    for field_name, tr in (("length", "Boy"), ("width", "En"), ("height", "Yükseklik")):
        v = getattr(item, field_name)
        if not _is_number(v):
            errors.append(f"{label}: {tr} sayısal olmalıdır.")
        elif float(v) < 0:
            errors.append(f"{label}: {tr} negatif olamaz ({v} cm).")
        elif float(v) == 0:
            errors.append(f"{label}: {tr} sıfır olamaz.")
        elif float(v) > MAX_DIM_CM:
            errors.append(f"{label}: {tr} {MAX_DIM_CM:.0f} cm'den büyük görünüyor; birim cm olmalıdır.")

    w = item.unit_weight
    if not _is_number(w):
        errors.append(f"{label}: birim ağırlık sayısal olmalıdır.")
    elif float(w) <= 0:
        errors.append(f"{label}: birim ağırlık sıfırdan büyük olmalıdır (girilen: {w} kg).")

    if item.max_top_load is not None:
        if not _is_number(item.max_top_load) or float(item.max_top_load) < 0:
            errors.append(f"{label}: üzerine konulabilecek azami ağırlık negatif veya geçersiz olamaz.")

    for field_name, tr in (("delivery_stop", "Teslimat sırası"), ("loading_priority", "Yükleme önceliği")):
        v = getattr(item, field_name)
        if not _is_number(v) or float(v) != int(float(v)) or int(float(v)) < 1:
            errors.append(f"{label}: {tr} 1 veya daha büyük bir tam sayı olmalıdır.")

    if not errors:
        if item.cargo_type == "palet" and not item.includes_pallet:
            warnings.append(f"{label}: palet ölçülerinin palet DAHİL dış ölçü olduğundan emin olun.")
        if item.supports_load and item.max_top_load is None:
            warnings.append(
                f"{label}: üzerine konulabilecek azami ağırlık girilmedi; güvenli tarafta kalmak için kendi "
                f"ağırlığı ({float(item.unit_weight):.0f} kg) kadar kabul edildi. Ambalaj dayanımını biliyorsanız girin."
            )
        if item.cargo_type == "palet" and float(item.unit_weight) > 1500:
            warnings.append(f"{label}: palet başına {float(item.unit_weight):.0f} kg; standart ahşap paletlerin "
                            "taşıma kapasitesi genellikle 1000-1500 kg'dır. Palet tipini kontrol edin.")
        if item.fragile and item.allow_on_top:
            warnings.append(f"{label}: kırılabilir yükün üstüne yük konulmayacak.")
    return errors, warnings


def validate_items(items: list[CargoItem]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not items:
        errors.append("Yük listesi boş. Planlama için en az bir yük ekleyin.")
        return errors, warnings
    codes = [str(i.code).strip() for i in items if str(i.code).strip()]
    dups = sorted({c for c in codes if codes.count(c) > 1})
    if dups:
        errors.append("Aynı ürün kodu birden fazla satırda kullanılmış: " + ", ".join(dups))
    for it in items:
        e, w = validate_item(it)
        errors += e
        warnings += w
    return errors, warnings


def validate_vehicle(v: Vehicle) -> list[str]:
    errors: list[str] = []
    for field_name, tr in (("length", "İç uzunluk"), ("width", "İç genişlik"), ("height", "İç yükseklik")):
        val = getattr(v, field_name)
        if not _is_number(val) or float(val) <= 0:
            errors.append(f"{v.name}: {tr} sıfırdan büyük olmalıdır.")
    if not _is_number(v.max_payload_kg) or float(v.max_payload_kg) <= 0:
        errors.append(f"{v.name}: azami yük kapasitesi sıfırdan büyük olmalıdır.")
    if not v.doors:
        errors.append(f"{v.name}: en az bir erişim kapısı tanımlanmalıdır.")
    for d in v.doors:
        if d.kind not in DOOR_KINDS:
            errors.append(f"{v.name}: geçersiz kapı tipi '{d.kind}'.")
        elif d.kind != "arka":
            if d.x_end <= d.x_start or d.x_start < 0 or d.x_end > v.length:
                errors.append(f"{v.name}: yan kapının X aralığı kasa uzunluğu içinde ve başlangıç < bitiş olmalıdır.")
        for val, tr in ((d.width, "genişliği"), (d.height, "yüksekliği")):
            if val is not None and val <= 0:
                errors.append(f"{v.name}: kapı açıklığı {tr} sıfırdan büyük olmalıdır.")
    for z in v.zones:
        if min(z.dx, z.dy, z.dz) <= 0:
            errors.append(f"{v.name}: '{z.name}' bölgesinin ölçüleri sıfırdan büyük olmalıdır.")
        if z.x < 0 or z.y < 0 or z.z < 0 or z.x + z.dx > v.length + 1e-6 or z.y + z.dy > v.width + 1e-6 or z.z + z.dz > v.height + 1e-6:
            errors.append(f"{v.name}: '{z.name}' bölgesi kasa sınırları dışına taşıyor.")
    a = v.axles
    if (a.front_x is None) != (a.rear_x is None):
        errors.append(f"{v.name}: aks hesabı için ön ve arka aks konumunun ikisi de girilmelidir.")
    elif a.front_x is not None and a.rear_x <= a.front_x:
        errors.append(f"{v.name}: arka aks konumu ön aks konumundan büyük olmalıdır.")
    if v.trip_cost is not None and (not _is_number(v.trip_cost) or float(v.trip_cost) < 0):
        errors.append(f"{v.name}: sefer maliyeti negatif olamaz.")
    if v.body_type not in BODY_TYPES:
        errors.append(f"{v.name}: geçersiz kasa tipi '{v.body_type}'.")
    if v.trailer is not None:
        errors += [f"{v.name} / römork: " + e.split(": ", 1)[-1] for e in validate_vehicle(v.trailer)]
    return errors
