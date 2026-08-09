import cv2
import numpy as np

from tracker.morse import extraction_maps, prepare_gray


def fixture_mask():
    mask = np.zeros((120, 180), np.uint8)
    cv2.circle(mask, (35, 60), 5, 255, -1)
    cv2.line(mask, (80, 60), (125, 60), 255, 5)
    return mask


def test_contour_mode_keeps_all_candidates_as_dot_residual():
    mask = fixture_mask()
    candidate, lines, dots = extraction_maps(mask, {"extraction_mode": "contour"})
    assert np.array_equal(candidate, mask)
    assert cv2.countNonZero(lines) == 0
    assert np.array_equal(dots, mask)


def test_hough_mode_separates_long_line_from_compact_dot():
    mask = fixture_mask()
    _, lines, dots = extraction_maps(mask, {
        "extraction_mode": "hough", "hough_threshold": 5,
        "hough_min_line_length": 10, "hough_max_line_gap": 2,
        "hough_line_thickness": 5,
    })
    assert cv2.countNonZero(lines[:, 75:135]) > 0
    assert cv2.countNonZero(dots[:, 25:45]) > 0


def test_morphology_mode_separates_long_line_from_compact_dot():
    mask = fixture_mask()
    _, lines, dots = extraction_maps(mask, {
        "extraction_mode": "morphology", "morph_line_length": 13,
        "morph_line_thickness": 1,
    })
    assert cv2.countNonZero(lines[:, 75:135]) > 0
    assert cv2.countNonZero(dots[:, 25:45]) > 0


def test_preprocessing_master_switch_is_true_bypass():
    x = np.tile(np.arange(64, 192, dtype=np.uint8), (80, 1))
    cfg = {"preprocess_enabled": False, "clahe_enabled": True,
           "autocontrast_enabled": True, "blur_px": 1}
    assert np.array_equal(prepare_gray(x, cfg), x)
    cfg["preprocess_enabled"] = True
    assert not np.array_equal(prepare_gray(x, cfg), x)
