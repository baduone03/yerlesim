"""Yazdırılabilir yükleme talimatı (PDF).

Şoför / forklift operatörü için: araç bilgisi, kontrol durumu, üstten ve yandan
yerleşim çizimi (adım numaralı), adım adım yükleme listesi, durak bazında
boşaltma listesi, özel taşıma uyarıları. Çizimler hesaplanan gerçek
koordinatlardan üretilir.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from .models import CARGO_TYPES, FleetPlan, Rules, VehiclePlan, access_text
from .container import lcl_breakdown
from .optimizer import fmt_num
from .visualization import OTHER, color_map

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
LIMITS = ("Bu talimat karar desteği içindir. Yük sabitleme (spanzet, kilit), fren/viraj kuvvetleri hesaplanmamıştır; "
          "aks yükü yalnız girilen verilerle yaklaşık değerlendirilmiştir. Araç ölçüleri DEMO ise gerçek değerlerle "
          "doğrulayın. Yükleme güvenliğinin sorumluluğu operasyondadır.")


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class _Doc(FPDF):
    def __init__(self, title: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
        self.set_margins(12, 12, 12)
        self.set_auto_page_break(True, margin=15)
        self.doc_title = title
        self.set_title(title)

    def footer(self) -> None:
        self.set_y(-11)
        self.set_font("DejaVu", "", 7)
        self.set_text_color(110, 110, 110)
        self.cell(0, 5, f"{self.doc_title}  |  Sayfa {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)

    def h1(self, text: str) -> None:
        self.set_font("DejaVu", "B", 14)
        self.cell(0, 8, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def h2(self, text: str) -> None:
        self.ln(1)
        self.set_font("DejaVu", "B", 10.5)
        self.cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def para(self, txt: str, size: float = 8.5, bold: bool = False) -> None:
        self.set_font("DejaVu", "B" if bold else "", size)
        self.multi_cell(0, size * 0.48, txt, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def status_box(self, ok: bool, txt: str) -> None:
        self.set_fill_color(*((226, 244, 233) if ok else (252, 228, 228)))
        self.set_draw_color(*((27, 175, 122) if ok else (227, 73, 72)))
        self.set_font("DejaVu", "B", 9)
        self.multi_cell(0, 4.6, txt, border=1, fill=True, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(0, 0, 0)
        self.ln(1)


def _drawing(pdf: _Doc, plan: VehiclePlan, rules: Rules, view: str, colors: dict[str, str]) -> None:
    """view: 'ust' (X-Y) veya 'yan' (X-Z, sol taraftan bakış). Sağ taraf arka kapıdır."""
    v = plan.vehicle
    avail_w = pdf.w - pdf.l_margin - pdf.r_margin - 14
    depth = v.width if view == "ust" else v.height
    scale = min(avail_w / v.length, 55 / depth)
    w, h = v.length * scale, depth * scale
    if pdf.get_y() + h + 12 > pdf.h - pdf.b_margin:
        pdf.add_page()
    x0 = pdf.l_margin + 7
    top = pdf.get_y() + 4
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_xy(pdf.l_margin, top - 4)
    pdf.cell(0, 4, "Üstten görünüm" if view == "ust" else "Yandan görünüm (sol taraftan)")

    def to_page(a, b, da, db):
        # Üstten: Y=0 (sol duvar) çizimin altında. Yandan: Z=0 (taban) çizimin altında.
        return x0 + a * scale, top + h - (b + db) * scale, da * scale, db * scale

    from .constraints import zone_arrays

    zarr, _, _ = zone_arrays(v, rules)
    pdf.set_fill_color(220, 220, 216)
    for z in zarr:
        if view == "ust":
            rx, ry, rw, rh = to_page(z[0], z[1], z[3] - z[0], z[4] - z[1])
        else:
            rx, ry, rw, rh = to_page(z[0], z[2], z[3] - z[0], z[5] - z[2])
        pdf.rect(rx, ry, rw, rh, style="F")

    if view == "ust":
        pls = sorted(plan.placements, key=lambda p: p.z1)
    else:
        pls = sorted(plan.placements, key=lambda p: -p.y)
    pdf.set_draw_color(255, 255, 255)
    pdf.set_line_width(0.3)
    for p in pls:
        if view == "ust":
            rx, ry, rw, rh = to_page(p.x, p.y, p.dx, p.dy)
        else:
            rx, ry, rw, rh = to_page(p.x, p.z, p.dx, p.dz)
        pdf.set_fill_color(*_rgb(colors.get(p.item.code, OTHER)))
        pdf.rect(rx, ry, rw, rh, style="DF")
        if rw >= 4 and rh >= 3:
            pdf.set_font("DejaVu", "B", 5.5 if rw < 7 else 6.5)
            pdf.set_text_color(255, 255, 255)
            pdf.set_xy(rx, ry + rh / 2 - 1.5)
            pdf.cell(rw, 3, str(p.load_step), align="C")
            pdf.set_text_color(0, 0, 0)
    pdf.set_draw_color(60, 60, 60)
    pdf.set_line_width(0.5)
    pdf.rect(x0, top, w, h)
    pdf.set_line_width(0.2)
    pdf.set_font("DejaVu", "", 6.5)
    pdf.set_xy(pdf.l_margin, top + h / 2 - 2)
    pdf.cell(7, 4, "ÖN", align="C")
    pdf.set_xy(x0 + w, top + h / 2 - 2)
    pdf.cell(7, 4, "KAPI" if any(d.kind == "arka" for d in v.doors) else "ARKA", align="L")
    pdf.set_y(top + h + 3)
    pdf.set_draw_color(0, 0, 0)


def _short_orientation(label: str) -> str:
    """Tablo için kısa yönelim etiketi: 'E-B-Y (90° döndürülmüş)' -> 'E-B-Y 90°'."""
    return (label.replace(" (standart)", "").replace(" (90° döndürülmüş)", " 90°")
            .replace(" (yan yatırılmış)", " yan").replace(" (dikey çevrilmiş)", " dikey"))


def _vehicle_section(pdf: _Doc, plan: VehiclePlan, total: int, rules: Rules, manual_edits: int = 0) -> None:
    v = plan.vehicle
    m = plan.metrics
    colors = color_map(plan, "kod")
    pdf.add_page()
    body = f" - {v.body_label}" if v.body_label else ""
    pdf.h1(f"Yükleme Talimatı - Araç {plan.unit_no}/{total}{body}")
    demo = " (DEMO ölçüler)" if v.is_demo else ""
    name = plan.unit_name or v.name
    pdf.para(f"{name}{demo}  |  Erişim: {access_text(v)}  |  İç ölçü: {v.length:.0f} × {v.width:.0f} × "
             f"{v.height:.0f} cm  |  İstiap: {fmt_num(v.max_payload_kg)} kg")
    pdf.para(f"Yük: {m['placed_count']} adet, {fmt_num(m['placed_weight_kg'])} kg  |  Hacim kullanımı "
             f"%{100 * m['volume_util']:.0f}  |  Ağırlık kullanımı %{100 * m['weight_util']:.0f}  |  "
             f"Kullanılan boy {m['used_length_cm']:.0f} cm  |  {fmt_num(m.get('ldm', 0.0), 1)} LDM")
    if v.is_container and v.tare_kg is not None:
        gross = v.tare_kg + m["placed_weight_kg"]
        lim = f" / azami {fmt_num(v.max_gross_kg)} kg" if v.max_gross_kg else ""
        pdf.para(f"VGM ön hesap: dara {fmt_num(v.tare_kg)} + yük {fmt_num(m['placed_weight_kg'])} = "
                 f"{fmt_num(gross)} kg brüt{lim} (istif/sabitleme malzemesi hariç; resmi VGM tartım veya "
                 "SOLAS Yöntem 2 ile beyan edilir)", bold=True)
    if manual_edits:
        pdf.para(f"Not: Bu plan elle düzenlendi ({manual_edits} değişiklik) ve yeniden kontrol edildi.", bold=True)
    pdf.ln(1)
    if plan.errors:
        lines = [f"{len(plan.errors)} KURAL İHLALİ VAR - yüklemeden önce planı düzeltin:"]
        lines += [f"• {e.category}: {e.message}" for e in plan.errors[:8]]
        if len(plan.errors) > 8:
            lines.append(f"... ve {len(plan.errors) - 8} ihlal daha.")
        pdf.status_box(False, "\n".join(lines))
    else:
        pdf.status_box(True, "Kontroller: çakışma, kasa sınırı, destek, istif, ayrım, ağırlık ve kapı erişimi - ihlal yok.")

    pdf.h2("Yerleşim (kutulardaki sayı = yükleme adımı)")
    _drawing(pdf, plan, rules, "ust", colors)
    _drawing(pdf, plan, rules, "yan", colors)

    # Lejant
    seen = {}
    for p in plan.placements:
        seen.setdefault(p.item.code, [p.item, 0])[1] += 1
    pdf.set_font("DejaVu", "", 7.5)
    for code, (it, cnt) in sorted(seen.items(), key=lambda kv: (kv[1][0].delivery_stop, kv[0])):
        if pdf.get_y() > pdf.h - pdf.b_margin - 6:
            pdf.add_page()
        pdf.set_fill_color(*_rgb(colors.get(code, OTHER)))
        y = pdf.get_y()
        pdf.rect(pdf.l_margin, y + 0.8, 3, 3, style="F")
        pdf.set_x(pdf.l_margin + 4.5)
        pdf.cell(0, 4.5, f"{code} - {it.name} × {cnt}  ({CARGO_TYPES.get(it.cargo_type, '')}, {it.delivery_stop}. durak)",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Özel uyarılar
    warn = []
    for code, (it, _) in sorted(seen.items()):
        tags = []
        if it.fragile:
            tags.append("KIRILABİLİR")
        if not it.allow_on_top:
            tags.append("üstüne yük konmaz")
        if not it.stackable:
            tags.append("başka yükün üstüne konmaz")
        if it.segregation_group:
            tags.append(f"ayrım grubu: {it.segregation_group}")
        if it.notes:
            tags.append(it.notes)
        if tags:
            warn.append(f"• {code}: " + "; ".join(tags))
    if warn:
        pdf.h2("Özel taşıma uyarıları")
        pdf.para("\n".join(warn))
    if plan.warnings:
        pdf.h2("Uyarılar")
        pdf.para("\n".join(f"• {w.category}: {w.message}" for w in plan.warnings))
    if plan.infos:
        pdf.para("\n".join(f"Bilgi - {w.category}: {w.message}" for w in plan.infos), size=7)

    # Yükleme adımları
    pdf.h2("Adım adım yükleme sırası")
    pdf.para("X: kabinden kapıya, Y: sol duvardan, Z: tabandan (cm). Konum yükün kabin-sol-alt köşesidir.", size=7)
    pdf.set_font("DejaVu", "", 7)
    pdf.set_draw_color(200, 200, 200)
    pdf.set_fill_color(255, 255, 255)
    with pdf.table(col_widths=(11, 20, 41, 12, 11, 11, 11, 24, 32, 13), text_align=("RIGHT", "LEFT", "LEFT", "CENTER",
                   "RIGHT", "RIGHT", "RIGHT", "CENTER", "LEFT", "RIGHT"), line_height=4,
                   headings_style=FontFace(emphasis="BOLD", fill_color=(236, 236, 232)),
                   first_row_as_headings=True) as table:
        table.row(["Adım", "Yük", "Ad", "Durak", "X", "Y", "Z", "Ölçü X×Y×Z", "Yönelim", "kg"])
        for p in sorted(plan.placements, key=lambda q: q.load_step):
            name = p.item.name if len(p.item.name) <= 30 else p.item.name[:29] + "…"
            table.row([str(p.load_step), p.uid, name, str(p.item.delivery_stop), f"{p.x:.0f}", f"{p.y:.0f}", f"{p.z:.0f}",
                       f"{p.dx:.0f}×{p.dy:.0f}×{p.dz:.0f}", _short_orientation(p.orientation), f"{p.weight:.0f}"])
    pdf.set_draw_color(0, 0, 0)

    # Boşaltma
    pdf.h2("Boşaltma sırası (durak bazında)")
    by_stop: dict[int, list] = {}
    for p in sorted(plan.placements, key=lambda q: q.unload_step):
        by_stop.setdefault(p.item.delivery_stop, []).append(p)
    lines = []
    for stop in sorted(by_stop):
        ps = by_stop[stop]
        lines.append(f"{stop}. durak ({len(ps)} adet): " + ", ".join(p.uid for p in ps))
    pdf.para("\n".join(lines))
    if rules.mode != "teslimat":
        pdf.para(f"Kural: {rules.mode}. Liste boşaltma adımı sırasındadır.", size=7)


def build_loading_pdf(fleet: FleetPlan, rules: Rules, title: Optional[str] = None,
                      lcl: Optional[dict] = None) -> bytes:
    """``lcl`` verilirse (items, rate, min_wm, currency anahtarlarıyla) gönderici dökümü eklenir."""
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    doc_title = title or f"LoadPlanner TR - {fleet.label} - {stamp}"
    pdf = _Doc(doc_title)
    n = fleet.n_vehicles
    for plan in fleet.plans:
        if not plan.placements and plan.vehicle.body_label:
            continue  # kombinasyonun boş kalan kasası için sayfa basma
        _vehicle_section(pdf, plan, n, rules, fleet.metrics.get("manual_edits", 0))
    if not fleet.plans:
        pdf.add_page()
        pdf.h1("Yükleme Talimatı")
    if lcl is not None:
        rows = lcl_breakdown(lcl["items"], fleet, lcl.get("rate"), lcl.get("min_wm", 1.0))
        cur = lcl.get("currency", "")
        pdf.h2("LCL gönderici dökümü (W/M)")
        pdf.para("Ücrete esas miktar = max(hacim m³, ağırlık ton), en az "
                 f"{fmt_num(lcl.get('min_wm', 1.0), 1)} W/M.", size=7)
        pdf.set_font("DejaVu", "", 7)
        pdf.set_fill_color(255, 255, 255)
        with pdf.table(col_widths=(40, 14, 22, 18, 18, 26, 48), line_height=4,
                       headings_style=FontFace(emphasis="BOLD", fill_color=(236, 236, 232)),
                       first_row_as_headings=True) as table:
            table.row(["Gönderici", "Adet", "Ağırlık kg", "m³", "W/M", f"Ücret {cur}".strip(), "Konteyner"])
            for r in rows:
                table.row([r.shipper, str(r.units), fmt_num(r.weight_kg), fmt_num(r.cbm, 2), fmt_num(r.wm, 2),
                           fmt_num(r.charge, 2) if r.charge is not None else "-",
                           r.containers + (f" ({r.unplaced} yerleşmedi)" if r.unplaced else "")])
    if fleet.notes:
        pdf.h2("Filo notları")
        pdf.para("\n".join(f"• {x}" for x in fleet.notes))
    if fleet.unplaced:
        pdf.h2(f"Yerleştirilemeyen yükler ({len(fleet.unplaced)})")
        pdf.para("\n".join(f"• {u.uid} - {u.item.name}: {u.reason}" for u in fleet.unplaced[:60])
                 + (f"\n... ve {len(fleet.unplaced) - 60} yük daha." if len(fleet.unplaced) > 60 else ""))
    pdf.h2("Sınırlar")
    pdf.para(f"{LIMITS}\nOluşturulma: {stamp}", size=7)
    return bytes(pdf.output())
