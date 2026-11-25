"""Reproduce the LEOI-30A public-manual single-slit example."""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares
from photonguard.experiments.optics import evaluate

HERE = Path(__file__).resolve().parent
LAMBDA_NM, DISTANCE_MM = 650.0, 504.0
MANUAL_MINIMA = ((1, 4.5), (2, 8.9), (3, 13.0))

def load_dataset(path=HERE / "raw_measurement.csv"):
    positions, readings = [], []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["position_mm", "current_reading"]:
            raise ValueError("Unexpected CSV columns")
        for row in reader:
            positions.append(float(row["position_mm"]))
            readings.append(float(row["current_reading"]))
    x, y = np.asarray(positions), np.asarray(readings)
    if len(x) != 63 or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("Expected 63 finite source data rows")
    if np.any(np.diff(x) <= 0) or np.any(y < 0):
        raise ValueError("Positions must increase and readings must be nonnegative")
    if 17.1 not in x or 34.9 not in x:
        raise ValueError("Source's nonuniform 17.1 and 34.9 mm positions must be retained")
    return x, y

def sinc2(x, baseline, amplitude, center, first_zero):
    return baseline + amplitude * np.sinc((x-center)/first_zero)**2

def fit_sinc2(x, y):
    result = least_squares(lambda p: sinc2(x, *p)-y, (10.,790.,26.,4.7),
        bounds=((0.,0.,24.,3.),(100.,2000.,28.,7.)))
    if not result.success:
        raise RuntimeError(result.message)
    baseline, amplitude, center, first_zero = map(float, result.x)
    predicted = sinc2(x, *result.x)
    residual = predicted-y
    rmse = float(np.sqrt(np.mean(residual**2)))
    return {
        "baseline_current_units":baseline, "amplitude_current_units":amplitude,
        "center_position_mm":center, "first_zero_distance_mm":first_zero,
        "inferred_slit_width_um":LAMBDA_NM*DISTANCE_MM/first_zero*1e-3,
        "rmse_current_units":rmse,
        "nrmse_by_data_range":rmse/float(np.ptp(y)),
        "nrmse_by_peak":rmse/float(np.max(y)),
        "r_squared":float(1-np.sum(residual**2)/np.sum((y-y.mean())**2)),
        "residual_mean_current_units":float(np.mean(residual)),
        "residual_max_abs_current_units":float(np.max(np.abs(residual))),
        "residuals_current_units":residual.tolist(),
        "fitted_readings":predicted.tolist()
    }

def minima_widths():
    result=[]
    for order, offset in MANUAL_MINIMA:
        theta=np.arctan(offset/DISTANCE_MM)
        width_um=order*LAMBDA_NM*1e-6/np.sin(theta)*1000
        result.append({"order":order,"offset_mm":offset,"inferred_width_um":float(width_um)})
    return result

