"""Regression tests for the third-party LEOI-30A public-manual case."""
import numpy as np
import pytest
from examples.public_data.leoi30a_single_slit.analyze import fit_sinc2,load_dataset,minima_widths

def test_public_manual_transcription_has_63_ordered_points():
    x,y=load_dataset()
    assert len(x)==len(y)==63
    assert x[0]==pytest.approx(11.)
    assert x[-1]==pytest.approx(41.)
    assert y[np.where(x==26.)[0][0]]==808
    assert 17.1 in x and 34.9 in x
    assert not np.any(np.isclose(x,17.2))
    assert np.all(np.diff(x)>0)

def test_minima_reproduce_manual_slit_width_calculation():
    widths=minima_widths()
    assert [v["inferred_width_um"] for v in widths]==pytest.approx([72.8029,73.6295,75.6251],rel=1e-5)
    assert np.mean([v["inferred_width_um"] for v in widths])==pytest.approx(74.0192,rel=1e-5)

def test_whole_curve_fit_is_reproducible_and_finite():
    x,y=load_dataset()
    fit=fit_sinc2(x,y)
    assert fit["inferred_slit_width_um"]==pytest.approx(69.64,abs=.15)
    assert fit["rmse_current_units"]==pytest.approx(6.344,abs=.02)
    assert fit["r_squared"]>.999
    assert np.all(np.isfinite(fit["residuals_current_units"]))
