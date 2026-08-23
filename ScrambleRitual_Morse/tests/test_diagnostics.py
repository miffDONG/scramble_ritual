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
    Glyph, cfg_with_defaults, extraction_detail, extraction_maps, find_glyphs,
    prepare_gray, preprocess, classify_slot, render_scene,
)
from webui.diagnostics import (
    DIAG_DOT_BGR, DIAG_LINE_BGR, VIEWS, caption, compare_wipe, dim_base,
    draw_extraction, extraction_summary, glyph_is_line, mask_over_frame,
    preprocess_chain, verdict_masks,
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
    detail = extraction_detail(mask, cfg)
    glyphs = find_glyphs(mask, cfg, gray=gray, line_mask=detail["line_mask"])
    return frame, gray, mask, detail, glyphs, cfg


def spread(img):
    """How much visible structure an image carries. A blank/uniform frame
    scores ~0, which is exactly the failure being guarded against."""
    return float(np.std(img.astype(np.float32)))


def line_dot_views(mode, gray, mask, detail, glyphs, cfg):
    """The 선 후보 / 점 후보 masks the server serves for this method: the
    pixel maps, or — for contour, which builds none — the per-blob verdicts."""
    if mode == "contour":
        return verdict_masks(mask, glyphs, cfg)
    return detail["line_mask"], detail["dot_mask"]


@pytest.mark.parametrize("mode", MODES)
def test_every_diagnostic_view_shows_the_frame_not_a_blank(mode):
    frame, gray, mask, detail, glyphs, cfg = stages(mode)
    line_mask, dot_mask = line_dot_views(mode, gray, mask, detail, glyphs, cfg)
    views = {
        "extract": draw_extraction(gray, detail, glyphs, cfg),
        "lines": mask_over_frame(gray, line_mask, DIAG_LINE_BGR),
        "dots": mask_over_frame(gray, dot_mask, DIAG_DOT_BGR),
    }
    for name, view in views.items():
        assert view.ndim == 3 and view.shape[2] == 3, name
        # the frame is always underneath, so no view can be a black rectangle
        assert spread(view) > 5.0, f"{name} in {mode} mode is effectively blank"


def test_contour_line_and_dot_views_carry_its_verdicts():
    """contour builds no pixel line map, so its 선 후보 tab was empty by
    construction. Its per-blob verdicts fill both views instead — and they
    must agree with the decoder, blob for blob."""
    frame, gray, mask, detail, glyphs, cfg = stages("contour")
    line_mask, dot_mask = verdict_masks(mask, glyphs, cfg)
    assert cv2.countNonZero(line_mask) > 0
    assert cv2.countNonZero(dot_mask) > 0
    for g in glyphs:
        painted = line_mask if glyph_is_line(g, cfg) else dot_mask
        assert painted[int(g.cy), int(g.cx)] == 255


@pytest.mark.parametrize("mode", ["hough", "morphology"])
def test_line_map_methods_fill_the_line_view(mode):
    """A method that DOES build a pixel map must put pixels in it. Its dot
    residual is allowed to run empty — a kernel/vote that claims every pixel
    is exactly the mis-tuning the view is there to expose (the caption says
    so, and the per-blob bars show every score pinned above the threshold)."""
    frame, gray, mask, detail, glyphs, cfg = stages(mode)
    assert cv2.countNonZero(detail["line_mask"]) > 0


@pytest.mark.parametrize("mode", MODES)
def test_extraction_view_differs_per_method(mode):
    """Choosing a different method must change what you SEE."""
    frame, gray, mask, detail, glyphs, cfg = stages(mode)
    view = draw_extraction(gray, detail, glyphs, cfg)
    base = dim_base(gray)
    assert view.shape[0] > base.shape[0]        # caption strip is appended
    tinted = view[:base.shape[0]]
    assert not np.array_equal(tinted, base)     # something was drawn on it


def test_extraction_views_are_not_all_identical():
    rendered = []
    for mode in MODES:
        f, gray, mask, detail, glyphs, cfg = stages(mode)
        rendered.append(draw_extraction(gray, detail, glyphs, cfg))
    for a in range(len(rendered)):
        for b in range(a + 1, len(rendered)):
            assert not np.array_equal(rendered[a], rendered[b]), \
                f"{MODES[a]} and {MODES[b]} render identically"


def test_each_method_draws_its_own_mechanism_not_just_the_union():
    """The regression this whole module exists for, one level deeper: two
    methods that produce a similar union must still LOOK different, because
    each draws its own intermediates (segments / orientations / boxes)."""
    f, gray, mask, hough, glyphs, cfg = stages("hough")
    assert hough["segments"], "hough found no segments to draw"
    assert hough["edges"] is not None

    f, gray, mask, morph, glyphs2, cfg2 = stages("morphology")
    assert set(morph["orientations"]) == {0, 45, 90, 135}
    assert set(morph["kernels"]) == {0, 45, 90, 135}
    # the four orientations must not be one identical mask repeated, or the
    # per-direction colouring would be a lie
    masks = [morph["orientations"][a].tobytes() for a in (0, 45, 90, 135)]
    assert len(set(masks)) > 1

    f, gray, mask, contour, glyphs3, cfg3 = stages("contour")
    assert contour["segments"] == [] and contour["orientations"] == {}
    assert cv2.countNonZero(contour["line_mask"]) == 0   # builds no line map


def test_extraction_maps_still_matches_extraction_detail():
    """The detector's hot path and the diagnostics must not drift apart."""
    for mode in MODES:
        f, gray, mask, detail, glyphs, cfg = stages(mode)
        cand, line_mask, dot_mask = extraction_maps(mask, cfg)
        assert np.array_equal(cand, detail["mask"])
        assert np.array_equal(line_mask, detail["line_mask"])
        assert np.array_equal(dot_mask, detail["dot_mask"])


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
    _, _, _, _, glyphs, cfg = stages(mode)
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
