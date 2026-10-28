"""Geometri yardımcıları, boşaltma sırası kuralları ve BAĞIMSIZ plan doğrulaması.

``verify_plan`` yerleşim algoritmasının iç durumunu kullanmaz; yalnızca
yerleşim koordinatlarından bütün kontrolleri baştan hesaplar. Böylece
algoritmadaki bir hata, raporda ihlal olarak görünür.

Bu modülün YAPMADIĞI kontroller (MVP sınırları):
  * Yük sabitleme (spanzet, kilit, sürtünme) hesabı yapılmaz.
  * Fren/viraj kuvvetleri ve dinamik yükler hesaplanmaz.
  * Aks yükü, yalnızca kullanıcı aks konumlarını ve aks başına azami faydalı
    yükü girdiyse, iki destekli basit kiriş modeliyle yaklaşık hesaplanır.
  * Silindirik yükler (rulo) çevreleyen dikdörtgen kutu olarak modellenir.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .models import CargoItem, Placement, Rules, Vehicle, Violation

EPS = 1e-6


# ----------------------------------------------------------------------------
# Boşaltma sırası (rank): küçük değer = önce boşaltılır
# ----------------------------------------------------------------------------

def unload_rank(item: CargoItem, mode: str) -> float:
    if mode == "teslimat":
        return float(item.delivery_stop)
    if mode == "LIFO":
        # Son yüklenen (yükleme önceliği büyük) ilk çıkar.
        return -float(item.loading_priority)
    if mode == "FIFO":
        # İlk yüklenen (yükleme önceliği küçük) ilk çıkar.
        return float(item.loading_priority)
    raise ValueError(f"Bilinmeyen kural: {mode}")


def is_bad_blocker(blocker_rank: float, blocked_rank: float, mode: str) -> bool:
    """``blocker`` yükü ``blocked`` yükünün önündeyse (veya üstündeyse) bu kurala aykırı mı?

    Teslimat/LIFO: önde duran yük daha SONRA boşaltılacaksa ihlaldir.
    FIFO: yükleme ve boşaltma sırası aynı olduğu için, farklı sıradaki iki yükün
    birbirinin önünde olması her durumda çelişkidir (biri ya yüklemede ya
    boşaltmada diğerinin yolunu keser).
    """
    if mode == "FIFO":
        return abs(blocker_rank - blocked_rank) > EPS
    return blocker_rank > blocked_rank + EPS


def dense_order(values) -> dict[float, int]:
    """Rank değerlerini 1, 2, 3... sırasına çevirir."""
    return {v: i + 1 for i, v in enumerate(sorted(set(values)))}


def rule_conflicts(items: list[CargoItem], rules: Rules, vehicle: Optional[Vehicle] = None) -> list[Violation]:
    """Planlamadan önce kural/teslimat sırası çelişkilerini bulur."""
    out: list[Violation] = []
    if rules.mode in ("FIFO", "LIFO"):
        stops = {i.delivery_stop for i in items}
        if len(stops) > 1:
            for a in items:
                for b in items:
                    if a is b:
                        continue
                    ra, rb = unload_rank(a, rules.mode), unload_rank(b, rules.mode)
                    if a.delivery_stop < b.delivery_stop and ra > rb + EPS:
                        out.append(Violation(
                            "KURAL ÇELİŞKİSİ", "uyari",
                            f"'{a.code}' {a.delivery_stop}. durakta teslim edilecek, ancak {rules.mode} kuralına göre "
                            f"'{b.code}' ({b.delivery_stop}. durak) yükünden SONRA çıkarılabilir. "
                            f"Plan {rules.mode} kuralına göre yapılacak; teslimat sırası korunamaz. "
                            "Teslimat sırasını korumak için 'Teslimat sırasına göre' kuralını seçin.",
                        ))
    if rules.mode == "FIFO" and vehicle is not None:
        prios = {i.loading_priority for i in items}
        only_rear = all(d.kind == "arka" for d in vehicle.doors)
        if len(prios) > 1 and only_rear:
            out.append(Violation(
                "KURAL ÇELİŞKİSİ", "uyari",
                f"{vehicle.name} yalnızca arka kapılı. Arka kapıdan yüklenen araçta ilk yüklenen yük en içte kalır; "
                "FIFO ancak farklı öncelikteki yükler birbirinin önüne geçmeyecek şekilde (yan yana şeritlerde) "
                "yerleştirilebilirse uygulanabilir. Bu mümkün olmazsa bazı yükler yerleştirilemez.",
            ))
    return out


# ----------------------------------------------------------------------------
# Geometri
# ----------------------------------------------------------------------------

def overlap_1d(a0, a1, b0, b1):
    """Açık aralık örtüşmesi (temas örtüşme sayılmaz). NumPy dizileriyle de çalışır."""
    return (a0 < b1 - EPS) & (a1 > b0 + EPS)


def boxes_overlap_mask(arr: np.ndarray, b) -> np.ndarray:
    """arr: (N, 6) [x0, y0, z0, x1, y1, z1]. b: 6'lı. Hacim örtüşmesi maskesi."""
    if arr.shape[0] == 0:
        return np.zeros(0, dtype=bool)
    return (
        overlap_1d(arr[:, 0], arr[:, 3], b[0], b[3])
        & overlap_1d(arr[:, 1], arr[:, 4], b[1], b[4])
        & overlap_1d(arr[:, 2], arr[:, 5], b[2], b[5])
    )


