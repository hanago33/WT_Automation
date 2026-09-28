# encoding: utf-8
"""#1 多尺度 + ROI 图片匹配冒烟测试。

通过 monkeypatch 用合成屏幕图替代真实截图，故意把模板以 0.8× 缩放贴入画面，
使精确匹配必然失败、必须走多尺度兜底；并验证 region(ROI) 偏移能还原绝对坐标。
"""
import cv2
import numpy as np
import pyautogui
import pytest

import wt_projection_helpers as ph


def _make_template_png(tmp_path, size=(60, 40)):
    rng = np.random.default_rng(42)
    img = (rng.random((*size, 3)) * 255).astype(np.uint8)
    img[5:15, 5:20] = (0, 255, 0)  # 明显标记，便于匹配
    path = tmp_path / "tmpl.png"
    cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return str(path), img


def _screen_with_template(tmpl_rgb, scale=1.0, pos=(50, 60), screen=(400, 300)):
    sh, sw = screen
    screen_img = np.zeros((sh, sw, 3), dtype=np.uint8)
    th, tw = tmpl_rgb.shape[:2]
    w = int(round(tw * scale))
    h = int(round(th * scale))
    resized = cv2.resize(tmpl_rgb, (w, h), interpolation=cv2.INTER_AREA)
    screen_img[pos[1]:pos[1] + h, pos[0]:pos[0] + w] = resized
    return screen_img, (pos[0] + w // 2, pos[1] + h // 2)


def _pil_from(np_img):
    from PIL import Image
    return Image.fromarray(np_img)


@pytest.fixture
def patch_screenshot(monkeypatch):
    state = {}

    def _set(img):
        state["img"] = img

        def _shot(region=None):
            if region:
                x, y, w, h = region
                return _pil_from(state["img"][y:y + h, x:x + w])
            return _pil_from(state["img"])

        monkeypatch.setattr(pyautogui, "screenshot", _shot)

    return _set


def test_multiscale_finds_scaled_template(tmp_path, patch_screenshot):
    path, tmpl = _make_template_png(tmp_path)
    screen, exp = _screen_with_template(tmpl, scale=0.8, pos=(50, 60))
    patch_screenshot(screen)
    # 画面里是 0.8× 的模板，精确(1.0)匹配必失败，必须靠多尺度兜底命中
    res = ph._match_template_multiscale(path, confidence=0.5, region=None,
                                        scales=[1.0, 0.9, 0.8, 0.7])
    assert res is not None
    assert abs(res.x - exp[0]) <= 4 and abs(res.y - exp[1]) <= 4


def test_multiscale_roi_offset(tmp_path, patch_screenshot):
    path, tmpl = _make_template_png(tmp_path)
    screen, exp_abs = _screen_with_template(tmpl, scale=0.8, pos=(50, 60))
    patch_screenshot(screen)
    region = (40, 40, 200, 200)
    res = ph._match_template_multiscale(path, confidence=0.5, region=region,
                                        scales=[1.0, 0.8])
    assert res is not None
    # ROI 偏移必须把子图坐标还原为绝对屏幕坐标
    assert abs(res.x - exp_abs[0]) <= 4 and abs(res.y - exp_abs[1]) <= 4


def test_locate_template_center_fallback(monkeypatch, tmp_path, patch_screenshot):
    path, tmpl = _make_template_png(tmp_path)
    screen, exp = _screen_with_template(tmpl, scale=0.8, pos=(50, 60))
    patch_screenshot(screen)
    # 强制精确匹配路径返回 None，确保走多尺度兜底
    monkeypatch.setattr(pyautogui, "locateCenterOnScreen", lambda *a, **k: None)
    res = ph.locate_template_center_by_path(path, timeout_seconds=2, confidence=0.5)
    assert res is not None
    assert abs(res.x - exp[0]) <= 4 and abs(res.y - exp[1]) <= 4
