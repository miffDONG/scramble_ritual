"""
The tuner is only usable if every option produces a PICTURE.

The regression these tests exist for: the derived maps used to be served as
bare binary masks, so "선 후보" was a fully black frame in contour mode (which
builds no line map) and "점 후보" was a fully black frame in morphology mode
(whose default kernel claims nearly every pixel). Switching methods therefore
gave no visual feedback at all.
"""

import cv2
import numpy as np
import pytest

from tracker.morse import (
    Glyph, cfg_with_defaults, extraction_maps, find_glyphs, prepare_gray,
    preprocess, classify_slot, render_scene,
)
from webui.diagnostics import (
    DIAG_DOT_BGR, DIAG_LINE_BGR, VIEWS, caption, compare_wipe, dim_base,
    draw_extraction, extraction_summary, glyph_is_line, mask_over_frame,
    preprocess_chain,
)
from webui.schema import RUNTIME_DEFAULTS, SCHEMA

MODES = ["contour", "hough", "morphology"]


def scene():
    """A synthetic card scene, the same renderer the sim source uses."""
    return render_scene([("10110100", 300, 160, 0.0),
                         ("01001101", 300, 330, 12.0)])


def stages(mode, **over):
    cfg = cfg_with_defaults({"extraction_mode": mode, **over})
    frame = scene()
    gray = prepare_gray(frame, cfg)
    mask = preprocess(frame, cfg, gray=gray)
    _, line_mask, dot_mask = extraction_maps(mask, cfg)
    glyphs = find_glyphs(mask, cfg, gray=gray, line_mask=line_mask)
    return frame, gray, mask, line_mask, dot_mask, glyphs, cfg


def spread(img):
    """How much visible structure an image carries. A blank/uniform frame
    scores ~0, which is exactly the failure being guarded against."""
    return float(np.std(img.astype(np.float32)))


@pytest.mark.parametrize("mode", MODES)
def test_every_diagnostic_view_shows_the_frame_not_a_blank(mode):
    frame, gray, mask, line_mask, dot_mask, glyphs, cfg = stages(mode)
    views = {
        "extract": draw_extraction(gray, mask, line_mask, dot_mask, glyphs, cfg),
        "lines": mask_over_frame(gray, line_mask, DIAG_LINE_BGR),
        "dots": mask_over_frame(gray, dot_mask, DIAG_DOT_BGR),
    }
    for name, view in views.items():
        assert view.ndim == 3 and view.shape[2] == 3, name
        # the frame is always underneath, so no view can be a black rectangle
        assert spread(view) > 5.0, f"{name} in {mode} mode is effectively blank"


@pytest.mark.parametrize("mode", MODES)
def test_extraction_view_differs_per_method(mode):
    """Choosing a different method must change what you SEE."""
    args = stages(mode)
    view = draw_extraction(args[1], args[2], args[3], args[4], args[5], args[6])
    base = dim_base(args[1])
    assert view.shape[0] > base.shape[0]        # caption strip is appended
    tinted = view[:base.shape[0]]
    assert not np.array_equal(tinted, base)     # something was drawn on it


def test_extraction_views_are_not_all_identical():
    rendered = []
    for mode in MODES:
        f, gray, mask, lm, dm, glyphs, cfg = stages(mode)
        rendered.append(draw_extraction(gray, mask, lm, dm, glyphs, cfg))
    for a in range(len(rendered)):
        for b in range(a + 1, len(rendered)):
            assert not np.array_equal(rendered[a], rendered[b]), \
                f"{MODES[a]} and {MODES[b]} render identically"


