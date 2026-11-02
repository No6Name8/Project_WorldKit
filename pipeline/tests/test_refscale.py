"""Reference-object scale: parsing, reliability order, card observation, part-of-object dedup, mask cache."""
from __future__ import annotations

import numpy as np
import pytest

from worldkit import mapmode as mm
from worldkit import refscale as rs


def test_parse_ref_object_coin_card_and_errors():
    r = rs.parse_ref("striped mug=70mm")
    assert (r.kind, r.name, r.size_cm) == ("object", "striped mug", pytest.approx(7.0))
    assert rs.parse_ref("mug = 7 cm").size_cm == pytest.approx(7.0) and rs.parse_ref("tin=0.12m").size_cm == pytest.approx(12.0)
    assert rs.parse_ref("saudi-25-halalas").kind == "coin" and rs.parse_ref("credit-card").size_cm == pytest.approx(8.56)
    for bad in ("mug", "=70mm", "mug=abc", "mug=1000m"):
        with pytest.raises(rs.RefError):
            rs.parse_ref(bad)


def test_most_reliable_reference_comes_first_and_describes_itself():
    specs = rs.specs_from_args(None, None, True, ["striped mug=70mm", "bottle=20cm"], "coin") + []
    assert [s.kind for s in specs] == ["card", "object", "object"]
    specs = rs.specs_from_args("saudi-25-halalas", None, True, ["striped mug=70mm"])
    assert [s.kind for s in specs] == ["coin", "card", "object"]
    assert rs.parse_ref("striped mug=70mm").describe() == "scale source: striped mug, 70 mm height, reconstructed mesh height set to the supplied value"
    assert "coin, 21.5 mm diameter" in specs[0].describe()


def test_name_matching_is_forgiving_but_not_loose():
    assert rs.name_matches("striped mug", "white striped mug") and rs.name_matches("Mug", "striped mug")
    assert not rs.name_matches("striped mug", "water bottle")


def test_card_observation_recovers_scale_and_elevation():
    m = np.zeros((200, 300), bool)
    ppc, el = 20.0, 30.0  # 20 px/cm, camera 30 deg above the surface
    m[100 - int(5.4 * ppc * np.sin(np.radians(el)) / 2):100 + int(5.4 * ppc * np.sin(np.radians(el)) / 2), 150 - int(8.56 * ppc / 2):150 + int(8.56 * ppc / 2)] = True
    got_ppc, got_el = rs.card_observation(m, 8.56, 5.4)
    assert got_ppc == pytest.approx(20.0, rel=0.03) and got_el == pytest.approx(30.0, abs=3.0)


def _m(x0, y0, x1, y1):
    a = np.zeros((100, 120), bool)
    a[y0:y1, x0:x1] = True
    return a


def test_a_cap_inside_a_bottle_mask_is_a_part_not_an_object():
    bottle = mm.Track("water bottle", {"f0": _m(20, 10, 50, 90), "f1": _m(22, 10, 52, 90)})
    cap = mm.Track("black bottle cap", {"f0": _m(28, 10, 42, 22), "f1": _m(30, 10, 44, 22)})
    other = mm.Track("candle", {"f0": _m(80, 20, 100, 80), "f1": _m(82, 20, 102, 80)})
    kept, dropped = mm.dedup_tracks([bottle, cap, other])
    assert sorted(t.name for t in kept) == ["candle", "water bottle"] and "part of 'water bottle'" in dropped[0]["reason"]


def test_mask_cache_reuses_the_result_and_fresh_recomputes(tmp_path):
    f = tmp_path / "frame.png"
    f.write_bytes(b"pixels")
    calls = []

    class R:
        used_fallback = False

    def fake(path, concept):
        calls.append(concept)
        return R()

    import worldkit.segment as seg

    old = seg.full_frame_mask
    seg.full_frame_mask = lambda path, r: np.ones((4, 4), bool)
    try:
        a = mm.cached_mask(tmp_path / "c", f, "mug", fake)
        b = mm.cached_mask(tmp_path / "c", f, "mug", fake)
        assert a is not None and b is not None and calls == ["mug"]
        mm.cached_mask(tmp_path / "c", f, "mug", fake, fresh=True)
        assert calls == ["mug", "mug"]
    finally:
        seg.full_frame_mask = old


def _card_quad(az_deg, el_deg, k=10.0, L=8.56, S=5.4):
    a, e = np.radians(az_deg), np.radians(el_deg)
    P = lambda x, z: np.array([k * (x * np.cos(a) - z * np.sin(a)), k * np.sin(e) * (x * np.sin(a) + z * np.cos(a))]) + 200  # noqa: E731
    return np.array([P(-L / 2, -S / 2), P(L / 2, -S / 2), P(L / 2, S / 2), P(-L / 2, S / 2)])


@pytest.mark.parametrize("az", [0, 20, 60, 90, 130, 170])
def test_card_corner_fit_any_rotation(az):
    ppc, el, _ = rs.card_from_quad(_card_quad(az, 35.0), 8.56, 5.4, prefer_el=33.0)
    assert ppc == pytest.approx(10.0, rel=1e-6) and el == pytest.approx(35.0, abs=1e-4)


def test_card_axis_aligned_is_ambiguous_and_resolved_by_other_frames():
    # a wide card seen from 35 deg has a second reading (long side pointing away, seen from low down): the other frames' elevation decides
    q = _card_quad(0.0, 35.0)
    ppc, el, amb = rs.card_from_quad(q, 8.56, 5.4, prefer_el=33.0)
    assert amb and el == pytest.approx(35.0, abs=1e-4) and ppc == pytest.approx(10.0, rel=1e-6)
    assert rs.card_from_quad(q, 8.56, 5.4, prefer_el=12.0)[1] < 20.0


def test_card_pointing_away_has_one_physical_reading():
    # the other reading of a card pointing straight away would need the camera beyond vertical, so it is rejected
    ppc, el, amb = rs.card_from_quad(_card_quad(90.0, 28.0), 8.56, 5.4)
    assert not amb and el == pytest.approx(28.0, abs=1e-4) and ppc == pytest.approx(10.0, rel=1e-6)
