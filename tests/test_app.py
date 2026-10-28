"""Streamlit arayüzünün başsız (headless) duman testi."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


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