def contact_areas(arr: np.ndarray, b) -> np.ndarray:
    """``b`` kutusunun tabanına temas eden (üst yüzü b.z0 seviyesinde olan) kutularla XY temas alanları."""
    if arr.shape[0] == 0:
        return np.zeros(0)
    top_ok = np.abs(arr[:, 5] - b[2]) < 1e-4
    ox = np.clip(np.minimum(arr[:, 3], b[3]) - np.maximum(arr[:, 0], b[0]), 0, None)
    oy = np.clip(np.minimum(arr[:, 4], b[4]) - np.maximum(arr[:, 1], b[1]), 0, None)
    return np.where(top_ok, ox * oy, 0.0)


def support_ok(arr: np.ndarray, b, min_ratio: float) -> tuple[bool, np.ndarray, str]:
    """Taban desteği kontrolü. (uygun mu, temas alanları, sebep)."""
    if b[2] < EPS:
        return True, np.zeros(arr.shape[0]), ""
    areas = contact_areas(arr, b)
    base = (b[3] - b[0]) * (b[4] - b[1])
    total = float(areas.sum())
    if total < EPS:
        return False, areas, "altında destek yok (havada)"
    if total + 1e-6 < min_ratio * base:
        return False, areas, f"taban desteği %{100 * total / base:.0f} (en az %{100 * min_ratio:.0f} gerekli)"
    idx = np.nonzero(areas > EPS)[0]
    cx, cy = (b[0] + b[3]) / 2, (b[1] + b[4]) / 2
    sx0 = np.maximum(arr[idx, 0], b[0]).min()
    sx1 = np.minimum(arr[idx, 3], b[3]).max()
    sy0 = np.maximum(arr[idx, 1], b[1]).min()
    sy1 = np.minimum(arr[idx, 4], b[4]).max()
    if not (sx0 - EPS <= cx <= sx1 + EPS and sy0 - EPS <= cy <= sy1 + EPS):
        return False, areas, "ağırlık merkezi destek alanının dışında"
    return True, areas, ""


def segregation_conflict_mask(arr: np.ndarray, b, sep: float) -> np.ndarray:
    """``arr`` içindeki kutulardan ``b`` ile ayrım kuralını bozanlar.

    Kural: taban izdüşümleri üst üste gelemez (istif yok) ve iki kutu arasındaki
    yatay (XY) mesafe ``sep`` değerinden az olamaz.
    """
    if arr.shape[0] == 0:
        return np.zeros(0, dtype=bool)
    gx = np.maximum(0.0, np.maximum(arr[:, 0] - b[3], b[0] - arr[:, 3]))
    gy = np.maximum(0.0, np.maximum(arr[:, 1] - b[4], b[1] - arr[:, 4]))
    footprint_overlap = overlap_1d(arr[:, 0], arr[:, 3], b[0], b[3]) & overlap_1d(arr[:, 1], arr[:, 4], b[1], b[4])
    return footprint_overlap | (np.hypot(gx, gy) < sep - EPS)


