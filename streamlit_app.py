"""LoadPlanner TR - Streamlit arayüzü.

Çalıştırma:  streamlit run streamlit_app.py
"""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path

import pandas as pd
import streamlit as st

from loadplanner.constraints import rule_conflicts
from loadplanner.io_csv import (
    fleet_placements_csv,
    items_to_csv,
    parse_items_csv,
    placements_dataframe,
    unplaced_csv,
)
from loadplanner.models import (
    BODY_TYPES,
    CARGO_TYPES,
    PALLET_PRESETS,
    OBJECTIVES,
    RULE_MODES,
    TRANSPORT_MODES,
    AxleConfig,
    CargoItem,
    Door,
    Rules,
    Vehicle,
    Zone,
    access_text,
    doors_for_body,
)
from loadplanner.manual import add_unplaced, move_placement, remove_placement, suggest_position, swap_placements
from loadplanner.optimizer import compare_vehicles, fmt_num
from loadplanner.pdf_report import build_loading_pdf
from loadplanner.container import lcl_breakdown, vgm_summary
from loadplanner.state_codec import decode_extra, decode_state, encode_state
from loadplanner.validation import validate_items, validate_vehicle
from loadplanner.vehicle_data import default_vehicles, get_default_vehicle
from loadplanner.visualization import figure_2d, figure_3d

EXAMPLE_CSV = Path(__file__).parent / "examples" / "ornek_yukler.csv"
LCL_DEFAULTS = {"rate": None, "currency": "USD", "min_wm": 1.0}
ITEM_DEFAULTS = {f.name: (f.default if f.default is not dataclasses.MISSING else "") for f in dataclasses.fields(CargoItem)}

LIMITATIONS = """
**Bu sürümün sınırları:**
- Yerleşim sezgisel bir algoritmayla bulunur; en iyi çözümün bulunduğu garanti edilmez. Karma filo seçimi de
  sezgiseldir (her adımda en uygun aracı seçer).
- Yük sabitleme (spanzet, kilit, sürtünme), fren/viraj kuvvetleri ve dinamik yükler **hesaplanmaz**; sistem
  yalnız kayma riski taşıyan boşlukları gösterir.
- Aks yükleri yalnızca aks konumları girildiyse, iki destekli basit modelle ve yalnız faydalı yük için
  **yaklaşık** hesaplanır. Aks başına izin verilen faydalı yük girilmediyse uygunluk değerlendirilmez.
- İstif dayanımı girilen "üzerine konulabilecek azami ağırlık" değerine göre, girilmemişse yükün kendi
  ağırlığı kadar kabul edilerek kontrol edilir.
- Tenteli araçta yandan erişim düz koridor modeliyle değerlendirilir (forklift yükü yana doğru çeker).
- Rulo ve özel şekilli yükler çevreleyen dikdörtgen kutu olarak modellenir.
- Demo araç ölçüleri temsilidir; gerçek aracın iç ölçüleri ve ruhsattaki istiap haddi girilmelidir.
- Yük planı bir karar destek çıktısıdır; yükleme güvenliğinin sorumluluğu operasyondadır.
"""

