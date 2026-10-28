"""Örnek (DEMO) araç profilleri.

UYARI: Buradaki ölçü ve kapasiteler Türkiye'de yaygın araç sınıfları için
yaklaşık, temsili değerlerdir. Üretici verisi DEĞİLDİR. Aynı sınıftaki araçlar
arasında model, şasi, kasa üreticisi ve üst yapıya göre ciddi farklar olur.
Gerçek planlamada aracın ruhsatı / teknik belgesi ve kasa üreticisinin
verdiği iç ölçüler girilmelidir. Aks yük limitleri bilinmediği için demo
profillerde boş bırakılmıştır (aks kontrolü yapılmaz).
"""

from __future__ import annotations

import copy

from .models import AxleConfig, Door, Vehicle, Zone

_DEMO_NOTE = "DEMO değer - gerçek araç ölçüsü ve ruhsattaki kapasite ile değiştirin."


def _profiles() -> list[Vehicle]:
    return [
        Vehicle(
            id="panelvan",
            name="Panelvan / hafif ticari araç",
            category="Hafif ticari",
            length=260,
            width=170,
            height=140,
            max_payload_kg=1000,
            doors=[Door("arka", width=150, height=130), Door("yan_sag", x_start=20, x_end=120, height=125)],
            zones=[
                Zone("Sol teker davlumbazı", 150, 0, 0, 90, 24, 30, True),
                Zone("Sağ teker davlumbazı", 150, 146, 0, 90, 24, 30, True),
            ],
            axles=AxleConfig(front_x=-90, rear_x=195),
            notes=_DEMO_NOTE,
        ),
        Vehicle(
            id="orta_kamyonet",
            name="Orta boy kapalı kasa kamyonet",
            category="Kamyonet (~3,5-7,5 t)",
            length=420,
            width=205,
            height=210,
            max_payload_kg=3500,
            doors=[Door("arka")],
            axles=AxleConfig(front_x=-110, rear_x=260),
            notes=_DEMO_NOTE,
        ),
        Vehicle(
            id="buyuk_kamyonet",
            name="Büyük kapalı kasa kamyonet",
            category="Kamyonet (~8-12 t)",
            length=620,
            width=240,
            height=240,
            max_payload_kg=6000,
            doors=[Door("arka")],
            axles=AxleConfig(front_x=-120, rear_x=400),
            notes=_DEMO_NOTE,
        ),
        Vehicle(
            id="on_teker",
            name="10 teker kamyon",
            category="Kamyon (6x2 / 6x4)",
            length=850,
            width=245,
            height=260,
            max_payload_kg=15000,
            doors=[Door("arka")],
            axles=AxleConfig(front_x=-130, rear_x=560, rear_label="Arka tandem aks"),
            notes=_DEMO_NOTE,
        ),
        Vehicle(
            id="yari_romork",
            name="13,60 m tenteli / kapalı yarı römork",
            category="Çekici + yarı römork",
            length=1360,
            width=245,
            height=270,
            max_payload_kg=24000,
            doors=[Door("arka")],
            axles=AxleConfig(front_x=120, rear_x=1060, front_label="Şasi pimi (king pin)", rear_label="Römork aks grubu"),
            notes=_DEMO_NOTE + " Tenteli araçta yan erişim için yan kapı tanımlanabilir.",
        ),
        Vehicle(
            id="ozel",
            name="Özel araç (kullanıcı tanımlı)",
            category="Kullanıcı tanımlı",
            length=400,
            width=200,
            height=200,
            max_payload_kg=2000,
            doors=[Door("arka")],
            is_demo=False,
            notes="Ölçüleri ve kapasiteyi kendi aracınıza göre girin.",
        ),
    ]


def default_vehicles() -> dict[str, Vehicle]:
    """Her çağrıda bağımsız (düzenlenebilir) kopyalar döndürür."""
    return {v.id: v for v in _profiles()}


def get_default_vehicle(vehicle_id: str) -> Vehicle:
    return copy.deepcopy(default_vehicles()[vehicle_id])
