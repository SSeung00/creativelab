import numpy as np
import pytest

from app.engine import color
from app.engine import params as PR
from app.engine.lrimport import lr_to_params, parse_lr_settings
from app.engine.pipeline import spline_lut


@pytest.mark.parametrize("t,ti", [(2850, 0), (5500, 13), (7500, -20), (12000, 40)])
def test_temp_tint_roundtrip(t, ti):
    x, y = color.temp_tint_to_xy(t, ti)
    t2, ti2 = color.xy_to_temp_tint(x, y)
    assert abs(t2 - t) < 2 and abs(ti2 - ti) < 0.1


def test_params_diff_roundtrip():
    p = PR.full({})
    assert PR.diff(p) == {}
    p["exposure"] = 0.7
    p["hsl"]["sat"][5] = -20
    d = PR.diff(p)
    assert d == {"exposure": 0.7, "hsl": {"sat": [0, 0, 0, 0, 0, -20, 0, 0]}}
    assert PR.full(d) == p


def test_lr_settings_parse():
    text = '''s = { Exposure2012 = -0.07,
Highlights2012 = -75,
CameraProfile = "Adobe Standard",
Look = { Name = "Adobe Color", Parameters = { ToneCurvePV2012 = { 0, 0, 255, 255 } } },
ToneCurvePV2012 = { 0, 0, 64, 70, 255, 255 },
WhiteBalance = "As Shot",
HasCrop = true, CropLeft = 0.1, CropRight = 0.9, CropTop = 0, CropBottom = 1, CropAngle = 2.5 }'''
    p = lr_to_params(parse_lr_settings(text))
    assert p["profile"] == "Adobe Color"
    assert p["exposure"] == -0.07 and p["highlights"] == -75
    assert p["curve"] == [[0, 0], [64, 70], [255, 255]]
    assert p["crop"]["left"] == 0.1 and p["crop"]["angle"] == 2.5
    assert p["wb"] == "as_shot"


def test_spline_identity_and_monotone():
    lut = spline_lut([[0, 0], [255, 255]])
    assert np.allclose(lut, np.linspace(0, 1, len(lut)), atol=1e-6)
    s = spline_lut([[0, 0], [64, 50], [192, 210], [255, 255]])
    assert s[0] == 0 and abs(s[-1] - 1) < 1e-6 and (np.diff(s) >= -1e-6).all()