def run_analysis(out):
    out=Path(out)
    out.mkdir(parents=True,exist_ok=False)
    x,y=load_dataset()
    fit=fit_sinc2(x,y)
    minima=minima_widths()
    # Existing PhotonGuard FFT evaluator: Fourier-plane scaling with effective
    # focal length Z is Fraunhofer-equivalent to free-space propagation at Z.
    fft=evaluate("diffraction",{
        "aperture":"single_slit","wavelength_nm":LAMBDA_NM,
        "focal_length_mm":DISTANCE_MM,"slit_width_um":fit["inferred_slit_width_um"],
        "slit_height_um":500.,"grid_size":512,"window_mm":2.0})
    if not fft.get("all_checks_passed",False):
        raise RuntimeError("PhotonGuard diffraction evaluator failed its calibration checks")
    mx=np.asarray(fft["data"]["x_mm"],float)
    mi=np.asarray(fft["data"]["centerline_intensity"],float)
    mi=mi/float(mi.max())
    sampled=np.interp(x-fit["center_position_mm"],mx,mi)
    design=np.column_stack((np.ones_like(sampled),sampled))
    affine,*_=np.linalg.lstsq(design,y,rcond=None)
    fft_pred=design@affine
    fft_res=fft_pred-y
    pairs=[]
    lookup={round(float(a),1):float(b) for a,b in zip(x,y)}
    for a,b in zip(x,y):
        if a<=26:
            mirror=round(52-float(a),1)
            if mirror in lookup: pairs.append(abs(float(b)-lookup[mirror]))
    minima_cmp=[]
    for item in minima:
        k=item["order"]; observed=MANUAL_MINIMA[k-1][1]
        pred=k*fit["first_zero_distance_mm"]
        minima_cmp.append({"order":k,"manual_offset_mm":observed,
            "fit_predicted_offset_mm":pred,"predicted_minus_observed_mm":pred-observed,
            "minima_derived_width_um":item["inferred_width_um"]})
    metrics={
      "provenance":"Third-party public LEOI-30A manual transcription; not user-collected data.",
      "source":"https://lambdasys.com/uploads/info/LEOI-30A-Content.pdf",
      "source_pdf_pages":[3,4,5],"data_points":int(len(x)),
      "scan_position_range_mm":[float(x.min()),float(x.max())],
      "nonuniform_positions_retained_mm":[17.1,34.9],
      "nominal_slit_width_um":70.0,
      "minima_derived_widths":minima,
      "minima_mean_width_um":float(np.mean([v["inferred_width_um"] for v in minima])),
      "whole_curve_ideal_sinc_squared_fit":fit,
      "minima_position_comparison":minima_cmp,
      "symmetry_about_manual_center_26mm":{
        "paired_points":len(pairs),"mean_absolute_current_difference":float(np.mean(pairs)),
        "max_absolute_current_difference":float(np.max(pairs)),
        "mean_absolute_difference_percent_of_peak":float(np.mean(pairs)/y.max()*100)},
      "existing_photonguard_fft_model":{
        "engine":fft["engine"],"parameters":fft["parameters"],
        "all_checks_passed":fft["all_checks_passed"],"checks":fft["checks"],
        "affine_baseline_current_units":float(affine[0]),
        "affine_scale_current_units":float(affine[1]),
        "rmse_current_units":float(np.sqrt(np.mean(fft_res**2))),
        "nrmse_by_data_range":float(np.sqrt(np.mean(fft_res**2))/np.ptp(y))},
      "interpretation":[
        "The sinc-squared fit and PhotonGuard FFT evaluator are two numerical forms of the same scalar Fraunhofer aperture model, not independent physical validations.",
        "The fitted width is conditional on assumed wavelength 650 nm and distance 504 mm.",
        "A single intensity scan cannot separately identify wavelength and slit width; the identifiable scale is approximately wavelength times distance divided by slit width.",
        "The detector active aperture is not reported; finite-area spatial averaging may smooth the profile and shift apparent minima.",
        "Photocell current is not calibrated irradiance. Baseline and amplitude are nuisance parameters, not calibrated physical quantities.",
        "R-squared is dominated by the central peak; residuals and minima comparisons must also be inspected."
      ]}
    (out/"metrics.json").write_text(json.dumps(metrics,indent=2,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    dense=np.linspace(x.min(),x.max(),1200)
    fig,ax=plt.subplots(figsize=(10,6))
    ax.scatter(x,y,s=22,label="Manual photocell readings",zorder=3)
    ax.plot(dense,sinc2(dense,fit["baseline_current_units"],fit["amplitude_current_units"],fit["center_position_mm"],fit["first_zero_distance_mm"]),label="Whole-curve ideal sinc² fit",lw=2)
    ax.plot(x,fft_pred,"--",label="PhotonGuard FFT Fraunhofer-equivalent model",lw=1.7)
    ax.set(xlabel="Photocell position X (mm)",ylabel="Photocell current reading (instrument units)",title="LEOI-30A Group 4 single-slit public-manual data")
    ax.grid(True,alpha=.25); ax.legend(); fig.tight_layout()
    fig.savefig(out/"comparison.png",dpi=180); plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,4))
    ax.axhline(0,lw=1); ax.plot(x,fit["residuals_current_units"],marker="o",ms=3,lw=1)
    ax.set(xlabel="Photocell position X (mm)",ylabel="Fit residual (current units)",title="Ideal sinc² fit residuals")
    ax.grid(True,alpha=.25); fig.tight_layout()
    fig.savefig(out/"residuals.png",dpi=180); plt.close(fig)
    return metrics

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",required=True,type=Path)
    args=parser.parse_args()
    m=run_analysis(args.out)
    print(json.dumps({"data_points":m["data_points"],
      "fit":{k:m["whole_curve_ideal_sinc_squared_fit"][k] for k in
      ("inferred_slit_width_um","rmse_current_units","nrmse_by_data_range","r_squared")},
      "minima_mean_width_um":m["minima_mean_width_um"],
      "fft_rmse":m["existing_photonguard_fft_model"]["rmse_current_units"],
      "output":str(args.out)},indent=2))

if __name__=="__main__": main()
