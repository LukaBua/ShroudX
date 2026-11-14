# Source of truth: read this before the Statistician Brief

Owner: Luka. For Aghamusa first, but everyone should read it.

The Statistician Brief (`Statistician_Brief_SPHEREx.pdf`) is good background for the statistics, but it was written as a general guide, not for our code. Where it disagrees with our own files, **our files win**, in this order:

1. `frame.py` (what a frame is)
2. `docs/api_contract.md` (what the backend returns)
3. The Core Build Prompt Pack (module names, interfaces, who owns what)
4. The Statistician Brief (background only)

## Where the brief and our files differ

| Topic | Statistician Brief | What we do | Why |
| --- | --- | --- | --- |
| Masking flags | Sample code masks `flags != 0`. | Never mask on `flags != 0`. Use `flags.flags_to_mask(...)`. | The SOURCE bit (21) is set on many good pixels. The decoder and bit table come from a real FLAGS header. |
| Zodiacal light | Says to check whether IMAGE is already subtracted. | It is **not** subtracted. `clean_frame` subtracts ZODI. | IRSA's SPHEREx intro tutorial says no zodiacal light subtraction is applied to IMAGE, and the data-products page says ZODI has not been subtracted. |
| Function names | `stack()`, `difference()`, `significance_map()`, `fit_motion()`, `extract_spectrum()` | `clean_frame`, `group_frames`, `split_epochs`, `align_frames`, `stack_frames`, `render_png` (Phase 2: `difference_images`, `render_diff_png`) | These are the Shared interfaces in the pack. Other modules call them by these names. |
| Who does motion | The statistician writes `fit_motion()`. | Emin owns `motion.py` (Prompt 10). | Avoids two people building the same thing. Aghamusa reviews Emin's error model. |
| Spectra | `extract_spectrum()` is extra credit. | Out of scope for now. | Not in the core or Phase 2 plan. |
| Input type | Raw arrays and lists. | Functions take and return `Frame` objects. | See `frame.py`. |

## The brief's "day 1 questions", answered

- **Is IMAGE already zodi-subtracted?** No. Subtract ZODI.
- **Flag bit meanings?** In `flags.py`, taken from a real FLAGS header.
- **Units?** IMAGE and ZODI are in MJy/sr; VARIANCE is in (MJy/sr)^2; sigma = sqrt(VARIANCE).
- **Wavelength of a pixel?** From the WCS-WAVE extension (spectral WCS, key W), evaluated at the target pixel. Yusuf's `data.py` does this and stores it in `Frame.wavelength`.

## Still useful from the brief

Inverse-variance weighting, sigma-clipping, the empirical noise check (compare measured empty-sky scatter with propagated sigma), correlated noise after reprojection, and the multiple-comparisons warning for the mover search. Use these ideas, with the names and types above.

If something here is wrong or out of date, tell Luka. Do not change this file yourself.
