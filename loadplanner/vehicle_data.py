"""Örnek (DEMO) araç profilleri.

UYARI: Buradaki ölçü ve kapasiteler Türkiye'de yaygın araç sınıfları için
yaklaşık, temsili değerlerdir. Üretici verisi DEĞİLDİR. Aynı sınıftaki araçlar
arasında model, şasi, kasa üreticisi ve üst yapıya göre ciddi farklar olur.
Gerçek planlamada aracın ruhsatı / teknik belgesindeki istiap haddi ve kasa
üreticisinin verdiği iç ölçüler girilmelidir. Aks yük limitleri bilinmediği
için demo profillerde boş bırakılmıştır (aks kontrolü yapılmaz). Sefer
maliyeti de boştur; kullanıcı kendi navlun fiyatını girer.
"""

from __future__ import annotations

import copy

from .models import AxleConfig, Door, Vehicle, Zone, doors_for_body

_DEMO_NOTE = "DEMO değer - gerçek araç ölçüsü ve ruhsattaki istiap haddi ile değiştirin."


def _v(id, name, category, L, W, H, payload, body="kapali", **kw) -> Vehicle:
    kw.setdefault("doors", doors_for_body(body, L))
    kw.setdefault("notes", _DEMO_NOTE)
    return Vehicle(id=id, name=name, category=category, length=L, width=W, height=H,
                   max_payload_kg=payload, body_type=body, **kw)


def _profiles() -> list[Vehicle]:
    return [
        _v("panelvan", "Panelvan / hafif ticari araç", "Hafif ticari", 260, 170, 140, 1000, "ozel",
           doors=[Door("arka", width=150, height=130), Door("yan_sag", x_start=20, x_end=120, height=125)],
           zones=[Zone("Sol teker davlumbazı", 150, 0, 0, 90, 24, 30, True),
                  Zone("Sağ teker davlumbazı", 150, 146, 0, 90, 24, 30, True)],
           axles=AxleConfig(front_x=-90, rear_x=195)),
        _v("orta_kamyonet", "Orta boy kapalı kasa kamyonet", "Kamyonet (~7,5 t)", 420, 205, 210, 3500,
           axles=AxleConfig(front_x=-110, rear_x=260)),
        _v("buyuk_kamyonet", "Büyük kapalı kasa kamyonet", "Kamyonet (~8-12 t)", 620, 240, 240, 6000,
           axles=AxleConfig(front_x=-120, rear_x=400)),
        _v("on_teker", "10 teker kamyon (kapalı kasa)", "Kamyon 6x2, 3 akslı", 850, 245, 260, 15000,
           axles=AxleConfig(front_x=-130, rear_x=560, rear_label="Arka tandem aks")),
        _v("kirkayak", "Kırkayak (12 teker) tenteli kamyon", "Kamyon 8x2, 4 akslı", 980, 248, 270, 19000, "tenteli",
           axles=AxleConfig(front_x=-60, rear_x=700, front_label="Ön aks grubu", rear_label="Arka tandem aks")),
        _v("yari_romork", "Tenteli dorse 13,60 m", "Çekici + yarı römork", 1360, 248, 270, 24000, "tenteli",
           axles=AxleConfig(front_x=120, rear_x=1060, front_label="Şasi pimi (king pin)", rear_label="Dorse aks grubu"),
           notes=_DEMO_NOTE + " Tente açılınca iki yandan forkliftle yükleme/boşaltma yapılabilir."),
        _v("mega_dorse", "Mega tenteli dorse 13,60 m", "Çekici + yarı römork (mega)", 1360, 248, 300, 24000, "tenteli",
           axles=AxleConfig(front_x=120, rear_x=1060, front_label="Şasi pimi (king pin)", rear_label="Dorse aks grubu"),
           notes=_DEMO_NOTE + " Mega dorsede iç yükseklik ~3 m'dir; hacimli, hafif yükler için uygundur."),
        _v("kapali_dorse", "Kapalı (kutu) dorse 13,60 m", "Çekici + yarı römork", 1360, 248, 265, 23000,
           axles=AxleConfig(front_x=120, rear_x=1060, front_label="Şasi pimi (king pin)", rear_label="Dorse aks grubu")),
        _v("frigo_dorse", "Frigorifik dorse 13,40 m", "Çekici + soğutmalı yarı römork", 1340, 246, 260, 22000, "frigorifik",
           axles=AxleConfig(front_x=120, rear_x=1040, front_label="Şasi pimi (king pin)", rear_label="Dorse aks grubu"),
           notes=_DEMO_NOTE + " Soğuk zincir yükleri yalnız frigorifik araçlara yüklenir."),
        _v("kamyon_romork", "Kamyon + römork (tenteli, 2 × 7,7 m)", "Kamyon + römork kombinasyonu",
           770, 248, 270, 11000, "ozel", body_label="Kamyon kasası",
           doors=[Door("yan_sol", 0.0, 770), Door("yan_sag", 0.0, 770)],
           axles=AxleConfig(front_x=-130, rear_x=520, rear_label="Kamyon arka aks grubu"),
           trailer=_v("kamyon_romork_r", "Römork", "Römork", 770, 248, 270, 13000, "tenteli", body_label="Römork",
                      axles=AxleConfig(front_x=150, rear_x=600, front_label="Römork ön aks", rear_label="Römork arka aks")),
           notes=_DEMO_NOTE + " Römork bağlıyken kamyon kasasının arka kapısı kullanılamaz; kamyon kasası "
                              "yalnız yanlardan (tente) yüklenir/boşaltılır. Toplam istiap ~24 t."),
        *_containers(),
        Vehicle(id="ozel", name="Özel araç (kullanıcı tanımlı)", category="Kullanıcı tanımlı",
                length=400, width=200, height=200, max_payload_kg=2000, doors=[Door("arka")],
                is_demo=False, notes="Ölçüleri ve kapasiteyi kendi aracınıza göre girin."),
    ]


