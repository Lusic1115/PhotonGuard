"""Registered geometry of a uniformly illuminated slit in an ideal focal plane."""

from ..core import finite

FIRST_MINIMUM = 'first_zero_mm'
FULL_WIDTH = 'central_zero_to_zero_width_mm'
FORMULA_ID = 'ideal_single_slit_focal_plane_v1'
WIDTH_LABELS = {FIRST_MINIMUM: '中心到单侧第一暗纹', FULL_WIDTH: '中央亮区全宽'}


def numerator(wavelength_nm, focal_length_mm, metric):
    if metric not in WIDTH_LABELS:
        raise ValueError('请选择两侧第一暗纹间全宽或中心到单侧第一暗纹的距离。')
    if any(not finite(value) or value <= 0 for value in (wavelength_nm, focal_length_mm)):
        raise ValueError('波长和焦距须为有限正数。')
    factor = 2 if metric == FULL_WIDTH else 1
    return factor * wavelength_nm * focal_length_mm / 1000


def width_mm(wavelength_nm, focal_length_mm, slit_width_um, metric):
    if not finite(slit_width_um) or slit_width_um <= 0:
        raise ValueError('缝宽须为有限正数。')
    return numerator(wavelength_nm, focal_length_mm, metric) / slit_width_um