def door_usable(vehicle: Vehicle, door, b) -> bool:
    """Kutu bu kapıdan (kapı açıklığı ve konum olarak) geçirilebilir mi?"""
    w, h = vehicle.door_size(door)
    dx, dy, dz = b[3] - b[0], b[4] - b[1], b[5] - b[2]
    if b[5] > h + EPS:
        return False
    if door.kind == "arka":
        return dy <= w + EPS and dz <= h + EPS
    return b[0] >= door.x_start - EPS and b[3] <= door.x_end + EPS and dx <= w + EPS and dz <= h + EPS


def corridor_mask(door, b, arr: np.ndarray) -> np.ndarray:
    """``b`` kutusunun bu kapıya giden düz çıkış koridorunda bulunan kutular."""
    if arr.shape[0] == 0:
        return np.zeros(0, dtype=bool)
    zo = overlap_1d(arr[:, 2], arr[:, 5], b[2], b[5])
    if door.kind == "arka":
        return (arr[:, 0] >= b[3] - EPS) & overlap_1d(arr[:, 1], arr[:, 4], b[1], b[4]) & zo
    xo = overlap_1d(arr[:, 0], arr[:, 3], b[0], b[3])
    if door.kind == "yan_sol":
        return (arr[:, 4] <= b[1] + EPS) & xo & zo
    return (arr[:, 1] >= b[4] - EPS) & xo & zo  # yan_sag