def test_compare_wipe_endpoints_are_a_full_ab():
    original = scene()
    enhanced = cv2.cvtColor(
        cv2.cvtColor(original, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    assert np.array_equal(compare_wipe(original, enhanced, 1.0), original)
    assert np.array_equal(compare_wipe(original, enhanced, 0.0), enhanced)
    # away from the 2px divider, each side is its own source untouched
    mid = compare_wipe(original, enhanced, 0.5)
    x = original.shape[1] // 2
    assert np.array_equal(mid[:, :x - 3], original[:, :x - 3])
    assert np.array_equal(mid[:, x + 3:], enhanced[:, x + 3:])
    assert not np.array_equal(mid[:, x, :], original[:, x, :])   # divider drawn


def test_caption_adds_a_strip_below_and_never_covers_the_image():
    img = scene()
    out = caption(img, [("ONE", (255, 255, 255)), ("TWO", (0, 200, 255))])
    assert out.shape[1] == img.shape[1]
    assert out.shape[0] > img.shape[0]
    assert np.array_equal(out[:img.shape[0]], img)   # pixels untouched


@pytest.mark.parametrize("mode", ["hough", "morphology"])
def test_glyph_verdict_matches_the_decoder(mode):
    """The dash/dot colour in the view must be the bit the decoder would emit,
    otherwise the picture teaches the wrong lesson."""
    _, _, _, _, _, glyphs, cfg = stages(mode)
    assert glyphs
    u, v = (1.0, 0.0), (0.0, 1.0)
    for g in glyphs:
        assert glyph_is_line(g, cfg) == (classify_slot(g, u, v, cfg) == "1")


def test_shape_classifier_wins_over_the_line_map_in_both():
    """classify_slot checks glyph.shape first; the diagnostic must too."""
    cfg = cfg_with_defaults({"extraction_mode": "hough"})
    g = Glyph(kind="dot", cx=0, cy=0, area=10.0, bbox=(0, 0, 4, 4),
              contour=np.zeros((1, 1, 2), np.int32))
    g.shape, g.line_score = "circle", 1.0      # line map says line, shape says dot
    assert glyph_is_line(g, cfg) is False
    assert classify_slot(g, (1.0, 0.0), (0.0, 1.0), cfg) == "0"
    g.shape, g.line_score = "line", 0.0
    assert glyph_is_line(g, cfg) is True


def test_extraction_summary_reports_only_the_active_methods_knobs():
    _, hough = extraction_summary({"extraction_mode": "hough",
                                   "hough_threshold": 7})
    assert "vote=7" in hough and "kernel" not in hough
    _, morph = extraction_summary({"extraction_mode": "morphology",
                                   "morph_line_length": 15})
    assert "kernel=15" in morph and "vote" not in morph


def test_preprocess_chain_follows_the_master_switch():
    cfg = {"preprocess_enabled": False, "clahe_enabled": True}
    assert preprocess_chain(cfg) == []
    cfg["preprocess_enabled"] = True
    assert preprocess_chain(cfg) == ["CLAHE"]
    cfg["gamma_enabled"] = True
    assert preprocess_chain(cfg) == ["Gamma", "CLAHE"]   # prepare_gray's order


# ---- schema gating -------------------------------------------------------

def test_show_when_only_references_real_option_keys():
    known = {o["key"] for o in SCHEMA} | set(RUNTIME_DEFAULTS)
    for opt in SCHEMA:
        for key in (opt.get("show_when") or {}):
            assert key in known, f"{opt['key']} gated on unknown key {key}"


def test_each_extraction_method_gates_its_own_parameters():
    gated = {o["key"]: o.get("show_when") for o in SCHEMA}
    for key in ("hough_threshold", "hough_min_line_length",
                "hough_max_line_gap", "hough_line_thickness"):
        assert gated[key] == {"extraction_mode": "hough"}, key
    for key in ("morph_line_length", "morph_line_thickness"):
        assert gated[key] == {"extraction_mode": "morphology"}, key
    # the overlap threshold is shared by the two line-map methods only
    assert gated["method_line_score_min"] == {
        "extraction_mode": ["hough", "morphology"]}
    # the selector itself is never hidden
    assert gated["extraction_mode"] is None


def test_preprocess_parameters_are_gated_on_their_own_filter():
    gated = {o["key"]: o.get("show_when") for o in SCHEMA}
    assert gated["preprocess_enabled"] is None
    assert gated["clahe_enabled"] == {"preprocess_enabled": True}
    assert gated["clahe_clip_limit"] == {"preprocess_enabled": True,
                                         "clahe_enabled": True}


def test_views_tuple_matches_what_the_pipeline_produces():
    assert set(VIEWS) == {"raw", "compare", "enhanced", "mask", "lines",
                          "dots", "extract", "overlay"}
