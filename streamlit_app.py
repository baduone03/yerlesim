"""LoadPlanner TR - Streamlit arayüzü.

Çalıştırma:  streamlit run streamlit_app.py
"""

from __future__ import annotations

import copy
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
    CARGO_TYPES,
    OBJECTIVES,
    RULE_MODES,
    AxleConfig,
    CargoItem,
    Door,
    Rules,
    Vehicle,
    Zone,
)
from loadplanner.optimizer import compare_vehicles
from loadplanner.state_codec import decode_state, encode_state
from loadplanner.validation import validate_items, validate_vehicle
from loadplanner.vehicle_data import default_vehicles, get_default_vehicle
from loadplanner.visualization import figure_2d, figure_3d

EXAMPLE_CSV = Path(__file__).parent / "examples" / "ornek_yukler.csv"

LIMITATIONS = """
**Bu sürümün sınırları (MVP):**
- Yerleşim sezgisel bir algoritmayla bulunur; en iyi çözümün bulunduğu garanti edilmez.
- Yük sabitleme (spanzet, kilit, sürtünme), fren/viraj kuvvetleri ve dinamik yükler **hesaplanmaz**.
- Aks yükleri yalnızca aks konumları girildiyse, iki destekli basit modelle ve yalnız faydalı yük için
  **yaklaşık** hesaplanır. Aks başına izin verilen faydalı yük girilmediyse uygunluk değerlendirilmez.
- İstif dayanımı yalnızca girilen "üzerine konulabilecek azami ağırlık" değerine göre kontrol edilir.
- Rulo ve özel şekilli yükler çevreleyen dikdörtgen kutu olarak modellenir.
- Demo araç ölçüleri temsilidir; gerçek aracın iç ölçüleri ve ruhsattaki kapasite girilmelidir.
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
            ss["items"], ss["rules"], ss["vehicles"] = decode_state(st.query_params["d"])
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


def signature() -> str:
    ss = st.session_state
    return repr((ss["items"], ss["vehicles"], ss["rules"]))


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

def vehicle_tab() -> None:
    vehicles: dict[str, Vehicle] = st.session_state["vehicles"]
    st.subheader("Araç profilleri")
    st.warning(
        "Hazır profillerdeki ölçü ve kapasiteler **DEMO / temsili değerlerdir**, üretici verisi değildir. "
        "Gerçek planlama için aracınızın iç ölçülerini, ruhsattaki/teknik belgedeki yük kapasitesini "
        "ve biliyorsanız aks bilgilerini girin."
    )
    rows = [{
        "Araç": v.name, "İç boy (cm)": v.length, "İç en (cm)": v.width, "İç yükseklik (cm)": v.height,
        "Hacim (m³)": round(v.volume_cm3 / 1e6, 2), "Azami yük (kg)": v.max_payload_kg,
        "Kapılar": ", ".join({"arka": "arka", "yan_sol": "sol yan", "yan_sag": "sağ yan"}[d.kind] for d in v.doors),
        "Veri": "DEMO" if v.is_demo else "Kullanıcı",
    } for v in vehicles.values()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    vid = st.selectbox("Düzenlenecek profil", list(vehicles), format_func=lambda k: vehicles[k].name)
    v = vehicles[vid]
    if v.notes:
        st.caption(v.notes)

    with st.form(f"veh_{vid}"):
        name = st.text_input("Profil adı", v.name)
        c1, c2 = st.columns(2)
        length = c1.number_input("İç uzunluk (cm)", min_value=0.0, value=float(v.length), step=5.0)
        width = c2.number_input("İç genişlik (cm)", min_value=0.0, value=float(v.width), step=5.0)
        c1, c2 = st.columns(2)
        height = c1.number_input("İç yükseklik (cm)", min_value=0.0, value=float(v.height), step=5.0)
        payload = c2.number_input("Azami faydalı yük (kg) - ruhsattaki değer", min_value=0.0,
                                  value=float(v.max_payload_kg), step=50.0)

        door_box = st.expander("Kapılar", expanded=False)
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
        doors = []
        if has_rear:
            doors.append(Door("arka", width=rear_w or None, height=rear_h or None))
        for kind, (on, xs, xe, hh) in side.items():
            if on:
                doors.append(Door(kind, x_start=xs, x_end=xe, height=hh or None))
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
        nv = Vehicle(id=vid, name=name, category=v.category, length=length, width=width, height=height,
                     max_payload_kg=payload, doors=doors, zones=zones, axles=axles, is_demo=is_demo, notes=v.notes)
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
    c1, c2 = st.columns(2)
    d = {}
    d["code"] = c1.text_input("Ürün kodu *", it.code, key=f"{prefix}_code")
    d["name"] = c2.text_input("Yük adı", it.name, key=f"{prefix}_name")
    types = list(CARGO_TYPES)
    c1, c2 = st.columns(2)
    d["cargo_type"] = c1.selectbox("Yük tipi", types, index=types.index(it.cargo_type) if it.cargo_type in types else 1,
                                   format_func=CARGO_TYPES.get, key=f"{prefix}_type")
    d["quantity"] = c2.number_input("Adet", min_value=0, step=1, value=int(it.quantity), key=f"{prefix}_qty")
    st.caption("Dış ölçüler (cm). Palet için **palet dahil** toplam dış ölçüyü girin.")
    c3, c4, c5 = st.columns(3)
    d["length"] = c3.number_input("Boy", min_value=0.0, value=float(it.length), step=1.0, key=f"{prefix}_L")
    d["width"] = c4.number_input("En", min_value=0.0, value=float(it.width), step=1.0, key=f"{prefix}_W")
    d["height"] = c5.number_input("Yükseklik", min_value=0.0, value=float(it.height), step=1.0, key=f"{prefix}_H")
    c1, c2 = st.columns(2)
    d["unit_weight"] = c1.number_input("Birim ağırlık (kg)", min_value=0.0, value=float(it.unit_weight), step=1.0, key=f"{prefix}_kg")
    d["max_top_load"] = c2.number_input("Üzerine konulabilecek azami ağırlık (kg, boş = sınır yok)", min_value=0.0,
                                        value=None if it.max_top_load is None else float(it.max_top_load),
                                        key=f"{prefix}_top")
    c1, c2 = st.columns(2)
    d["allow_on_top"] = c1.checkbox("Üstüne başka yük konulabilir", it.allow_on_top, key=f"{prefix}_aot")
    d["stackable"] = c2.checkbox("İstiflenebilir (başka yükün üstüne konulabilir)", it.stackable, key=f"{prefix}_stk")
    c1, c2 = st.columns(2)
    d["rotatable"] = c1.checkbox("90° döndürülebilir (dikey eksen)", it.rotatable, key=f"{prefix}_rot")
    d["allow_tipping"] = c2.checkbox("Yan yatırılabilir (diğer yönler)", it.allow_tipping, key=f"{prefix}_tip")
    c1, c2 = st.columns(2)
    d["fragile"] = c1.checkbox("Kırılabilir / hassas", it.fragile, key=f"{prefix}_frg")
    d["includes_pallet"] = c2.checkbox("Ölçüler palet dahil", it.includes_pallet, key=f"{prefix}_pal")
    c1, c2 = st.columns(2)
    d["delivery_stop"] = c1.number_input("Teslimat sırası (1 = ilk durak)", min_value=1, step=1,
                                         value=int(it.delivery_stop), key=f"{prefix}_stop")
    d["loading_priority"] = c2.number_input("Yükleme önceliği (1 = ilk yüklenen)", min_value=1, step=1,
                                            value=int(it.loading_priority), key=f"{prefix}_prio")
    d["notes"] = st.text_input("Sabitleme / taşıma notları", it.notes, key=f"{prefix}_notes")
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
        with st.expander(f"{it.code} - {it.name} × {it.quantity}  |  {it.length:.0f}×{it.width:.0f}×{it.height:.0f} cm, "
                         f"{it.unit_weight:.0f} kg, durak {it.delivery_stop}"):
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
                        rec = {k: (None if (k == "max_top_load" and pd.isna(v)) else v) for k, v in r.to_dict().items()}
                        for k in ("quantity", "delivery_stop", "loading_priority"):
                            rec[k] = int(rec[k])
                        for k in ("code", "name", "notes"):
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
    mode = st.radio("Boşaltma sırası kuralı", list(RULE_MODES), format_func=RULE_MODES.get,
                    index=list(RULE_MODES).index(r.mode))
    st.info(
        "- **Teslimat sırası:** boşaltma sırası yüklerin *teslimat sırası* alanına göre belirlenir "
        "(1. durak ilk). Yükleme sırası bundan geriye doğru hesaplanır.\n"
        "- **LIFO:** *yükleme önceliği* büyük olan (son yüklenen) ilk çıkar. Arka kapılı araçların doğal düzenidir.\n"
        "- **FIFO:** ilk yüklenen ilk çıkar. Tek arka kapılı araçta ancak farklı öncelikteki yükler "
        "birbirinin önüne geçmezse (yan yana şeritler) mümkündür; aksi hâlde yük yerleştirilemez ve nedeni bildirilir.\n\n"
        "Her durumda gerçek erişim kontrol edilir: bir yükü çıkarmak için önce başka bir yükü indirmek gerekiyorsa "
        "bu **erişim ihlali** olarak raporlanır."
    )
    mixed = st.checkbox("Farklı boşaltma sırasındaki yükler üst üste konulabilir", r.allow_mixed_stop_stacking,
                        help="Kapalıyken (önerilen) her istif tek bir durağa ait yüklerden oluşur.")
    sup = st.slider("Üstteki yük için asgari taban desteği (%)", 50, 100, int(r.min_support_ratio * 100), 5)
    clear = st.number_input("Arka kapı önünde boş bırakılacak mesafe (cm)", min_value=0.0, value=float(r.rear_clearance_cm), step=10.0)
    st.caption("Kapı tanımları (arka/yan kapı) ve yasak bölgeler 'Araç Seçimi' sekmesinde araç profiline göre girilir.")
    new = Rules(mode=mode, allow_mixed_stop_stacking=mixed, min_support_ratio=sup / 100, rear_clearance_cm=clear)
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

def optimize_tab() -> None:
    ss = st.session_state
    vehicles: dict[str, Vehicle] = ss["vehicles"]
    st.subheader("Optimizasyon")
    obj = st.radio("Optimizasyon önceliği", list(OBJECTIVES), format_func=OBJECTIVES.get,
                   index=list(OBJECTIVES).index(ss["objective"]), horizontal=True)
    ss["objective"] = obj
    default_sel = [k for k in vehicles if k != "ozel"]
    sel = st.multiselect("Karşılaştırılacak araç profilleri", list(vehicles), default=default_sel,
                         format_func=lambda k: vehicles[k].name)
    c1, c2 = st.columns(2)
    max_n = c1.number_input("Bir araç tipinden en fazla kaç araç kullanılabilir", 1, 20, 5)
    depth = c2.selectbox("Arama derinliği", ["hizli", "normal", "kapsamli"], index=1,
                         format_func={"hizli": "Hızlı", "normal": "Normal", "kapsamli": "Kapsamlı (yavaş)"}.get)
    st.caption("Her araç tipi için gerçek 3D yerleşim yapılır; seçim yalnız hacme veya ağırlığa göre yapılmaz. "
               "Karma filo (farklı tiplerden araçlar birlikte) bu sürümde desteklenmez.")

    if st.button("🚚 Yerleşimi hesapla", type="primary", width="stretch"):
        errs, warns = validate_items(ss["items"])
        for v in sel:
            errs += validate_vehicle(vehicles[v])
        if not sel:
            errs.append("En az bir araç profili seçin.")
        if errs:
            for e in errs:
                st.error(e)
            return
        for w in warns:
            st.info(w)
        with st.spinner("3D yerleşim hesaplanıyor..."):
            res = compare_vehicles([copy.deepcopy(vehicles[v]) for v in sel], ss["items"], ss["rules"],
                                   objective=obj, max_vehicles=int(max_n), depth=depth)
        ss["results"] = res
        ss["result_sig"] = signature()
        ss["view_idx"] = 0

    res = ss["results"]
    if not res:
        st.info("Henüz hesaplama yapılmadı.")
        return
    if ss["result_sig"] != signature():
        st.warning("Girdiler son hesaplamadan sonra değişti. Sonuçlar güncel değil; yeniden hesaplayın.")
    best = res[0]
    if best.metrics["unplaced_count"] == 0 and best.metrics["error_count"] == 0:
        st.success(f"Önerilen araç: **{best.vehicle.name}** × {best.n_vehicles}")
    else:
        st.error(f"Hiçbir araç tipiyle bütün yükler kurallara uygun yerleştirilemedi. En iyi seçenek: "
                 f"**{best.vehicle.name}** × {best.n_vehicles}. Ayrıntılar 'Sonuç Raporu' sekmesinde.")
    st.write(best.explanation)
    rows = []
    for i, f in enumerate(res):
        m = f.metrics
        rows.append({
            "Sıra": i + 1, "Araç": f.vehicle.name, "Araç sayısı": m["n_vehicles"],
            "Yerleşen": f"{m['placed_count']}/{m['total_units']}", "Yerleşmeyen": m["unplaced_count"],
            "Hacim kullanımı": pct(m["volume_util"]), "Ağırlık kullanımı": pct(m["weight_util"]),
            "İhlal": m["error_count"], "Alt sınır (ağırlık/hacim)": f"{m['min_by_weight']} / {m['min_by_volume']}",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("'Alt sınır': yalnız ağırlık veya yalnız hacim hesabına göre gereken en az araç sayısı. "
               "3D yerleşim bu sayıdan fazla araç gerektirebilir. Hacim kullanımı tek başına başarı ölçütü değildir.")
    ss["view_idx"] = st.selectbox("İncelenecek seçenek (3D, rapor ve dışa aktarma için)", range(len(res)),
                                  index=min(ss.get("view_idx", 0), len(res) - 1),
                                  format_func=lambda i: f"{i + 1}. {res[i].vehicle.name} × {res[i].n_vehicles}")


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
    st.subheader(f"{fleet.vehicle.name} × {fleet.n_vehicles}")
    k = 0
    if fleet.n_vehicles > 1:
        k = st.radio("Araç", range(fleet.n_vehicles), format_func=lambda i: f"Araç {i + 1}", horizontal=True)
    plan = fleet.plans[k]
    n = len(plan.placements)
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
    if fleet is ss["results"][0]:
        st.markdown(f"**Seçilen araç ve neden:** {fleet.explanation}")
    else:
        st.markdown(f"**İncelenen seçenek:** {fleet.vehicle.name} (önerilen seçenek değil).")
    c = st.columns(3)
    c[0].metric("Toplam yük adedi", m["total_units"])
    c[1].metric("Toplam ağırlık", f"{m['total_weight_kg']:,.0f} kg".replace(",", "."))
    c[2].metric("Toplam hacim", f"{m['total_volume_m3']:.2f} m³".replace(".", ","))
    c = st.columns(3)
    c[0].metric("Araç sayısı", m["n_vehicles"])
    c[1].metric("Yerleşen / yerleşmeyen", f"{m['placed_count']} / {m['unplaced_count']}")
    c[2].metric("İhlal", m["error_count"])

    for conf in rule_conflicts(ss["items"], ss["rules"], fleet.vehicle):
        st.warning(conf.message)

    for i, plan in enumerate(fleet.plans):
        pm = plan.metrics
        with st.expander(f"Araç {i + 1}: {pm['placed_count']} yük, hacim {pct(pm['volume_util'])}, "
                         f"ağırlık {pct(pm['weight_util'])}, {len(plan.errors)} ihlal", expanded=i == 0):
            c = st.columns(3)
            c[0].metric("Hacim kullanımı", pct(pm["volume_util"]))
            c[1].metric("Ağırlık kullanımı", pct(pm["weight_util"]))
            c[2].metric("Kullanılan boy", f"{pm['used_length_cm']:.0f} cm")
            st.caption(f"Yerleşen yük ağırlığı {pm['placed_weight_kg']:.0f} / {plan.vehicle.max_payload_kg:.0f} kg; "
                       f"en yüksek nokta {pm['max_height_cm']:.0f} cm; üst üste konan yük sayısı {pm['stacked_count']}; "
                       f"strateji: {plan.strategy}.")
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
    st.download_button("Yerleşim planı ve yük koordinatları (CSV)", fleet_placements_csv(fleet).encode("utf-8-sig"),
                       "yerlesim_plani.csv", "text/csv", type="primary")
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
    enc = encode_state(ss["items"], ss["rules"], ss["vehicles"])
    if enc == ss.get("saved_state"):
        return
    ss["saved_state"] = enc
    empty = encode_state([], Rules(), default_vehicles())
    if enc == empty:
        st.query_params.pop("d", None)
    else:
        st.query_params["d"] = enc


main()
