# LEOI-30A Group 4: public single-slit measurement case

## Provenance

**This is a transcription of a third-party public experimental manual. It is not data collected by PhotonGuard contributors or by the user.**

Source: [LEOI-30A Manual](https://lambdasys.com/uploads/info/LEOI-30A-Content.pdf), Experiment 1, "Examples of Data Recording and Processing", PDF file pages 3–5 (one-based: data table on page 3, processing on pages 4–5; these are file-page indices, not printed manual page labels).

The manual gives Group 4 nominal slit width 0.07 mm and distance Z = 511 - 7 = 504 mm. Its example slit-width calculation uses wavelength 650 nm; this is treated as the manual's example/specification value, not as an independently recorded per-run wavelength measurement. The manual lists central maximum X0 = 26.0 mm, current 808; first, second and third minima offsets 4.5, 8.9 and 13.0 mm; and minima-derived widths 0.0728, 0.0736 and 0.0756 mm (mean about 0.074 mm).

All 63 position/current pairs are transcribed in raw_measurement.csv. The irregular positions 17.1 mm and 34.9 mm are preserved. No missing values are interpolated and no positions are forced onto a uniform grid.

## Reproduce

From app/source with the official PhotonGuard runtime active:

    python examples/public_data/leoi30a_single_slit/analyze.py --out /tmp/photonguard-leoi30a

Outputs: metrics.json, comparison.png and residuals.png.

## Model

The analysis reuses the existing PhotonGuard function photonguard.experiments.optics.evaluate("diffraction", ...). The measured free-space Fraunhofer plane is represented by the mathematically equivalent Fourier scaling with effective focal length equal to propagation distance Z. This is a scalar paraxial equivalence, not a claim that the apparatus contains a lens.

The whole-curve fit is I(X)=b+A*sinc^2((X-X0)/x1), using NumPy's normalized sinc. It estimates baseline b, amplitude A, center X0 and first-zero scale x1. Given assumed wavelength and distance, effective slit width is a=lambda*Z/x1. The three manually tabulated minima are also processed independently using a sin(theta)=k lambda, theta=arctan(deltaX/Z); these results are not forced to equal the full-curve fit.

## Limitations

- One scan identifies approximately lambda*Z/a, not wavelength and slit width independently. Fitted width is conditional on 650 nm and 504 mm.
- Photocell current is an instrument reading, not calibrated irradiance. Baseline and amplitude are nuisance parameters.
- The manual says nominal scan increments are 0.5 mm, but its printed table includes 17.1 and 34.9 mm; the transcription preserves these.
- The active photocell aperture is not specified. Finite detector-area averaging may smooth the profile and affect apparent minima and side lobes.
- Alignment, source spectrum, stray light, galvanometer resolution and repeatability are not quantified in this one example.
- R-squared is dominated by the central peak. Inspect residuals, side lobes and minima positions as well.
- The sinc-squared curve and PhotonGuard FFT evaluator are two numerical forms of the same scalar Fraunhofer model, not independent physical validations.

## Attribution

This adds a documented public-data transcription and analysis example. It does not replace the existing solver, upstream contributions, project license or original manual attribution. Consult the source manual for its own copyright and redistribution terms.

## Audit and reproduced results

The 63 CSV pairs match the source PDF text table on file page 3, including 17.1 and 34.9 mm. The source's page images could not be retrieved during this audit; this comparison is against extracted PDF text. All measurements remain third-party manual data, not contributor- or user-collected data.

At 650 nm and 504 mm, the whole-curve fit gives effective width 69.6373 um, baseline 11.4505, center 26.01238 mm, RMSE 6.34363 current units (0.79295% of the data range), and R-squared 0.9991826. Residuals are prediction minus reading; maximum absolute residual is 13.8876 units. Their oscillatory side-lobe structure and the minima mismatch should not be hidden by the high R-squared.

The three predicted minima offsets exceed the manually tabulated offsets by 0.20437, 0.50875 and 1.11312 mm. Exact-angle minima inversion gives 72.8029, 73.6295 and 75.6251 um (mean 74.0192 um); the manual uses a small-angle approximation and rounds these to 0.0728, 0.0736 and 0.0756 mm. No uncertainty or significance can be inferred from this one scan.

Symmetry about the manual center uses 31 distinct left/right pairs, excluding the center's self-comparison. Their absolute differences sum to 73 current units, mean 2.35484 units (0.29144% of peak 808), maximum 8 units. The earlier 32-pair statistic included the center and slightly understated the mean.

PhotonGuard FFT RMSE is approximately 6.34865 current units after fitting baseline and scale and using the sinc fit's width. This is agreement between numerical representations with fitted parameters, not a held-out prediction or an independent experimental validation. Local audit reproduction used the evaluator's explicitly labeled NumPy fallback; the official CI runtime remains the full-suite evidence.
