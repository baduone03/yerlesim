"""Veri modelleri: araç, kapı, yasak bölge, aks bilgisi, yük satırı, yerleşim ve plan.

Koordinat sistemi (tüm ölçüler cm, ağırlıklar kg):
    X: araç uzunluğu boyunca. X=0 kasanın ön duvarı (kabin tarafı), X=L arka kapı.
    Y: araç genişliği boyunca. Y=0 sol duvar (sürücü tarafı), Y=W sağ duvar.
    Z: kasa tabanından yukarı doğru.
Bir kutunun (x, y, z) koordinatı kutunun minimum köşesidir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CARGO_TYPES = {
    "palet": "Palet",
    "koli": "Koli",
    "sandik": "Sandık",
    "makine": "Makine",
    "rulo": "Rulo",
    "ozel": "Özel yük",
}

DOOR_KINDS = {
    "arka": "Arka kapı",
    "yan_sol": "Sol yan kapı",
    "yan_sag": "Sağ yan kapı",
}

RULE_MODES = {
    "teslimat": "Teslimat sırasına göre (önerilen)",
    "LIFO": "LIFO - son yüklenen ilk çıkar",
    "FIFO": "FIFO - ilk yüklenen ilk çıkar",
}

OBJECTIVES = {
    "min_arac": "Minimum araç sayısı",
    "max_hacim": "Maksimum hacim kullanımı",
    "kolay_bosaltma": "Boşaltma kolaylığı",
}


def normalize_group(name: str) -> str:
    """Ayrım grubu adını karşılaştırma için normalleştirir (Türkçe küçük harf)."""
    s = str(name or "").strip().replace("I", "ı").replace("İ", "i")
    return " ".join(s.lower().split())


@dataclass
class Door:
    """Araç erişim kapısı.

    Arka kapı için ``x_start``/``x_end`` kullanılmaz. Yan kapılar için kapı
    açıklığının X aralığıdır. ``width``/``height`` kapı açıklığının ölçüleridir;
    None ise kasa iç ölçüsüne eşit kabul edilir.
    """

    kind: str
    x_start: float = 0.0
    x_end: float = 0.0
    width: Optional[float] = None
    height: Optional[float] = None


@dataclass
class Zone:
    """Kasa içinde yük konulamayacak hacim.

    blocks_passage=True: fiziksel engel (ör. teker davlumbazı). Yükün kapıya
    giden yolunu da keser.
    blocks_passage=False: boş bırakılacak alan (ör. kapı önü, sabitleme alanı).
    Yük konulmaz ama geçişe engel sayılmaz.
    """

    name: str
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float
    blocks_passage: bool = True

    def bounds(self) -> tuple[float, float, float, float, float, float]:
        return (self.x, self.y, self.z, self.x + self.dx, self.y + self.dy, self.z + self.dz)


@dataclass
class AxleConfig:
    """Basitleştirilmiş iki destekli aks modeli.

    Pozisyonlar kasa ön duvarına göre cm cinsindendir (ön aks kasanın önündeyse
    negatif). Yarı römorkta ön destek şasi pimidir (king pin).
    ``*_payload_limit_kg``: o aksa binebilecek AZAMİ FAYDALI YÜK
    (izin verilen aks yükü - boş araç aks yükü). Bilinmiyorsa None bırakılır ve
    aks kontrolü yapılmaz.
    """

    front_x: Optional[float] = None
    rear_x: Optional[float] = None
    front_payload_limit_kg: Optional[float] = None
    rear_payload_limit_kg: Optional[float] = None
    front_label: str = "Ön aks"
    rear_label: str = "Arka aks grubu"

    def has_geometry(self) -> bool:
        return (
            self.front_x is not None
            and self.rear_x is not None
            and self.rear_x > self.front_x
        )


@dataclass
class Vehicle:
    id: str
    name: str
    category: str
    length: float
    width: float
    height: float
    max_payload_kg: float
    doors: list[Door] = field(default_factory=lambda: [Door("arka")])
    zones: list[Zone] = field(default_factory=list)
    axles: AxleConfig = field(default_factory=AxleConfig)
    is_demo: bool = True
    notes: str = ""

    @property
    def volume_cm3(self) -> float:
        return self.length * self.width * self.height

    def door_size(self, door: Door) -> tuple[float, float]:
        """Kapı açıklığının (genişlik, yükseklik) değeri."""
        if door.kind == "arka":
            w = door.width if door.width else self.width
        else:
            w = door.width if door.width else max(door.x_end - door.x_start, 0.0)
        h = door.height if door.height else self.height
        return w, h


@dataclass
class CargoItem:
    """Kullanıcının girdiği bir yük satırı (aynı tip ``quantity`` adet yük).

    Ölçüler yükün DIŞ ölçüleridir; palet için palet dahil ölçü girilmelidir.
    length = boy (araç boyunca varsayılan yön), width = en, height = yükseklik.
    """

    code: str
    name: str
    cargo_type: str = "koli"
    quantity: int = 1
    length: float = 0.0
    width: float = 0.0
    height: float = 0.0
    unit_weight: float = 0.0
    includes_pallet: bool = True
    allow_on_top: bool = True
    max_top_load: Optional[float] = None
    rotatable: bool = True
    allow_tipping: bool = False
    fragile: bool = False
    stackable: bool = True
    delivery_stop: int = 1
    loading_priority: int = 1
    notes: str = ""
    segregation_group: str = ""  # ör. "gıda", "kimyasal"; Rules.incompatible ile birlikte kullanılır

    @property
    def group_key(self) -> str:
        return normalize_group(self.segregation_group)

    @property
    def unit_volume_cm3(self) -> float:
        return self.length * self.width * self.height

    @property
    def supports_load(self) -> bool:
        """Üstüne yük konulabilir mi (kırılabilir yükün üstüne konulmaz)."""
        return self.allow_on_top and not self.fragile

    def orientations(self) -> list[tuple[float, float, float, str]]:
        """İzin verilen yönelimler: (dx, dy, dz, etiket).

        Etiket, hangi orijinal ölçünün hangi eksene geldiğini gösterir
        (B=boy, E=en, Y=yükseklik; sıra X-Y-Z).
        """
        L, W, H = self.length, self.width, self.height
        cands = [(L, W, H, "B-E-Y (standart)")]
        if self.rotatable:
            cands.append((W, L, H, "E-B-Y (90° döndürülmüş)"))
        if self.allow_tipping:
            cands += [
                (L, H, W, "B-Y-E (yan yatırılmış)"),
                (H, L, W, "Y-B-E (yan yatırılmış)"),
                (W, H, L, "E-Y-B (dikey çevrilmiş)"),
                (H, W, L, "Y-E-B (dikey çevrilmiş)"),
            ]
        seen: set[tuple[float, float, float]] = set()
        out = []
        for dx, dy, dz, label in cands:
            if (dx, dy, dz) in seen:
                continue
            seen.add((dx, dy, dz))
            out.append((dx, dy, dz, label))
        return out


@dataclass
class Rules:
    mode: str = "teslimat"
    allow_mixed_stop_stacking: bool = False
    min_support_ratio: float = 0.75
    rear_clearance_cm: float = 0.0
    # Birlikte istiflenemeyen ve arasında en az ``separation_cm`` yatay mesafe
    # bırakılması gereken ayrım grubu çiftleri, ör. (("gıda", "kimyasal"),).
    incompatible: tuple = ()
    separation_cm: float = 0.0

    def __post_init__(self) -> None:
        pairs = set()
        for pair in self.incompatible:
            a, b = (normalize_group(x) for x in pair)
            if a and b and a != b:
                pairs.add(tuple(sorted((a, b))))
        self.incompatible = tuple(sorted(pairs))

    def are_incompatible(self, a: CargoItem, b: CargoItem) -> bool:
        ga, gb = a.group_key, b.group_key
        if not ga or not gb or ga == gb:
            return False
        return tuple(sorted((ga, gb))) in self.incompatible


@dataclass
class CargoUnit:
    """Bir yük satırının tek bir fiziksel adedi."""

    uid: str
    item: CargoItem
    index: int


@dataclass
class Placement:
    uid: str
    item: CargoItem
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float
    orientation: str
    rank: float
    load_step: int = 0
    unload_step: int = 0

    @property
    def x1(self) -> float:
        return self.x + self.dx

    @property
    def y1(self) -> float:
        return self.y + self.dy

    @property
    def z1(self) -> float:
        return self.z + self.dz

    @property
    def weight(self) -> float:
        return self.item.unit_weight

    @property
    def volume(self) -> float:
        return self.dx * self.dy * self.dz

    def bounds(self) -> tuple[float, float, float, float, float, float]:
        return (self.x, self.y, self.z, self.x1, self.y1, self.z1)


@dataclass
class Violation:
    """Bir kural ihlali veya uyarı.

    severity: "hata" (plan geçersiz) veya "uyari" (kullanıcı değerlendirmeli).
    """

    category: str
    severity: str
    message: str
    uids: list[str] = field(default_factory=list)


@dataclass
class UnplacedUnit:
    uid: str
    item: CargoItem
    reason: str
    scope: str  # "tekil" (yük tek başına sığmıyor) veya "birlikte" (liste birlikte sığmıyor)


@dataclass
class VehiclePlan:
    vehicle: Vehicle
    placements: list[Placement] = field(default_factory=list)
    unplaced: list[UnplacedUnit] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    strategy: str = ""

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "hata"]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "uyari"]

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass
class FleetPlan:
    """Bir araç tipiyle (gerekirse birden fazla adet) yapılan planlama sonucu."""

    vehicle: Vehicle
    plans: list[VehiclePlan] = field(default_factory=list)
    unplaced: list[UnplacedUnit] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    score: tuple = ()
    explanation: str = ""

    @property
    def n_vehicles(self) -> int:
        return len(self.plans)
