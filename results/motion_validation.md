# SPHEREx Proper Motion & Parallax Validation Report

- **Command:** `python scripts/validate_motion.py`
- **Timestamp (UTC):** `2026-11-14T18:45:30Z`
- **Data versions used:** `v27`
- **Pre-committed criteria:**
  - Stationary (`pm_lit < 0.05"/yr`): `AGREE` if `|pm| / sigma < 3`, else `DISAGREE`.
  - Moving (`pm_lit >= 0.05"/yr`): `UNINFORMATIVE` if `sigma >= 0.3 * pm_lit` (not a pass); `AGREE` if `|pm_vec - pm_lit_vec| < 3 * sigma` and `sigma < 0.3 * pm_lit`; `DISAGREE` otherwise.

| Target | Status | PM Lit ("/yr) | PM Meas +/- Sigma ("/yr) | Diff (sigma) | Plx Meas +/- Sigma (") | chi2_red | Epochs (Frames) | Version | Window (um) | Diagnostics |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- | :--- | :--- |
| Barnard's Star | **UNINFORMATIVE** | 10.3933 | 5.5559 +/- 21.1471* | 0.70 | 5.2561 +/- 8.0676 | 117425.86 | 4 (6/7) | v27 | D1:0.745-0.843 | Rejected 1 frame(s) below SNR threshold (5.0).; Reduced chi-squared is 117425.86 (> 1): parameter uncertainties inflated by sqrt(reduced_chi2) = 342.67. |
| WISE J085510.83-071442.5 | **UNINFORMATIVE** | 8.1515 | 10.8940 +/- 11.2509 | 1.55 | -4.6645 +/- 3.7537 | 0.11 | 3 (3/7) | v27 | D5:4.145-4.225 | Epoch 4: All 4 frames in epoch rejected (SNR < 5.0).; Rejected 8 frame(s) below SNR threshold (5.0). |
| eps Ind B | **AGREE** | 4.6842 | 4.6075 +/- 0.1540* | 0.76 | 0.5293 +/- 0.1015 | 51.28 | 6 (12/12) | v27 | D5:3.983-4.070 | Epoch 1: One or more frames in epoch included unreliable/saturated pixels in centroiding (excluded from flux).; Reduced chi-squared is 51.28 (> 1): parameter uncertainties inflated by sqrt(reduced_chi2) = 7.16. |
| RW Aurigae | **DISAGREE** | 0.0216 | 0.5271 +/- 0.1041* | 5.06 | 0.4342 +/- 0.0426 | 149.79 | 3 (9/9) | v27 | D3:1.639-1.736 | Epoch 1: One or more frames in epoch included unreliable/saturated pixels in centroiding (excluded from flux).; Epoch 2: One or more frames in epoch included unreliable/saturated pixels in centroiding (excluded from flux).; Epoch 3: One or more frames in epoch included unreliable/saturated pixels in centroiding (excluded from flux).; Reduced chi-squared is 149.79 (> 1): parameter uncertainties inflated by sqrt(reduced_chi2) = 12.24.; SYSTEMATIC: High reduced chi2 (149.79) indicates inter-frame WCS alignment residuals or unmodeled binary/photocentre motion.; SYSTEMATIC: Wavelength span across group is 0.097 um; chromatic centroid shift or LVF gradient may bias positions.; SYSTEMATIC: Unreliable/saturated pixels present in cutout; non-linear core may shift flux-weighted centroid. |
| R Draconis | **UNINFORMATIVE** | 0.0215 | N/A | N/A | N/A | N/A | 0 (0/8) | v27 | D4:3.115-3.192 | SYSTEMATIC: At least 2 epochs are required to fit proper motion, got 0. (saturated/unreliable core masks target pixels; 8 frames rejected) |