st.set_page_config(page_title="LoadPlanner TR", page_icon="🚚", layout="wide")
st.markdown(
    """<style>
    .block-container {padding-top: 1.2rem; padding-left: 0.8rem; padding-right: 0.8rem; max-width: 1200px;}
    div[data-testid="stTabs"] button {padding: 0.3rem 0.6rem;}
    @media (max-width: 640px) { h1 {font-size: 1.5rem;} }
    </style>""",
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# Durum
# ----------------------------------------------------------------------------

def init_state() -> None:
    ss = st.session_state
    if "items" not in ss and "d" in st.query_params:
        # Sayfa yenilendi veya paylaşılan bağlantı açıldı: girdileri adresten geri yükle.
        try:
            ss["items"], ss["rules"], ss["vehicles"], avail = decode_state(st.query_params["d"])
            if avail:
                ss["avail"] = avail
            extra = decode_extra(st.query_params["d"])
            if extra.get("tm") in TRANSPORT_MODES:
                ss["transport_mode"] = extra["tm"]
            if isinstance(extra.get("lcl"), dict):
                ss["lcl"] = {**LCL_DEFAULTS, **extra["lcl"]}
            ss["saved_state"] = st.query_params["d"]
        except ValueError as e:
            ss.setdefault("flash", []).append(("warning", f"{e}. Boş liste ile başlanıyor."))
    ss.setdefault("items", [])
    ss.setdefault("vehicles", default_vehicles())
    ss.setdefault("rules", Rules())
    ss.setdefault("results", None)
    ss.setdefault("result_sig", None)
    ss.setdefault("objective", "min_arac")
    ss.setdefault("flash", [])
    ss.setdefault("transport_mode", "karayolu")
    ss.setdefault("lcl", dict(LCL_DEFAULTS))


def signature() -> str:
    ss = st.session_state
    return repr((ss["items"], ss["vehicles"], ss["rules"], sorted(availability().items()), ss.get("allow_mixed", True),
                 ss.get("transport_mode", "karayolu")))


def flash(msg: str, kind: str = "success") -> None:
    st.session_state["flash"].append((kind, msg))


def show_flash() -> None:
    for kind, msg in st.session_state["flash"]:
        getattr(st, kind)(msg)
    st.session_state["flash"] = []


def pct(x: float) -> str:
    return f"%{100 * x:.1f}".replace(".", ",")


# ----------------------------------------------------------------------------
# 1. Araç seçimi
# ----------------------------------------------------------------------------

def _num(v) -> str:
    return fmt_num(float(v)) if v is not None else "-"


def vehicle_tab() -> None:
    vehicles: dict[str, Vehicle] = st.session_state["vehicles"]
    st.subheader("Araç profilleri")
    st.warning(
        "Hazır profillerdeki ölçü ve kapasiteler **DEMO / temsili değerlerdir**, üretici verisi değildir. "
        "Gerçek planlama için aracınızın iç ölçülerini, ruhsattaki **istiap haddini** ve biliyorsanız "
        "aks bilgilerini girin."
    )
    show_containers = st.radio("Göster", ["Karayolu araçları", "Konteynerler"], horizontal=True,
                               index=1 if st.session_state["transport_mode"] != "karayolu" else 0,
                               key="vehicle_filter") == "Konteynerler"
    shown = {k: v for k, v in vehicles.items() if v.is_container == show_containers}
    rows = []
    for v in shown.values():
        dims = " + ".join(f"{b.length:.0f}×{b.width:.0f}×{b.height:.0f}" for b in v.bodies)
        rows.append({
            "Araç": v.name, "Erişim": " / ".join(access_text(b) for b in v.bodies),
            "İç ölçü (cm)": dims, "Hacim (m³)": round(v.total_volume_cm3 / 1e6, 1),
            "İstiap (kg)": _num(v.total_payload_kg),
            **({"Dara (kg)": _num(v.tare_kg), "Azami brüt (kg)": _num(v.max_gross_kg)} if show_containers else {}),
            ("Navlun" if show_containers else "Sefer (TL)"): _num(v.trip_cost),
            "Veri": "DEMO" if v.is_demo else "Kullanıcı",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    vid = st.selectbox("Düzenlenecek profil", list(shown), format_func=lambda k: vehicles[k].name)
    v = vehicles[vid]
    if v.notes:
        st.caption(v.notes)

    with st.form(f"veh_{vid}"):
        name = st.text_input("Profil adı", v.name)
        body_types = list(BODY_TYPES)
        body = st.selectbox("Kasa tipi", body_types, index=body_types.index(v.body_type) if v.body_type in body_types else 0,
                            format_func=BODY_TYPES.get,
                            help="Kasa tipi kapıları belirler: tenteli araçta iki yandan forkliftle erişim vardır; "
                                 "frigorifik araç soğuk zincir yüklerini taşıyabilir. 'Özel' seçilirse kapılar "
                                 "aşağıdaki 'Kapılar' bölümünden alınır.")
        c1, c2 = st.columns(2)
        length = c1.number_input("İç uzunluk (cm)", min_value=0.0, value=float(v.length), step=5.0)
        width = c2.number_input("İç genişlik (cm)", min_value=0.0, value=float(v.width), step=5.0)
        c1, c2 = st.columns(2)
        height = c1.number_input("İç yükseklik (cm)", min_value=0.0, value=float(v.height), step=5.0)
        if v.is_container:
            tare = c2.number_input("Dara (kg) - CSC plakası", min_value=0.0, value=float(v.tare_kg or 0), step=10.0)
            gross = st.number_input("Azami brüt ağırlık (kg) - CSC plakası", min_value=0.0,
                                    value=float(v.max_gross_kg or 0), step=100.0,
                                    help="Taşınabilecek yük = azami brüt - dara. VGM ön hesabında kullanılır.")
            payload = max(gross - tare, 0.0)
            st.caption(f"Taşınabilecek yük: {fmt_num(payload)} kg")
        else:
            payload = c2.number_input("İstiap haddi / azami faydalı yük (kg)", min_value=0.0,
                                      value=float(v.max_payload_kg), step=50.0)
        cost = st.number_input("Navlun (konteyner başı, isteğe bağlı)" if v.is_container else
                               "Sefer maliyeti (TL, isteğe bağlı)", min_value=0.0, step=500.0,
                               value=None if v.trip_cost is None else float(v.trip_cost),
                               help="Girilirse 'Minimum maliyet' hedefi ve karma filo seçiminde kullanılır.")

        tr = v.trailer
        if tr is not None:
            st.markdown("**Römork**")
            c1, c2 = st.columns(2)
            t_len = c1.number_input("Römork iç uzunluk (cm)", min_value=0.0, value=float(tr.length), step=5.0)
            t_wid = c2.number_input("Römork iç genişlik (cm)", min_value=0.0, value=float(tr.width), step=5.0)
            c1, c2 = st.columns(2)
            t_hei = c1.number_input("Römork iç yükseklik (cm)", min_value=0.0, value=float(tr.height), step=5.0)
            t_pay = c2.number_input("Römork istiap haddi (kg)", min_value=0.0, value=float(tr.max_payload_kg), step=50.0)
            t_body = st.selectbox("Römork kasa tipi", body_types[:3],
                                  index=body_types.index(tr.body_type) if tr.body_type in body_types[:3] else 0,
                                  format_func=BODY_TYPES.get)

        door_box = st.expander("Kapılar (yalnız kasa tipi 'Özel' ise kullanılır)", expanded=False)
        rear = next((d for d in v.doors if d.kind == "arka"), None)
        left = next((d for d in v.doors if d.kind == "yan_sol"), None)
        right = next((d for d in v.doors if d.kind == "yan_sag"), None)
        has_rear = door_box.checkbox("Arka kapı var", rear is not None)
        c1, c2 = door_box.columns(2)
        rear_w = c1.number_input("Arka kapı açıklık genişliği (cm, 0 = kasa genişliği)", min_value=0.0,
                                 value=float(rear.width or 0) if rear else 0.0)
        rear_h = c2.number_input("Arka kapı açıklık yüksekliği (cm, 0 = kasa yüksekliği)", min_value=0.0,
                                 value=float(rear.height or 0) if rear else 0.0)
        side = {}
        for kind, d, label in (("yan_sag", right, "Sağ yan kapı"), ("yan_sol", left, "Sol yan kapı")):
            on = door_box.checkbox(f"{label} var", d is not None, key=f"{vid}_{kind}_on")
            c1, c2, c3 = door_box.columns(3)
            xs = c1.number_input(f"{label} başlangıç X (cm)", min_value=0.0, value=float(d.x_start) if d else 0.0, key=f"{vid}_{kind}_xs")
            xe = c2.number_input(f"{label} bitiş X (cm)", min_value=0.0, value=float(d.x_end) if d else 100.0, key=f"{vid}_{kind}_xe")
            hh = c3.number_input(f"{label} yükseklik (cm, 0 = kasa)", min_value=0.0, value=float(d.height or 0) if d else 0.0, key=f"{vid}_{kind}_h")
            side[kind] = (on, xs, xe, hh)

        axle_box = st.expander("Aks bilgisi (isteğe bağlı)", expanded=False)
        axle_box.caption("Konumlar kasa ön duvarına göre cm; ön aks kasanın önündeyse negatif. "
                         "İzin verilen faydalı yük = izin verilen aks yükü - boş araç aks yükü.")
        a = v.axles
        use_axle = axle_box.checkbox("Aks konumlarını kullan", a.has_geometry())
        c1, c2 = axle_box.columns(2)
        fx = c1.number_input(f"{a.front_label} konumu (cm)", value=float(a.front_x if a.front_x is not None else -100.0))
        rx = c2.number_input(f"{a.rear_label} konumu (cm)", value=float(a.rear_x if a.rear_x is not None else length * 0.6))
        c1, c2 = axle_box.columns(2)
        fl = c1.number_input("Ön aksa izin verilen faydalı yük (kg, 0 = bilinmiyor)", min_value=0.0,
                             value=float(a.front_payload_limit_kg or 0))
        rl = c2.number_input("Arka aksa izin verilen faydalı yük (kg, 0 = bilinmiyor)", min_value=0.0,
                             value=float(a.rear_payload_limit_kg or 0))

        zone_box = st.expander(f"Yasak bölgeler / boş bırakılacak alanlar ({len(v.zones)})", expanded=False)
        zone_box.caption("Teker davlumbazı, sabitleme alanı vb. Ölçüler cm; x, y, z bölgenin en küçük köşesi.")
        zdf = pd.DataFrame([{"ad": z.name, "x": z.x, "y": z.y, "z": z.z, "dx": z.dx, "dy": z.dy, "dz": z.dz,
                             "geçişi_keser": z.blocks_passage} for z in v.zones],
                           columns=["ad", "x", "y", "z", "dx", "dy", "dz", "geçişi_keser"])
        zdf = zone_box.data_editor(zdf, num_rows="dynamic", width="stretch", key=f"zones_{vid}",
                                   column_config={"geçişi_keser": st.column_config.CheckboxColumn(
                                       help="İşaretliyse fiziksel engeldir; yükün kapıya giden yolunu da keser.")})
        is_demo = st.checkbox("Bu profil DEMO değer içeriyor", v.is_demo)
        saved = st.form_submit_button("Profili kaydet", type="primary")

    if saved:
        if body == "ozel":
            doors = []
            if has_rear:
                doors.append(Door("arka", width=rear_w or None, height=rear_h or None))
            for kind, (on, xs, xe, hh) in side.items():
                if on:
                    doors.append(Door(kind, x_start=xs, x_end=xe, height=hh or None))
        else:
            doors = doors_for_body(body, length)
        zones = []
        zerr = []
        for _, r in zdf.iterrows():
            try:
                if pd.isna(r["dx"]) and pd.isna(r["ad"]):
                    continue
                zones.append(Zone(str(r["ad"] or "Bölge"), float(r["x"]), float(r["y"]), float(r["z"]),
                                  float(r["dx"]), float(r["dy"]), float(r["dz"]), bool(r["geçişi_keser"])))
            except (TypeError, ValueError):
                zerr.append(f"Bölge satırı geçersiz: {dict(r)}")
        axles = AxleConfig(front_label=a.front_label, rear_label=a.rear_label)
        if use_axle:
            axles.front_x, axles.rear_x = fx, rx
            axles.front_payload_limit_kg = fl or None
            axles.rear_payload_limit_kg = rl or None
        trailer = None
        if tr is not None:
            trailer = copy.deepcopy(tr)
            trailer.length, trailer.width, trailer.height, trailer.max_payload_kg = t_len, t_wid, t_hei, t_pay
            trailer.body_type, trailer.doors = t_body, doors_for_body(t_body, t_len)
        nv = Vehicle(id=vid, name=name, category=v.category, length=length, width=width, height=height,
                     max_payload_kg=payload, doors=doors, zones=zones, axles=axles, is_demo=is_demo, notes=v.notes,
                     body_type=body, trailer=trailer, trip_cost=cost, body_label=v.body_label,
                     is_container=v.is_container,
                     tare_kg=tare if v.is_container else v.tare_kg,
                     max_gross_kg=gross if v.is_container else v.max_gross_kg)
        errs = zerr + validate_vehicle(nv)
        if errs:
            for e in errs:
                st.error(e)
        else:
            vehicles[vid] = nv
            flash(f"'{name}' profili kaydedildi.")
            st.rerun()
    if vid != "ozel" and st.button("Bu profili demo değerlere döndür"):
        vehicles[vid] = get_default_vehicle(vid)
        flash("Profil demo değerlere döndürüldü.", "info")
        st.rerun()


# ----------------------------------------------------------------------------
# 2. Yük listesi
# ----------------------------------------------------------------------------

def item_fields(prefix: str, it: CargoItem) -> dict:
    """Yük formu alanları. Sık kullanılanlar üstte, nadir olanlar 'Diğer özellikler' altında."""
    c1, c2 = st.columns(2)
    d = {}
    d["code"] = c1.text_input("Ürün kodu *", it.code, key=f"{prefix}_code")
    d["name"] = c2.text_input("Yük adı", it.name, key=f"{prefix}_name")
    types = list(CARGO_TYPES)
    c1, c2 = st.columns(2)
    d["cargo_type"] = c1.selectbox("Yük tipi", types, index=types.index(it.cargo_type) if it.cargo_type in types else 1,
                                   format_func=CARGO_TYPES.get, key=f"{prefix}_type")
    d["quantity"] = c2.number_input("Adet", min_value=0, step=1, value=int(it.quantity), key=f"{prefix}_qty")
    presets = ["elle"] + list(PALLET_PRESETS)
    preset = st.selectbox("Palet tabanı", presets, key=f"{prefix}_preset",
                          format_func=lambda k: "Ölçüyü elle gir" if k == "elle" else PALLET_PRESETS[k][0],
                          help="Seçilirse boy ve en otomatik dolar; yüksekliği yükle birlikte (palet dahil) girin.")
    st.caption("Dış ölçüler (cm). Palet için **palet dahil** toplam dış ölçüyü girin.")
    c3, c4, c5 = st.columns(3)
    d["length"] = c3.number_input("Boy", min_value=0.0, value=float(it.length), step=1.0, key=f"{prefix}_L")
    d["width"] = c4.number_input("En", min_value=0.0, value=float(it.width), step=1.0, key=f"{prefix}_W")
    d["height"] = c5.number_input("Yükseklik", min_value=0.0, value=float(it.height), step=1.0, key=f"{prefix}_H")
    if preset != "elle":
        d["length"], d["width"] = PALLET_PRESETS[preset][1], PALLET_PRESETS[preset][2]
    c1, c2 = st.columns(2)
    d["unit_weight"] = c1.number_input("Birim ağırlık (kg)", min_value=0.0, value=float(it.unit_weight), step=1.0, key=f"{prefix}_kg")
    d["delivery_stop"] = c2.number_input("Teslimat sırası (1 = ilk durak)", min_value=1, step=1,
                                         value=int(it.delivery_stop), key=f"{prefix}_stop")
    lcl_mode = st.session_state.get("transport_mode") == "lcl"
    if lcl_mode:
        d["shipper"] = st.text_input("Gönderici (LCL)", it.shipper, key=f"{prefix}_shp",
                                     help="Konteyneri paylaşan müşteri. Ücret dökümü göndericiye göre yapılır.")
    c1, c2, c3 = st.columns(3)
    d["allow_on_top"] = c1.checkbox("Üstüne yük konabilir", it.allow_on_top, key=f"{prefix}_aot")
    d["fragile"] = c2.checkbox("Kırılabilir / hassas", it.fragile, key=f"{prefix}_frg")
    d["cold_chain"] = c3.checkbox("Soğuk zincir", it.cold_chain, key=f"{prefix}_cold",
                                  help="İşaretlenirse yük yalnız frigorifik araca yüklenir.")
    with st.expander("Diğer özellikler (istif dayanımı, döndürme, öncelik, notlar)"):
        d["max_top_load"] = st.number_input(
            "Üzerine konulabilecek azami ağırlık (kg)", min_value=0.0, key=f"{prefix}_top",
            value=None if it.max_top_load is None else float(it.max_top_load),
            help="Boş bırakılırsa yükün kendi ağırlığı kadar kabul edilir (aynı yükten biri üstüne konabilir, "
                 "daha ağırı konamaz).")
        c1, c2 = st.columns(2)
        d["stackable"] = c1.checkbox("Başka yükün üstüne konabilir", it.stackable, key=f"{prefix}_stk")
        d["rotatable"] = c2.checkbox("90° döndürülebilir (dikey eksen)", it.rotatable, key=f"{prefix}_rot")
        c1, c2 = st.columns(2)
        d["allow_tipping"] = c1.checkbox("Yan yatırılabilir", it.allow_tipping, key=f"{prefix}_tip")
        d["includes_pallet"] = c2.checkbox("Ölçüler palet dahil", it.includes_pallet, key=f"{prefix}_pal")
        d["loading_priority"] = st.number_input("Yükleme önceliği (1 = ilk yüklenen; yalnız FIFO/LIFO kuralında)",
                                                min_value=1, step=1, value=int(it.loading_priority), key=f"{prefix}_prio")
        d["segregation_group"] = st.text_input(
            "Ayrım grubu (ör. gıda, kimyasal)", it.segregation_group, key=f"{prefix}_seg",
            help="Hangi grupların birlikte taşınamayacağı 'Kurallar' sekmesinde tanımlanır.")
        d["notes"] = st.text_input("Sabitleme / taşıma notları", it.notes, key=f"{prefix}_notes")
        if not lcl_mode:
            d["shipper"] = st.text_input("Gönderici (konteyner LCL için)", it.shipper, key=f"{prefix}_shp")
    d["code"] = d["code"].strip()
    d["name"] = d["name"].strip() or d["code"]
    return d


def check_and_build(d: dict, others: list[CargoItem]) -> CargoItem | None:
    it = CargoItem(**d)
    errors, warnings = validate_items(others + [it])
    if errors:
        for e in errors:
            st.error(e)
        return None
    for w in warnings:
        if it.code in w:
            st.info(w)
    return it


def items_tab() -> None:
    items: list[CargoItem] = st.session_state["items"]
    st.subheader(f"Yük listesi ({len(items)} satır, {sum(i.quantity for i in items)} adet)")
    st.caption("Liste sayfa adresine otomatik kaydedilir; sayfayı yenileyince geri gelir. "
               "Adresi yer imlerine ekleyerek veya paylaşarak listeyi saklayabilirsiniz. "
               "Hesaplama sonuçları saklanmaz; yenilemeden sonra yeniden hesaplayın.")

    with st.expander("➕ Yeni yük ekle", expanded=not items):
        with st.form("add_item", clear_on_submit=False):
            tmpl = CargoItem(code=f"Y{len(items) + 1}", name="", cargo_type="koli", quantity=1,
                             length=60, width=40, height=40, unit_weight=10)
            d = item_fields(f"new{len(items)}", tmpl)
            add = st.form_submit_button("Listeye ekle", type="primary")
        if add:
            it = check_and_build(d, items)
            if it:
                items.append(it)
                flash(f"'{it.code}' eklendi.")
                st.rerun()

    c1, c2 = st.columns(2)
    if c1.button("Örnek yük listesini yükle"):
        new, errs = parse_items_csv(EXAMPLE_CSV.read_bytes())
        st.session_state["items"] = new
        flash("Örnek yük listesi yüklendi.", "info")
        st.rerun()
    if items and c2.button("Listeyi temizle"):
        st.session_state["items"] = []
        st.rerun()

    for idx, it in enumerate(items):
        flags = "".join([" ❄️" if it.cold_chain else "", " ⚠️kırılır" if it.fragile else "",
                         f" · {it.shipper}" if it.shipper else ""])
        with st.expander(f"{it.code} - {it.name} × {it.quantity}  |  {it.length:.0f}×{it.width:.0f}×{it.height:.0f} cm, "
                         f"{it.unit_weight:.0f} kg, durak {it.delivery_stop}{flags}"):
            with st.form(f"edit_{idx}_{it.code}"):
                d = item_fields(f"e{idx}_{it.code}", it)
                c1, c2 = st.columns(2)
                save = c1.form_submit_button("Kaydet", type="primary")
                delete = c2.form_submit_button("Sil")
            if save:
                new = check_and_build(d, items[:idx] + items[idx + 1:])
                if new:
                    items[idx] = new
                    flash(f"'{new.code}' güncellendi.")
                    st.rerun()
            if delete:
                items.pop(idx)
                flash(f"'{it.code}' silindi.", "info")
                st.rerun()

    if items:
        with st.expander("📋 Tablo görünümü (masaüstünde toplu düzenleme)"):
            df = pd.DataFrame([vars(i) for i in items])
            edited = st.data_editor(df, num_rows="dynamic", width="stretch", key="bulk_editor",
                                    column_config={"cargo_type": st.column_config.SelectboxColumn(options=list(CARGO_TYPES))})
            if st.button("Tablodaki değişiklikleri uygula"):
                new_items, errs = [], []
                for n, r in edited.iterrows():
                    try:
                        rec = {}
                        for k, v in r.to_dict().items():
                            if k not in ITEM_DEFAULTS:
                                continue
                            if v is None or (not isinstance(v, str) and pd.isna(v)):
                                v = ITEM_DEFAULTS[k]  # boş hücre: alanın varsayılanı (NaN 'doğru' sayılmasın)
                            rec[k] = v.item() if hasattr(v, "item") else v
                        for k in ("quantity", "delivery_stop", "loading_priority"):
                            rec[k] = int(rec[k])
                        for k in ("code", "name", "notes", "segregation_group"):
                            rec[k] = "" if pd.isna(rec[k]) else str(rec[k])
                        new_items.append(CargoItem(**rec))
                    except (TypeError, ValueError) as e:
                        errs.append(f"Satır {n + 1}: {e}")
                e2, _ = validate_items(new_items)
                errs += e2
                if errs:
                    for e in errs:
                        st.error(e)
                else:
                    st.session_state["items"] = new_items
                    flash("Tablo değişiklikleri uygulandı.")
                    st.rerun()

    st.markdown("#### CSV içe aktar")
    st.caption("Ayırıcı ',' veya ';' olabilir; ondalık virgül desteklenir. Zorunlu sütunlar: "
               "kod, adet, boy_cm, en_cm, yukseklik_cm, birim_agirlik_kg.")
    st.download_button("Örnek CSV şablonunu indir", EXAMPLE_CSV.read_bytes(), "ornek_yukler.csv", "text/csv")
    up = st.file_uploader("Yük listesi CSV", type=["csv", "txt"])
    mode = st.radio("İçe aktarma şekli", ["Mevcut listeye ekle", "Listeyi değiştir"], horizontal=True)
    if up is not None and st.button("CSV'yi içe aktar", type="primary"):
        new, errs = parse_items_csv(up.getvalue())
        for e in errs:
            st.error(e)
        if new:
            base = [] if mode == "Listeyi değiştir" else list(items)
            e2, w2 = validate_items(base + new)
            if e2:
                st.error("İçe aktarılan liste geçersiz; hiçbir satır eklenmedi:")
                for e in e2:
                    st.error(e)
            else:
                st.session_state["items"] = base + new
                flash(f"{len(new)} satır içe aktarıldı." + (f" {len(errs)} satır hatalı olduğu için atlandı." if errs else ""))
                st.rerun()


# ----------------------------------------------------------------------------
# 3. Yükleme kuralları
# ----------------------------------------------------------------------------

def rules_tab() -> None:
    r: Rules = st.session_state["rules"]
    st.subheader("Yükleme ve boşaltma kuralları")
    mode = st.radio("Boşaltma sırası", list(RULE_MODES), format_func=RULE_MODES.get,
                    index=list(RULE_MODES).index(r.mode))
    with st.expander("Bu seçenekler ne demek?"):
        st.markdown(
            "- **Teslimat sırası:** yükler *teslimat sırası* alanına göre boşaltılır (1. durak ilk); 1. durağın yükü "
            "kapıya en yakın konur. Yükleme sırası bundan geriye doğru hesaplanır.\n"
            "- **LIFO:** *yükleme önceliği* büyük olan (son yüklenen) ilk çıkar. Arka kapılı araçların doğal düzenidir.\n"
            "- **Gönderici bir arada (LCL):** 'Gelişmiş kurallar' altında; farklı göndericilerin yükleri üst üste "
            "konmaz, aynı göndericininkiler arka arkaya yüklenir.\n"
            "- **FIFO:** ilk yüklenen ilk çıkar. Tek arka kapılı araçta ancak farklı öncelikteki yükler birbirinin "
            "önüne geçmezse (yan yana şeritler) mümkündür; tenteli araçta yandan erişim işi kolaylaştırır.\n\n"
            "Her durumda gerçek erişim kontrol edilir: bir yükü çıkarmak için önce başka bir yükü indirmek "
            "gerekiyorsa bu **erişim ihlali** olarak raporlanır.")

    with st.expander("Gelişmiş kurallar"):
        mixed = st.checkbox("Farklı duraklara ait yükler üst üste konabilir", r.allow_mixed_stop_stacking,
                            help="Kapalıyken (önerilen) her istif tek bir durağın yüklerinden oluşur.")
        center = st.checkbox("Kısmi yükü kasa ortasına doğru kaydır (aks dengesi)", r.center_partial_load,
                             help="Yük kasayı doldurmuyorsa bütün yük kapıya doğru kaydırılarak ağırlık merkezi "
                                  "kasa ortasına yaklaştırılır. Önde kalan boşluk takoz/boş paletle doldurulmalıdır.")
        shipper_rule = st.checkbox("Aynı göndericinin yükleri bir arada (LCL)", r.keep_shipper_together,
                                   help="Farklı göndericilerin yükleri üst üste konmaz; her göndericinin yükleri "
                                        "arka arkaya yüklenerek blok oluşturur.")
        sup = st.slider("Üstteki yük için asgari taban desteği (%)", 50, 100, int(r.min_support_ratio * 100), 5)
        clear = st.number_input("Arka kapı önünde boş bırakılacak mesafe (cm)", min_value=0.0,
                                value=float(r.rear_clearance_cm), step=10.0)

    st.markdown("#### Birlikte taşınamaz yük grupları")
    st.caption("Yüklere 'Ayrım grubu' verin (ör. gıda, kimyasal). Burada eşleştirilen gruplar üst üste konulmaz "
               "ve aralarında en az aşağıdaki yatay mesafe bırakılır. Her satıra bir çift yazın: gıda, kimyasal")
    groups = sorted({i.segregation_group.strip() for i in st.session_state["items"] if i.segregation_group.strip()})
    if groups:
        st.caption("Listedeki gruplar: " + ", ".join(groups))
    pairs_text = st.text_area("Uyumsuz grup çiftleri", "\n".join(f"{a}, {b}" for a, b in r.incompatible),
                              placeholder="gıda, kimyasal")
    sep = st.number_input("Uyumsuz yükler arasında en az yatay mesafe (cm)", min_value=0.0,
                          value=float(r.separation_cm), step=10.0)
    if pairs_text.strip() and sep == 0:
        st.caption("Mesafe 0 cm: uyumsuz yükler yalnızca üst üste konmaz, yan yana bitişik durabilir. "
                   "Aralarında boşluk istiyorsanız mesafe girin (ör. 50 cm).")
    pairs, bad_lines = [], []
    for line in pairs_text.splitlines():
        if not line.strip():
            continue
        parts = [x.strip() for x in line.replace(";", ",").split(",") if x.strip()]
        if len(parts) == 2 and parts[0].lower() != parts[1].lower():
            pairs.append(tuple(parts))
        else:
            bad_lines.append(line)
    for line in bad_lines:
        st.error(f"Anlaşılamayan satır: '{line}'. Biçim: grup1, grup2")
    new = Rules(mode=mode, allow_mixed_stop_stacking=mixed, min_support_ratio=sup / 100, rear_clearance_cm=clear,
                incompatible=tuple(pairs), separation_cm=sep, center_partial_load=center,
                keep_shipper_together=shipper_rule)
    if new != r:
        st.session_state["rules"] = new
        st.rerun()
    items = st.session_state["items"]
    if items:
        conf = rule_conflicts(items, new)
        for c in conf[:10]:
            st.warning(c.message)
        if len(conf) > 10:
            st.warning(f"... ve {len(conf) - 10} çelişki daha.")


# ----------------------------------------------------------------------------
# 4. Optimizasyon
# ----------------------------------------------------------------------------

DEFAULT_AVAILABLE = 3


def availability() -> dict[str, int]:
    ss = st.session_state
    av = ss.setdefault("avail", {})
    for k in ss["vehicles"]:
        av.setdefault(k, 0 if k == "ozel" else DEFAULT_AVAILABLE)
    return av


def optimize_tab() -> None:
    ss = st.session_state
    vehicles: dict[str, Vehicle] = ss["vehicles"]
    st.subheader("Araç seçimi ve hesaplama")
    modes = list(TRANSPORT_MODES)
    tm = st.radio("Taşıma şekli", modes, format_func=TRANSPORT_MODES.get, horizontal=True,
                  index=modes.index(ss["transport_mode"]))
    if tm != ss["transport_mode"]:
        ss["transport_mode"] = tm
        if tm == "lcl" and not ss["rules"].keep_shipper_together:
            ss["rules"] = dataclasses.replace(ss["rules"], keep_shipper_together=True)
            flash("LCL için 'Aynı göndericinin yükleri bir arada' kuralı açıldı (Kurallar > Gelişmiş).", "info")
        st.rerun()
    containers = tm != "karayolu"
    if tm == "fcl":
        st.caption("FCL: yükleriniz için en uygun konteyner tipi ve adedi seçilir; konteyner başı navlun "
                   "girilirse maliyete göre karşılaştırılır. Her konteyner için VGM ön hesabı gösterilir.")
    elif tm == "lcl":
        st.caption("LCL: farklı göndericilerin yükleri ortak konteynerde toplanır. Yüklere 'Gönderici' girin; "
                   "ücret dökümü W/M esasına göre (max(m³, ton)) göndericiye göre yapılır.")
        lcl = ss["lcl"]
        c1, c2, c3 = st.columns(3)
        rate = c1.number_input("W/M birim fiyatı", min_value=0.0, step=1.0, value=lcl["rate"],
                               help="1 W/M (1 m³ veya 1 ton, hangisi büyükse) için ücret.")
        cur = c2.text_input("Para birimi", lcl["currency"])
        min_wm = c3.number_input("Asgari W/M", min_value=0.0, step=0.5, value=float(lcl["min_wm"]))
        ss["lcl"] = {"rate": rate, "currency": cur.strip() or "USD", "min_wm": min_wm}
        missing = [i.code for i in ss["items"] if not i.shipper.strip()]
        if missing:
            st.warning(f"{len(missing)} yük satırında gönderici girilmemiş ({', '.join(missing[:5])}"
                       f"{' ...' if len(missing) > 5 else ''}). Bunlar dökümde ayrı satırda görünür.")
    obj = st.radio("Öncelik", list(OBJECTIVES), format_func=OBJECTIVES.get,
                   index=list(OBJECTIVES).index(ss["objective"]), horizontal=True)
    ss["objective"] = obj

    st.markdown("**Kullanılabilir konteynerler**" if containers else "**Kullanılabilir araçlar**")
    st.caption("Her tipten kaç adet kullanılabileceğini (0 = kullanma) ve biliyorsanız "
               + ("konteyner başı navlunu" if containers else "sefer maliyetini")
               + " girin. Maliyet 'Minimum maliyet' önceliğinde ve karma seçimde kullanılır.")
    av = availability()
    mode_ids = [k for k, v in vehicles.items() if v.is_container == containers]
    cost_col = "Navlun / konteyner" if containers else "Maliyet (TL)"
    df = pd.DataFrame([{"id": k, "Araç": vehicles[k].name, "Adet": int(av.get(k, 0)), cost_col: vehicles[k].trip_cost}
                       for k in mode_ids])
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key=f"avail_editor_{tm}",
        column_config={"id": None, "Araç": st.column_config.TextColumn(disabled=True),
                       "Adet": st.column_config.NumberColumn(min_value=0, max_value=50, step=1, required=True),
                       cost_col: st.column_config.NumberColumn(min_value=0, step=100, format="%.0f",
                                                               help="Sefer / konteyner başı maliyet (isteğe bağlı)")})
    for _, row in edited.iterrows():
        k = row["id"]
        n = int(row["Adet"]) if not pd.isna(row["Adet"]) else 0
        av[k] = max(n, 0)
        c = None if pd.isna(row[cost_col]) else float(row[cost_col])
        if c != vehicles[k].trip_cost:
            vehicles[k] = copy.deepcopy(vehicles[k])
            vehicles[k].trip_cost = c
    allow_mixed = st.checkbox("Farklı konteyner tiplerini birlikte kullanabilir" if containers else
                              "Farklı araç tiplerini birlikte kullanabilir (karma filo)", value=ss.get("allow_mixed", True))
    ss["allow_mixed"] = allow_mixed
    with st.expander("Gelişmiş"):
        depth = st.selectbox("Arama derinliği", ["hizli", "normal", "kapsamli"], index=1,
                             format_func={"hizli": "Hızlı", "normal": "Normal (önerilen)", "kapsamli": "Kapsamlı (yavaş)"}.get)
    sel = [k for k in mode_ids if av.get(k, 0) > 0]

    if st.button("🚚 Yerleşimi hesapla", type="primary", width="stretch"):
        errs, warns = validate_items(ss["items"])
        for v in sel:
            errs += validate_vehicle(vehicles[v])
        if not sel:
            errs.append("En az bir " + ("konteyner" if containers else "araç") + " tipine 0'dan büyük adet girin.")
        if errs:
            for e in errs:
                st.error(e)
            return
        if warns:
            with st.expander(f"Girdi uyarıları ({len(warns)})", expanded=len(warns) <= 2):
                for w in warns:
                    st.info(w)
        bar = st.progress(0.0, text="Hazırlanıyor...")
        res = compare_vehicles([copy.deepcopy(vehicles[v]) for v in sel], ss["items"], ss["rules"],
                               objective=obj, depth=depth, availability={k: av[k] for k in sel},
                               allow_mixed=allow_mixed,
                               progress=lambda f, t: bar.progress(min(max(f, 0.0), 1.0), text=t))
        bar.empty()
        ss["results"] = res
        ss["results_original"] = copy.deepcopy(res)
        ss["result_sig"] = signature()
        ss["view_idx"] = 0

    res = ss["results"]
    if not res:
        st.info("Henüz hesaplama yapılmadı.")
        return
    if ss["result_sig"] != signature():
        st.warning("Girdiler son hesaplamadan sonra değişti. Sonuçlar güncel değil; yeniden hesaplayın.")
    best = res[0]
    m = best.metrics
    if m["unplaced_count"] == 0 and m["error_count"] == 0:
        st.success(f"Önerilen: **{best.label}**")
    else:
        st.error(f"Bütün yükler kurallara uygun yerleştirilemedi. En iyi seçenek: **{best.label}**. "
                 "Ayrıntılar 'Rapor' sekmesinde.")
    c = st.columns(4)
    c[0].metric("Araç", m["n_vehicles"])
    c[1].metric("Yerleşen yük", f"{m['placed_count']}/{m['total_units']}")
    c[2].metric("Yükleme metresi", f"{fmt_num(m['ldm'], 1)} LDM")
    is_cont = any(p.vehicle.is_container for p in best.plans)
    c[3].metric("Navlun toplamı" if is_cont else "Sefer maliyeti",
                (fmt_num(m["total_cost"]) + ("" if is_cont else " TL")) if m["total_cost"] is not None else "-")
    st.write(best.explanation)
    for note in best.notes:
        st.warning(note)
    container_summaries(best)
    rows = []
    for i, f in enumerate(res):
        fm = f.metrics
        rows.append({
            "Seçenek": f"{i + 1}. {f.label}" + (" (karma)" if f.mixed else ""),
            "Yerleşen": f"{fm['placed_count']}/{fm['total_units']}",
            "Hacim": pct(fm["volume_util"]), "Ağırlık": pct(fm["weight_util"]),
            "LDM": fmt_num(fm["ldm"], 1),
            "Maliyet (TL)": fmt_num(fm["total_cost"]) if fm["total_cost"] is not None else "-",
            "İhlal": fm["error_count"],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Hacim ve ağırlık oranı kullanılan araçların toplam kapasitesine göredir; tek başına başarı ölçütü "
               "değildir. LDM: kaplanan taban alanının 2,40 m genişliğe göre yükleme metresi karşılığı.")
    ss["view_idx"] = st.selectbox("İncelenecek seçenek (3D, rapor ve dışa aktarma için)", range(len(res)),
                                  index=min(ss.get("view_idx", 0), len(res) - 1),
                                  format_func=lambda i: f"{i + 1}. {res[i].label}")


def container_summaries(fleet) -> None:
    """Konteyner seçeneklerinde VGM; LCL modunda gönderici ücret dökümü."""
    ss = st.session_state
    vgm = vgm_summary(fleet)
    if vgm:
        st.markdown("**VGM ön hesabı (brüt ağırlık)**")
        st.dataframe(pd.DataFrame([{
            "Konteyner": f"{r.unit_no}. {r.container}", "Dara (kg)": fmt_num(r.tare_kg), "Yük (kg)": fmt_num(r.cargo_kg),
            "Brüt / VGM (kg)": fmt_num(r.gross_kg), "Azami brüt (kg)": fmt_num(r.max_gross_kg) if r.max_gross_kg else "-",
            "Durum": "uygun" if r.ok else "AŞIYOR"} for r in vgm]), hide_index=True, width="stretch")
        st.caption("Ön hesaptır: istif/sabitleme malzemesi dahil değildir. Resmi VGM, SOLAS'a göre tartımla veya "
                   "bütün paket, palet ve sabitleme malzemesi ağırlıkları toplanarak (Yöntem 2) beyan edilir.")
    if ss.get("transport_mode") == "lcl":
        lcl = ss["lcl"]
        rows = lcl_breakdown(ss["items"], fleet, lcl["rate"], lcl["min_wm"])
        cur = lcl["currency"]
        st.markdown("**LCL gönderici dökümü (W/M)**")
        st.dataframe(pd.DataFrame([{
            "Gönderici": r.shipper, "Adet": r.units, "Ağırlık (kg)": fmt_num(r.weight_kg), "Hacim (m³)": fmt_num(r.cbm, 2),
            "W/M": fmt_num(r.wm, 2), f"Ücret ({cur})": fmt_num(r.charge, 2) if r.charge is not None else "-",
            "Konteyner": r.containers + (f" ({r.unplaced} yerleşmedi)" if r.unplaced else "")} for r in rows]),
            hide_index=True, width="stretch")
        total = sum(r.charge for r in rows if r.charge is not None)
        st.caption(f"W/M = max(hacim m³, ağırlık ton), en az {fmt_num(lcl['min_wm'], 1)}. "
                   + (f"Toplam: {fmt_num(total, 2)} {cur}." if lcl["rate"] is not None else
                      "Ücret için W/M birim fiyatını girin."))


def current_fleet():
    res = st.session_state["results"]
    if not res:
        st.info("Önce 'Optimizasyon' sekmesinde hesaplama yapın.")
        return None
    return res[min(st.session_state.get("view_idx", 0), len(res) - 1)]


# ----------------------------------------------------------------------------
# 5. 3D yerleşim
# ----------------------------------------------------------------------------

def view_tab() -> None:
    fleet = current_fleet()
    if fleet is None:
        return
    if not fleet.plans:
        st.error("Bu araç tipine hiçbir yük yerleştirilemedi.")
        return
    rules = st.session_state["rules"]
    st.subheader(fleet.label)
    k = 0
    if len(fleet.plans) > 1:
        def plan_label(i: int) -> str:
            p = fleet.plans[i]
            name = p.vehicle.body_label or (p.unit_name or p.vehicle.name)
            return f"Araç {p.unit_no} · {name}" + (" (boş)" if not p.placements else "")
        k = st.radio("Araç", range(len(fleet.plans)), format_func=plan_label, horizontal=True)
    plan = fleet.plans[k]
    n = len(plan.placements)
    if n == 0:
        st.info("Bu kasaya yük yerleştirilmedi.")
        manual_edit_panel(fleet, k)
        return
    c1, c2, c3 = st.columns(3)
    labels = c1.toggle("Etiketleri göster", value=n <= 60)
    color_by = c2.radio("Renk", ["kod", "durak"], format_func={"kod": "Ürün koduna göre", "durak": "Teslimat durağına göre"}.get)
    hl = c3.toggle("İhlalli yükleri kırmızı göster", value=True)
    step = st.slider("Yükleme adımı (adım adım yükleme görünümü)", 1, n, n) if n > 1 else n
    shown = sorted(plan.placements, key=lambda p: p.load_step)
    if step and step <= n:
        cur = shown[step - 1]
        st.caption(f"Adım {step}/{n}: **{cur.uid}** ({cur.item.name}) → X={cur.x:.0f}, Y={cur.y:.0f}, Z={cur.z:.0f} cm, "
                   f"{cur.orientation}")
    highlight = {u for v in plan.errors for u in v.uids} if hl else set()
    if plan.errors:
        st.error(f"Bu planda {len(plan.errors)} ihlal var. Ayrıntılar 'Sonuç Raporu' sekmesinde.")
    else:
        st.success("Çakışma, kasa dışı yük, destek/istif ve erişim kontrolleri: ihlal bulunmadı.")
    st.plotly_chart(figure_3d(plan, rules, step, labels, color_by, highlight), width="stretch")
    t1, t2, t3 = st.tabs(["Üstten", "Yandan", "Arkadan"])
    for t, vw in ((t1, "ust"), (t2, "yan"), (t3, "arka")):
        with t:
            st.plotly_chart(figure_2d(plan, rules, vw, step, color_by), width="stretch")
    st.markdown("#### Yük koordinatları")
    st.dataframe(placements_dataframe(plan), hide_index=True, width="stretch")
    manual_edit_panel(fleet, k)


def _after_edit(fleet, k: int, what: str) -> None:
    plan = fleet.plans[k]
    if plan.errors:
        flash(f"{what} Planda şimdi {len(plan.errors)} ihlal var; kırmızı yükleri ve raporu kontrol edin.", "warning")
    else:
        flash(f"{what} Yeniden kontrol edildi: ihlal yok.")
    st.rerun()


def manual_edit_panel(fleet, k: int) -> None:
    ss = st.session_state
    rules = ss["rules"]
    plan = fleet.plans[k]
    edits = fleet.metrics.get("manual_edits", 0)
    with st.expander("✏️ Yerleşimi elle düzenle", expanded=False):
        st.caption("Her değişiklikten sonra plan baştan kontrol edilir (çakışma, sınır, destek, istif, ayrım, "
                   "erişim). Hatalı değişiklik engellenmez, ihlal olarak gösterilir. Koordinatlar cm; "
                   "X=0 kabin tarafı, Y=0 sol duvar, Z=0 taban.")
        if edits:
            st.info(f"Bu planda {edits} elle değişiklik var.")
            if st.button("↩️ Otomatik plana geri dön", key=f"reset_{k}"):
                orig = ss.get("results_original")
                idx = ss.get("view_idx", 0)
                if orig:
                    ss["results"][idx] = copy.deepcopy(orig[idx])
                    flash("Otomatik plana geri dönüldü.", "info")
                    st.rerun()
        op = st.radio("İşlem", ["Taşı / döndür", "Yer değiştir", "Araçtan çıkar", "Yerleşmeyen yükü ekle"],
                      key=f"op_{k}", horizontal=True)
        pls = sorted(plan.placements, key=lambda p: p.load_step)
        label = {p.uid: f"{p.uid} - {p.item.name} (X{p.x:.0f} Y{p.y:.0f} Z{p.z:.0f})" for p in pls}
        sfx = f"{k}_{edits}"

        if op == "Taşı / döndür" and pls:
            uid = st.selectbox("Yük", [p.uid for p in pls], format_func=label.get, key=f"mv_uid_{sfx}")
            p = next(q for q in pls if q.uid == uid)
            ors = [o[3] for o in p.item.orientations()]
            ori = st.selectbox("Yönelim", ors, index=ors.index(p.orientation) if p.orientation in ors else 0,
                               key=f"mv_or_{sfx}_{uid}")
            c1, c2, c3 = st.columns(3)
            x = c1.number_input("X (cm)", value=float(p.x), step=10.0, key=f"mv_x_{sfx}_{uid}")
            y = c2.number_input("Y (cm)", value=float(p.y), step=10.0, key=f"mv_y_{sfx}_{uid}")
            z = c3.number_input("Z (cm)", value=float(p.z), step=10.0, key=f"mv_z_{sfx}_{uid}")
            c1, c2 = st.columns(2)
            if c1.button("Uygula", type="primary", key=f"mv_go_{sfx}"):
                move_placement(fleet, k, uid, x, y, z, ori, rules)
                _after_edit(fleet, k, f"{uid} taşındı.")
            if c2.button("Kurallara uygun ilk konuma taşı", key=f"mv_auto_{sfx}"):
                pos = suggest_position(plan, p.item, rules, exclude_uid=uid)
                if pos is None:
                    st.error("Bu yük için mevcut yerleşimi bozmadan kurallara uygun bir konum bulunamadı.")
                else:
                    move_placement(fleet, k, uid, pos[0], pos[1], pos[2], pos[3], rules)
                    _after_edit(fleet, k, f"{uid} önerilen konuma taşındı.")

        elif op == "Yer değiştir" and len(pls) >= 2:
            ids = [p.uid for p in pls]
            a = st.selectbox("1. yük", ids, format_func=label.get, key=f"sw_a_{sfx}")
            b = st.selectbox("2. yük", [i for i in ids if i != a], format_func=label.get, key=f"sw_b_{sfx}")
            st.caption("Yükler birbirinin köşe konumuna geçer; her yük kendi ölçüsünü korur.")
            if st.button("Yer değiştir", type="primary", key=f"sw_go_{sfx}"):
                swap_placements(fleet, k, a, b, rules)
                _after_edit(fleet, k, f"{a} ile {b} yer değiştirdi.")

        elif op == "Araçtan çıkar" and pls:
            uid = st.selectbox("Yük", [p.uid for p in pls], format_func=label.get, key=f"rm_uid_{sfx}")
            if st.button("Araçtan çıkar", type="primary", key=f"rm_go_{sfx}"):
                remove_placement(fleet, k, uid, rules)
                _after_edit(fleet, k, f"{uid} araçtan çıkarıldı ve 'yerleştirilemeyen' listesine alındı.")

        elif op == "Yerleşmeyen yükü ekle":
            if not fleet.unplaced:
                st.info("Yerleştirilemeyen yük yok.")
                return
            umap = {u.uid: u for u in fleet.unplaced}
            uid = st.selectbox("Yük", list(umap), format_func=lambda i: f"{i} - {umap[i].item.name}", key=f"ad_uid_{sfx}")
            u = umap[uid]
            st.caption(f"Neden yerleşmemişti: {u.reason}")
            if st.button("Kurallara uygun ilk konuma ekle", type="primary", key=f"ad_auto_{sfx}"):
                pos = suggest_position(plan, u.item, rules)
                if pos is None:
                    st.error("Bu araçta mevcut yerleşimi bozmadan kurallara uygun bir konum yok.")
                else:
                    add_unplaced(fleet, k, uid, pos[0], pos[1], pos[2], pos[3], rules)
                    _after_edit(fleet, k, f"{uid} araca eklendi.")
            st.markdown("veya konumu kendiniz girin:")
            ors = [o[3] for o in u.item.orientations()]
            ori = st.selectbox("Yönelim", ors, key=f"ad_or_{sfx}_{uid}")
            c1, c2, c3 = st.columns(3)
            x = c1.number_input("X (cm)", value=0.0, step=10.0, key=f"ad_x_{sfx}_{uid}")
            y = c2.number_input("Y (cm)", value=0.0, step=10.0, key=f"ad_y_{sfx}_{uid}")
            z = c3.number_input("Z (cm)", value=0.0, step=10.0, key=f"ad_z_{sfx}_{uid}")
            if st.button("Bu konuma ekle", key=f"ad_go_{sfx}"):
                add_unplaced(fleet, k, uid, x, y, z, ori, rules)
                _after_edit(fleet, k, f"{uid} araca eklendi.")


# ----------------------------------------------------------------------------
# 6. Sonuç raporu
# ----------------------------------------------------------------------------

def report_tab() -> None:
    fleet = current_fleet()
    if fleet is None:
        return
    ss = st.session_state
    m = fleet.metrics
    st.subheader("Sonuç raporu")
    if ss["result_sig"] != signature():
        st.warning("Girdiler değişti; rapor son hesaplamaya aittir.")
    if fleet.metrics.get("manual_edits"):
        st.info(f"Bu plan elle düzenlendi ({fleet.metrics['manual_edits']} değişiklik); aşağıdaki değerler "
                "düzenlenmiş plana göredir.")
    if fleet is ss["results"][0]:
        st.markdown(f"**Seçilen araç ve neden:** {fleet.explanation}")
    else:
        st.markdown(f"**İncelenen seçenek:** {fleet.label} (önerilen seçenek değil).")
    c = st.columns(3)
    c[0].metric("Toplam yük adedi", m["total_units"])
    c[1].metric("Toplam ağırlık", f"{fmt_num(m['total_weight_kg'])} kg")
    c[2].metric("Toplam hacim", f"{fmt_num(m['total_volume_m3'], 2)} m³")
    c = st.columns(3)
    c[0].metric("Araç sayısı", m["n_vehicles"])
    c[1].metric("Yerleşen / yerleşmeyen", f"{m['placed_count']} / {m['unplaced_count']}")
    c[2].metric("İhlal", m["error_count"])

    shown_conf = set()
    for plan in fleet.plans:
        for conf in rule_conflicts(ss["items"], ss["rules"], plan.vehicle):
            if conf.message not in shown_conf:
                shown_conf.add(conf.message)
                st.warning(conf.message)
    for note in fleet.notes:
        st.warning(note)
    container_summaries(fleet)

    for i, plan in enumerate(fleet.plans):
        pm = plan.metrics
        if not plan.placements:
            st.caption(f"{plan.title}: boş.")
            continue
        with st.expander(f"{plan.title} ({plan.unit_name or plan.vehicle.name}): {pm['placed_count']} yük, "
                         f"hacim {pct(pm['volume_util'])}, ağırlık {pct(pm['weight_util'])}, {len(plan.errors)} ihlal",
                         expanded=i == 0):
            c = st.columns(3)
            c[0].metric("Hacim kullanımı", pct(pm["volume_util"]))
            c[1].metric("Ağırlık kullanımı", pct(pm["weight_util"]))
            c[2].metric("Yükleme metresi", f"{fmt_num(pm.get('ldm', 0.0), 1)} LDM")
            st.caption(f"Yerleşen yük ağırlığı {fmt_num(pm['placed_weight_kg'])} / {fmt_num(plan.vehicle.max_payload_kg)} kg; "
                       f"kullanılan boy {pm['used_length_cm']:.0f} cm; en yüksek nokta {pm['max_height_cm']:.0f} cm; "
                       f"üst üste konan yük sayısı {pm['stacked_count']}; strateji: {plan.strategy}.")
            if pm.get("cog"):
                cx, cy, cz = pm["cog"]
                st.caption(f"Yük ağırlık merkezi (yaklaşık): X={cx:.0f}, Y={cy:.0f} (kasa ortası {plan.vehicle.width / 2:.0f}), Z={cz:.0f} cm.")
            if plan.errors:
                st.markdown("**İhlaller**")
                st.dataframe(pd.DataFrame([{"Tür": x.category, "Açıklama": x.message} for x in plan.errors]),
                             hide_index=True, width="stretch")
            else:
                st.success("Kasa sınırı, çakışma, yasak bölge, destek, istif, ağırlık ve kapı erişimi kontrollerinde ihlal yok.")
            if plan.warnings:
                st.markdown("**Uyarılar**")
                for x in plan.warnings:
                    st.warning(f"{x.category}: {x.message}")
            for x in plan.infos:
                st.caption(f"ℹ️ {x.category}: {x.message}")
            seq = sorted(plan.placements, key=lambda p: p.load_step)
            st.markdown("**Yükleme sırası:** " + " → ".join(p.uid for p in seq[:80]) + (" ..." if len(seq) > 80 else ""))
            useq = sorted(plan.placements, key=lambda p: p.unload_step)
            st.markdown("**Boşaltma sırası:** " + " → ".join(f"{p.uid} (durak {p.item.delivery_stop})" for p in useq[:80])
                        + (" ..." if len(useq) > 80 else ""))

    st.markdown("#### Yerleştirilemeyen yükler")
    if not fleet.unplaced:
        st.success("Bütün yükler yerleştirildi.")
    else:
        single = [u for u in fleet.unplaced if u.scope == "tekil"]
        together = [u for u in fleet.unplaced if u.scope != "tekil"]
        if single:
            st.error(f"{len(single)} yük bu araca **tek başına** sığmıyor/taşınamıyor (başka araç tipi gerekir).")
        if together:
            st.error(f"{len(together)} yük tek başına sığıyor, ancak **liste birlikte** sığmıyor veya kurallar nedeniyle yerleşmiyor.")
        st.dataframe(pd.DataFrame([{"Yük": u.uid, "Ad": u.item.name,
                                    "Kapsam": "Tek başına sığmıyor" if u.scope == "tekil" else "Birlikte sığmıyor",
                                    "Neden": u.reason} for u in fleet.unplaced]),
                     hide_index=True, width="stretch")
        st.markdown("**Olası çözümler:** araç sayısı sınırını artırın, daha büyük araç seçin, döndürme/istif "
                    "izinlerini gözden geçirin, karışık istife izin verin veya teslimat sırası kuralını kullanın.")
    st.markdown(LIMITATIONS)


# ----------------------------------------------------------------------------
# 7. Dışa aktarma
# ----------------------------------------------------------------------------

def export_tab() -> None:
    ss = st.session_state
    st.subheader("Dışa aktarma")
    if ss["items"]:
        st.download_button("Yük listesini CSV indir", items_to_csv(ss["items"]).encode("utf-8-sig"),
                           "yuk_listesi.csv", "text/csv")
    fleet = current_fleet()
    if fleet is None:
        return
    st.markdown("**Yükleme talimatı (PDF)** - şoför / depo için: kontrol durumu, adım numaralı üstten ve yandan "
                "çizim, adım adım yükleme listesi, durak bazında boşaltma, özel uyarılar. Telefondan paylaşılabilir.")
    if fleet.metrics.get("error_count"):
        st.warning("Bu planda kural ihlali var; PDF'in ilk sayfasında kırmızı uyarı olarak yazılır.")
    # PDF her yeniden çalıştırmada değil, istenince üretilir; plan değişirse önbellek geçersiz olur.
    key = (ss["result_sig"], ss.get("view_idx", 0), fleet.metrics.get("manual_edits", 0),
           repr(ss["rules"]), fleet.metrics.get("error_count", 0), id(fleet), repr(ss["lcl"]), ss["transport_mode"])
    cached = ss.get("pdf_cache")
    if cached and cached[0] == key:
        st.download_button("📄 Yükleme talimatını indir (PDF)", cached[1], "yukleme_talimati.pdf", "application/pdf",
                           type="primary")
    elif st.button("📄 Yükleme talimatı PDF'ini hazırla", type="primary"):
        try:
            with st.spinner("PDF hazırlanıyor..."):
                lcl = None
                if ss["transport_mode"] == "lcl":
                    lcl = {"items": ss["items"], **ss["lcl"]}
                ss["pdf_cache"] = (key, build_loading_pdf(fleet, ss["rules"], lcl=lcl))
        except Exception as e:  # PDF üretimi uygulamanın geri kalanını durdurmamalı
            st.error(f"PDF oluşturulamadı: {e}")
        else:
            st.rerun()
    st.markdown("**Veri dosyaları**")
    st.download_button("Yerleşim planı ve yük koordinatları (CSV)", fleet_placements_csv(fleet).encode("utf-8-sig"),
                       "yerlesim_plani.csv", "text/csv")
    if ss["transport_mode"] == "lcl":
        lcl = ss["lcl"]
        rows = lcl_breakdown(ss["items"], fleet, lcl["rate"], lcl["min_wm"])
        ldf = pd.DataFrame([{"gonderici": r.shipper, "adet": r.units, "agirlik_kg": round(r.weight_kg, 1),
                             "hacim_m3": round(r.cbm, 3), "wm": round(r.wm, 3),
                             f"ucret_{lcl['currency']}": r.charge, "konteyner": r.containers,
                             "yerlesmeyen": r.unplaced} for r in rows])
        st.download_button("LCL gönderici dökümü (CSV)", ldf.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "lcl_dokum.csv", "text/csv")
    vgm = vgm_summary(fleet)
    if vgm:
        vdf = pd.DataFrame([{"konteyner_no": r.unit_no, "konteyner": r.container, "dara_kg": r.tare_kg,
                             "yuk_kg": round(r.cargo_kg, 1), "brut_vgm_kg": round(r.gross_kg, 1),
                             "azami_brut_kg": r.max_gross_kg} for r in vgm])
        st.download_button("VGM ön hesabı (CSV)", vdf.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "vgm_on_hesap.csv", "text/csv")
    if fleet.unplaced:
        st.download_button("Yerleştirilemeyen yükler (CSV)", unplaced_csv(fleet).encode("utf-8-sig"),
                           "yerlesmeyen_yukler.csv", "text/csv")
    st.caption("CSV dosyaları ';' ayırıcı ve ondalık virgülle, Excel'in Türkçe ayarlarında doğrudan açılacak biçimdedir. "
               "Koordinatlar cm; X=0 kasa ön duvarı, Y=0 sol duvar, Z=0 taban; değerler yükün en küçük köşesidir.")


# ----------------------------------------------------------------------------

def main() -> None:
    init_state()
    st.title("🚚 LoadPlanner TR")
    st.caption("Araç içi yük yerleşim ve lojistik planlama - MVP. Sonuçlar karar desteği içindir; "
               "yük güvenliği kontrollerinin sınırları 'Sonuç Raporu' sekmesinde yazılıdır.")
    show_flash()
    tabs = st.tabs(["1. Araç", "2. Yükler", "3. Kurallar", "4. Optimizasyon", "5. 3D Yerleşim", "6. Rapor", "7. Dışa Aktar"],
                   key="main_tab", on_change="rerun")
    with tabs[0]:
        vehicle_tab()
    with tabs[1]:
        items_tab()
    with tabs[2]:
        rules_tab()
    with tabs[3]:
        optimize_tab()
    with tabs[4]:
        view_tab()
    with tabs[5]:
        report_tab()
    with tabs[6]:
        export_tab()
    save_state_to_url()


def save_state_to_url() -> None:
    """Girdileri adres satırına yazar; sayfa yenilenince init_state geri yükler."""
    ss = st.session_state
    av = availability()
    defaults = {k: (0 if k == "ozel" else DEFAULT_AVAILABLE) for k in ss["vehicles"]}
    changed_av = {k: n for k, n in av.items() if defaults.get(k) != n}
    extra = {}
    if ss["transport_mode"] != "karayolu":
        extra["tm"] = ss["transport_mode"]
    if ss["lcl"] != LCL_DEFAULTS:
        extra["lcl"] = ss["lcl"]
    enc = encode_state(ss["items"], ss["rules"], ss["vehicles"], changed_av, extra)
    if enc == ss.get("saved_state"):
        return
    ss["saved_state"] = enc
    empty = encode_state([], Rules(), default_vehicles())
    if enc == empty:
        st.query_params.pop("d", None)
    else:
        st.query_params["d"] = enc


main()