def zone_arrays(vehicle: Vehicle, rules: Rules) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Yasak bölgeler (kullanıcı tanımlı + kapı önü boşluğu). (kutular, geçişi_keser, isimler)."""
    boxes, blocks, names = [], [], []
    for z in vehicle.zones:
        boxes.append(z.bounds())
        blocks.append(z.blocks_passage)
        names.append(z.name)
    if rules.rear_clearance_cm > 0 and any(d.kind == "arka" for d in vehicle.doors):
        c = min(rules.rear_clearance_cm, vehicle.length)
        boxes.append((vehicle.length - c, 0, 0, vehicle.length, vehicle.width, vehicle.height))
        blocks.append(False)
        names.append("Arka kapı önü boşluğu")
    arr = np.array(boxes, dtype=float).reshape(-1, 6)
    return arr, np.array(blocks, dtype=bool), names


# ----------------------------------------------------------------------------
# Ağırlık dağılımı
# ----------------------------------------------------------------------------

def weight_analysis(vehicle: Vehicle, placements: list[Placement]) -> dict:
    total = sum(p.weight for p in placements)
    res: dict = {"total_weight": total, "cog": None, "axle": None}
    if total <= 0:
        return res
    cx = sum(p.weight * (p.x + p.dx / 2) for p in placements) / total
    cy = sum(p.weight * (p.y + p.dy / 2) for p in placements) / total
    cz = sum(p.weight * (p.z + p.dz / 2) for p in placements) / total
    res["cog"] = (cx, cy, cz)
    a = vehicle.axles
    if a.has_geometry():
        span = a.rear_x - a.front_x
        rear = total * (cx - a.front_x) / span
        front = total - rear
        res["axle"] = {"front": front, "rear": rear}
    return res


def _weight_violations(vehicle: Vehicle, placements: list[Placement]) -> tuple[list[Violation], dict]:
    out: list[Violation] = []
    wa = weight_analysis(vehicle, placements)
    if wa["cog"] is None:
        return out, wa
    cx, cy, cz = wa["cog"]
    lat = cy - vehicle.width / 2
    if abs(lat) > 0.10 * vehicle.width:
        out.append(Violation("AĞIRLIK DAĞILIMI", "uyari",
                             f"Yük ağırlık merkezi kasa ortasından {abs(lat):.0f} cm {'sağa' if lat > 0 else 'sola'} kaymış "
                             f"(genişliğin %{100 * abs(lat) / vehicle.width:.0f}'i). Yanal dengeyi kontrol edin."))
    if cz > 0.5 * vehicle.height:
        out.append(Violation("AĞIRLIK DAĞILIMI", "uyari",
                             f"Yük ağırlık merkezi yüksek (Z = {cz:.0f} cm). Devrilme riskini değerlendirin."))
    a = vehicle.axles
    if wa["axle"] is None:
        out.append(Violation("AKS", "uyari",
                             "Aks konumları girilmediği için aks yükü hesaplanmadı. Güvenlik sonucu verilemez."))
        return out, wa
    f, r = wa["axle"]["front"], wa["axle"]["rear"]
    if f < 0 or r < 0:
        out.append(Violation("AKS", "uyari",
                             "Yük ağırlık merkezi aks açıklığının dışında; bir aks yüksüz kalabilir. Yerleşimi gözden geçirin."))
    limits_known = a.front_payload_limit_kg is not None and a.rear_payload_limit_kg is not None
    if a.front_payload_limit_kg is not None and f > a.front_payload_limit_kg + EPS:
        out.append(Violation("AKS", "hata",
                             f"{a.front_label} faydalı yük payı yaklaşık {f:.0f} kg; girilen sınır {a.front_payload_limit_kg:.0f} kg."))
    if a.rear_payload_limit_kg is not None and r > a.rear_payload_limit_kg + EPS:
        out.append(Violation("AKS", "hata",
                             f"{a.rear_label} faydalı yük payı yaklaşık {r:.0f} kg; girilen sınır {a.rear_payload_limit_kg:.0f} kg."))
    if not limits_known:
        out.append(Violation("AKS", "uyari",
                             f"Aks yükleri yaklaşık hesaplandı ({a.front_label}: {f:.0f} kg, {a.rear_label}: {r:.0f} kg, yalnız faydalı yük). "
                             "Aks başına izin verilen faydalı yük girilmediği için uygunluk değerlendirilmedi."))
    return out, wa


# ----------------------------------------------------------------------------
# Bağımsız doğrulama
# ----------------------------------------------------------------------------

def _arr(placements: list[Placement]) -> np.ndarray:
    return np.array([p.bounds() for p in placements], dtype=float).reshape(-1, 6)


def _label(p: Placement) -> str:
    return f"{p.uid} ({p.item.name})"


def verify_plan(vehicle: Vehicle, placements: list[Placement], rules: Rules,
                detailed_sequence: bool = False) -> tuple[list[Violation], dict]:
    """Bir yerleşim planını baştan doğrular.

    Ayrıca yerleşimlerin ``load_step`` / ``unload_step`` alanlarını doldurur.
    ``detailed_sequence=True`` ise boşaltma tek tek (kapıya en yakın / en üstteki
    yük önce) simüle edilir; daha okunur bir yükleme sırası verir ama daha yavaştır.
    (ihlaller, ağırlık analizi) döndürür.
    """
    v: list[Violation] = []
    n = len(placements)
    arr = _arr(placements)
    zarr, zblocks, znames = zone_arrays(vehicle, rules)
    L, W, H = vehicle.length, vehicle.width, vehicle.height
    ranks = np.array([unload_rank(p.item, rules.mode) for p in placements], dtype=float)
    for p, r in zip(placements, ranks):
        p.rank = float(r)

    # 1) Kasa sınırları
    for p in placements:
        if p.x < -EPS or p.y < -EPS or p.z < -EPS or p.x1 > L + EPS or p.y1 > W + EPS or p.z1 > H + EPS:
            v.append(Violation("SINIR", "hata",
                               f"{_label(p)} araç iç sınırlarının dışına taşıyor "
                               f"(X {p.x:.0f}-{p.x1:.0f}, Y {p.y:.0f}-{p.y1:.0f}, Z {p.z:.0f}-{p.z1:.0f} cm).", [p.uid]))

    # 2) Çakışma
    for i in range(n):
        m = boxes_overlap_mask(arr[i + 1:], arr[i])
        for j in np.nonzero(m)[0] + i + 1:
            v.append(Violation("ÇAKIŞMA", "hata",
                               f"{_label(placements[i])} ile {_label(placements[j])} aynı hacmi işgal ediyor.",
                               [placements[i].uid, placements[j].uid]))

    # 3) Yasak bölgeler
    for p, b in zip(placements, arr):
        for k in np.nonzero(boxes_overlap_mask(zarr, b))[0]:
            v.append(Violation("YASAK BÖLGE", "hata", f"{_label(p)} '{znames[k]}' bölgesine giriyor.", [p.uid]))

    # 4) Destek, istif kuralları
    supporters: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for i, (p, b) in enumerate(zip(placements, arr)):
        if p.z < EPS:
            continue
        ok, areas, why = support_ok(arr, b, rules.min_support_ratio)
        if not ok:
            v.append(Violation("DESTEK", "hata", f"{_label(p)} yeterince desteklenmiyor: {why}.", [p.uid]))
        tot = float(areas.sum())
        for j in np.nonzero(areas > EPS)[0]:
            if j == i:
                continue
            supporters[i].append((int(j), float(areas[j]) / tot))
            s = placements[j]
            if not s.item.supports_load:
                why2 = "kırılabilir" if s.item.fragile else "üstüne yük konulamaz"
                v.append(Violation("İSTİF", "hata", f"{_label(s)} {why2}, ancak üzerinde {_label(p)} var.", [s.uid, p.uid]))
            if not p.item.stackable:
                v.append(Violation("İSTİF", "hata", f"{_label(p)} istiflenemez (başka yükün üstüne konulamaz) ama {_label(s)} üzerinde.", [p.uid, s.uid]))
            if not rules.allow_mixed_stop_stacking and abs(ranks[i] - ranks[j]) > EPS:
                v.append(Violation("İSTİF", "hata",
                                   f"{_label(p)} farklı boşaltma sırasındaki {_label(s)} üzerine konulmuş (karışık istif kapalı).",
                                   [p.uid, s.uid]))
            if p.weight > s.weight * 1.5 and p.weight - s.weight > 20:
                v.append(Violation("İSTİF", "uyari",
                                   f"Ağır yük hafif yükün üzerinde: {_label(p)} {p.weight:.0f} kg, altındaki {_label(s)} {s.weight:.0f} kg.",
                                   [p.uid, s.uid]))

    # 5) İstif dayanımı: üstten alta yük aktarımı
    load_on = np.zeros(n)
    for i in sorted(range(n), key=lambda k: -placements[k].z):
        carried = placements[i].weight + load_on[i]
        for j, share in supporters[i]:
            load_on[j] += carried * share
    for j, p in enumerate(placements):
        lim = p.item.max_top_load
        if lim is not None and p.item.supports_load and load_on[j] > lim + 1e-6:
            v.append(Violation("İSTİF DAYANIMI", "hata",
                               f"{_label(p)} üzerine yaklaşık {load_on[j]:.0f} kg biniyor; izin verilen {lim:.0f} kg.", [p.uid]))

    # 5b) Ayrım (birlikte taşınamaz yük grupları)
    if rules.incompatible:
        for i in range(n):
            for j in range(i + 1, n):
                a, c = placements[i], placements[j]
                if rules.are_incompatible(a.item, c.item) and segregation_conflict_mask(arr[j:j + 1], arr[i], rules.separation_cm)[0]:
                    v.append(Violation(
                        "AYRIM", "hata",
                        f"{_label(a)} ('{a.item.segregation_group}') ile {_label(c)} ('{c.item.segregation_group}') "
                        f"ayrı tutulmalı: üst üste konulamaz ve aralarında en az {rules.separation_cm:.0f} cm olmalı.",
                        [a.uid, c.uid]))

    # 6) Toplam ağırlık
    total_w = float(sum(p.weight for p in placements))
    if total_w > vehicle.max_payload_kg + EPS:
        v.append(Violation("AĞIRLIK", "hata",
                           f"Toplam yük {total_w:.0f} kg, aracın azami yük kapasitesi {vehicle.max_payload_kg:.0f} kg."))

    # 7) Kapı erişimi ve boşaltma simülasyonu
    v += _access_simulation(vehicle, placements, arr, ranks, supporters, zarr, zblocks, rules, detailed_sequence)

    # 8) Ağırlık dağılımı / aks
    wv, wa = _weight_violations(vehicle, placements)
    v += wv
    return v, wa


def _access_simulation(vehicle, placements, arr, ranks, supporters, zarr, zblocks, rules,
                       one_at_a_time: bool = False) -> list[Violation]:
    n = len(placements)
    out: list[Violation] = []
    on_top = [set() for _ in range(n)]
    for i in range(n):
        for j, _ in supporters[i]:
            on_top[j].add(i)
    # Her yük için kullanılabilir kapılar ve kapı koridorundaki yükler
    door_info: list[list[set[int]]] = []
    blocked_zone = zarr[zblocks] if zarr.shape[0] else zarr
    for i, b in enumerate(arr):
        lst = []
        for d in vehicle.doors:
            if not door_usable(vehicle, d, b):
                continue
            if blocked_zone.shape[0] and corridor_mask(d, b, blocked_zone).any():
                continue
            m = corridor_mask(d, b, arr)
            m[i] = False
            lst.append(set(np.nonzero(m)[0].tolist()))
        door_info.append(lst)
        if not lst:
            out.append(Violation("KAPI", "hata",
                                 f"{_label(placements[i])} hiçbir kapıdan çıkarılamıyor (kapı açıklığı, yan kapı aralığı veya "
                                 "teker davlumbazı gibi sabit engel nedeniyle).", [placements[i].uid]))

    remaining = set(range(n))
    seq: list[int] = []

    def blockers(i: int) -> set[int]:
        top = on_top[i] & remaining
        if not door_info[i]:
            return top | {-1}
        best = min((c & remaining for c in door_info[i]), key=len)
        return top | best

    for r in sorted(set(ranks.tolist())):
        group = {i for i in remaining if abs(ranks[i] - r) <= EPS}
        while group:
            free = [i for i in group if not blockers(i)]
            if free:
                free.sort(key=lambda i: (-arr[i, 3], -arr[i, 5], arr[i, 1]))
                if one_at_a_time:
                    free = free[:1]
                seq += free
                remaining -= set(free)
                group -= set(free)
                continue
            # Takılma: kurala aykırı biçimde engellenen, kapıya en yakın yükü
            # zorla çıkar ve ihlal yaz. (Kapısı hiç olmayan yük KAPI ihlaliyle raporlandı.)
            def bad_of(k: int) -> list[int]:
                return sorted(m for m in blockers(k) if m >= 0 and is_bad_blocker(ranks[m], ranks[k], rules.mode))

            stuck = [k for k in group if bad_of(k)]
            if stuck:
                i = max(stuck, key=lambda k: (arr[k, 3], arr[k, 5]))
            else:
                i = min(group, key=lambda k: (len(blockers(k)), -arr[k, 3]))
            bad = bad_of(i)
            if bad:
                p = placements[i]
                names = ", ".join(_label(placements[k]) for k in bad[:6]) + (" ..." if len(bad) > 6 else "")
                out.append(Violation(
                    "ERİŞİM", "hata",
                    f"{_label(p)} boşaltma sırası geldiğinde erişilemiyor; önce şu yükler indirilmeli: {names}. "
                    "Öneri: bu yükleri aracın ön tarafına alın veya boşaltma sırasını gözden geçirin.",
                    [p.uid] + [placements[k].uid for k in bad]))
            seq.append(i)
            remaining.discard(i)
            group.discard(i)

    for step, i in enumerate(seq, start=1):
        placements[i].unload_step = step
        placements[i].load_step = n - step + 1

    if rules.mode == "FIFO":
        # Yükleme sırası = boşaltma sırası olmalı; önündeki/üstündeki yük farklı öncelikteyse çelişki.
        reported = set()
        for i in range(n):
            for j in on_top[i] | (min(door_info[i], key=len) if door_info[i] else set()):
                if abs(ranks[i] - ranks[j]) > EPS and (i, j) not in reported:
                    reported.add((i, j))
                    a, b = placements[i], placements[j]
                    out.append(Violation(
                        "SIRA (FIFO)", "hata",
                        f"FIFO çelişkisi: {_label(b)} (öncelik {b.item.loading_priority}) {_label(a)} "
                        f"(öncelik {a.item.loading_priority}) yükünün önünde/üstünde. Biri yüklemede ya da boşaltmada "
                        "diğerinin yolunu keser.", [a.uid, b.uid]))
    return out
