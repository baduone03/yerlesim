"""Streamlit arayüzünün başsız (headless) duman testi."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


def test_app_runs_example_end_to_end():
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception
    next(b for b in at.button if b.label == "Örnek yük listesini yükle").click().run()
    assert len(at.session_state["items"]) == 4
    next(b for b in at.button if "Yerleşimi hesapla" in b.label).click().run()
    assert not at.exception
    res = at.session_state["results"]
    assert res and res[0].metrics["unplaced_count"] == 0 and res[0].metrics["error_count"] == 0


def test_app_blocks_empty_list():
    at = AppTest.from_file(APP, default_timeout=60).run()
    next(b for b in at.button if "Yerleşimi hesapla" in b.label).click().run()
    assert any("boş" in e.value for e in at.error)
    assert at.session_state["results"] is None


def test_items_survive_page_reload_via_url():
    at = AppTest.from_file(APP, default_timeout=60).run()
    next(b for b in at.button if b.label == "Örnek yük listesini yükle").click().run()
    saved = at.query_params["d"]
    assert saved
    # Yenileme: yeni oturum, aynı adres
    fresh = AppTest.from_file(APP, default_timeout=60)
    fresh.query_params["d"] = saved[0] if isinstance(saved, list) else saved
    fresh.run()
    assert not fresh.exception
    assert [i.code for i in fresh.session_state["items"]] == ["PLT-A", "PLT-B", "KOL-1", "SND-1"]


def test_manual_edit_and_pdf_flow():
    at = AppTest.from_file(APP, default_timeout=180).run()
    next(b for b in at.button if b.label == "Örnek yük listesini yükle").click().run()
    next(b for b in at.button if "Yerleşimi hesapla" in b.label).click().run()
    fleet = at.session_state["results"][0]
    assert fleet.metrics["error_count"] == 0
    # Yer değiştir: iki farklı duraktaki yük
    at.radio(key="op_0").set_value("Yer değiştir").run()
    a = next(p.uid for p in fleet.plans[0].placements if p.item.code == "SND-1")
    b = next(p.uid for p in fleet.plans[0].placements if p.item.code == "PLT-B")
    at.selectbox(key="sw_a_0_0").set_value(a).run()
    at.selectbox(key="sw_b_0_0").set_value(b).run()
    next(x for x in at.button if x.label == "Yer değiştir").click().run()
    assert not at.exception
    fleet = at.session_state["results"][0]
    assert fleet.metrics["manual_edits"] == 1 and fleet.metrics["error_count"] > 0
    # Geri al
    next(x for x in at.button if "Otomatik plana geri dön" in x.label).click().run()
    assert at.session_state["results"][0].metrics["error_count"] == 0
    # PDF
    next(x for x in at.button if "PDF'ini hazırla" in x.label).click().run()
    assert not at.exception
    assert at.session_state["pdf_cache"][1][:5] == b"%PDF-"


def test_lcl_mode_flow_with_vgm_and_breakdown():
    from loadplanner.models import CargoItem

    at = AppTest.from_file(APP, default_timeout=180).run()
    at.session_state["items"] = [
        CargoItem("A1", "Tekstil", "koli", 30, 60, 40, 40, 18, max_top_load=80, shipper="Aslan Tekstil"),
        CargoItem("B1", "Makine parçası", "sandik", 3, 120, 100, 100, 600, max_top_load=600, shipper="Bora Makine"),
    ]
    at.run()
    mode_radio = next(r for r in at.radio if r.label == "Taşıma şekli")
    mode_radio.set_value("lcl").run()
    assert at.session_state["transport_mode"] == "lcl"
    assert at.session_state["rules"].keep_shipper_together
    rate = next(n for n in at.number_input if n.label == "W/M birim fiyatı")
    rate.set_value(45.0).run()
    next(b for b in at.button if "Yerleşimi hesapla" in b.label).click().run()
    assert not at.exception
    best = at.session_state["results"][0]
    assert all(p.vehicle.is_container for p in best.plans) and best.metrics["unplaced_count"] == 0
    md = " ".join(m.value for m in at.markdown)
    assert "LCL gönderici dökümü" in md and "VGM ön hesabı" in md
    next(x for x in at.button if "PDF'ini hazırla" in x.label).click().run()
    assert not at.exception and at.session_state["pdf_cache"][1][:5] == b"%PDF-"