_CONTAINER_NOTE = ("DEMO değer - konteynerin CSC plakasındaki dara ve azami brüt ağırlık ile hat/kiralama "
                   "şirketinin iç ölçüleri esas alınmalıdır. Karayolu taşımasında ayrıca araç ve aks sınırları geçerlidir.")


def _container(id, name, L, W, H, door_w, door_h, tare, max_gross, reefer=False) -> Vehicle:
    return Vehicle(id=id, name=name, category="Deniz konteyneri", length=L, width=W, height=H,
                   max_payload_kg=max_gross - tare, doors=[Door("arka", width=door_w, height=door_h)],
                   body_type="frigorifik" if reefer else "kapali", is_container=True,
                   tare_kg=tare, max_gross_kg=max_gross, notes=_CONTAINER_NOTE)


def _containers() -> list[Vehicle]:
    return [
        _container("k20dc", "20' DC konteyner", 589, 235, 239, 234, 228, 2200, 30480),
        _container("k40dc", "40' DC konteyner", 1203, 235, 239, 234, 228, 3750, 30480),
        _container("k40hc", "40' HC konteyner", 1203, 235, 269, 234, 258, 3900, 30480),
        _container("k45hc", "45' HC konteyner", 1355, 235, 269, 234, 258, 4800, 32500),
        _container("k20rf", "20' reefer konteyner", 544, 229, 227, 229, 226, 3000, 30480, reefer=True),
        _container("k40rf", "40' reefer (HC) konteyner", 1158, 229, 255, 229, 250, 4600, 34000, reefer=True),
    ]


def default_vehicles() -> dict[str, Vehicle]:
    """Her çağrıda bağımsız (düzenlenebilir) kopyalar döndürür."""
    return {v.id: v for v in _profiles()}


def get_default_vehicle(vehicle_id: str) -> Vehicle:
    return copy.deepcopy(default_vehicles()[vehicle_id])
