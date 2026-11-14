# AI-assisted: written with Claude (Anthropic).
"""Unit tests for the SPHEREx FLAGS decoder."""

import numpy as np
import pytest
from astropy.io import fits

import flags
from flags import (
    ALL_FLAG_BITS,
    DETECTOR_DEFECTS,
    EXTRA_FLAG_BITS,
    FLAG_BITS,
    GROUPS,
    IMAGE_ARTIFACTS,
    INFORMATION_ONLY,
    TRANSIENTS,
    UNRELIABLE_CORE,
    decode_flags,
    flags_to_mask,
    summarize,
    verify_against_header,
)

# The MP_* cards copied from a real FLAGS header (IRSA cutout, 29x29 pixels).
REAL_HEADER_CARDS = [
    "HIERARCH MP_TRANSIENT = 0 / Transient detected during SUR",
    "HIERARCH MP_OVERFLOW = 1 / Overflow detected during SUR",
    "HIERARCH MP_SUR_ERROR = 2 / Error in onboard processing",
    "HIERARCH MP_PHANTOM = 4 / Phantom pixel",
    "HIERARCH MP_REFERENCE = 5 / Reference pixel",
    "HIERARCH MP_NONFUNC = 6 / Permanently unusable",
    "HIERARCH MP_DICHROIC = 7 / Low efficiency due to dichroic",
    "HIERARCH MP_MISSING_DATA = 9 / Onboard data lost",
    "MP_HOT  =                   10 / Hot pixel",
    "MP_COLD =                   11 / Anomalously low signal",
    "HIERARCH MP_FULLSAMPLE = 12 / Pixel full sample history is available",
    "HIERARCH MP_PHANMISS = 14 / Phantom correction was not applied",
    "HIERARCH MP_NONLINEAR = 15 / Nonlinearity correction cannot be applied reliably",
    "HIERARCH MP_PERSIST = 17 / Persistent charge above threshold",
    "HIERARCH MP_OUTLIER = 19 / Pixel flagged by Detect Outliers",
    "HIERARCH MP_SOURCE = 21 / Pixel mapped to a known source",
]


def bit(name: str) -> int:
    """Return the integer value of one named flag."""
    return 1 << FLAG_BITS[name]


def test_table_matches_real_header() -> None:
    """FLAG_BITS must match the MP_* keywords of a real FLAGS header exactly."""
    text = "".join(f"{card:<80}" for card in REAL_HEADER_CARDS)
    header = fits.Header.fromstring(text)
    assert verify_against_header(header) == []


def test_verify_against_header_reports_problems() -> None:
    """A wrong, missing or extra keyword is reported."""
    header = {f"MP_{name}": b for name, b in FLAG_BITS.items()}
    header["MP_HOT"] = 3  # wrong bit
    del header["MP_COLD"]  # missing
    header["MP_NEW"] = 25  # unknown to FLAG_BITS
    problems = verify_against_header(header)
    assert len(problems) == 3
    assert any("HOT" in p and "bit 3" in p for p in problems)
    assert any("COLD" in p for p in problems)
    assert any("NEW" in p for p in problems)


def test_sixteen_flags_and_unique_bits() -> None:
    """There are 16 defined flags, each with its own bit."""
    assert len(FLAG_BITS) == 16
    assert len(set(FLAG_BITS.values())) == 16


def test_groups_partition_all_flags() -> None:
    """Every flag belongs to exactly one group."""
    all_names = [name for group in GROUPS.values() for name in group]
    assert sorted(all_names) == sorted(ALL_FLAG_BITS)


def test_group_membership_of_key_flags() -> None:
    """Spot-check the rationale of the groups."""
    assert "HOT" in DETECTOR_DEFECTS
    assert "NONFUNC" in DETECTOR_DEFECTS
    assert "NONLINEAR" in UNRELIABLE_CORE
    assert "OVERFLOW" in UNRELIABLE_CORE
    assert "OUTLIER" in TRANSIENTS
    assert "SOURCE" in INFORMATION_ONLY
    assert "FULLSAMPLE" in INFORMATION_ONLY
    assert {"GHOST", "STREAK", "BLOOM", "HALO"} <= IMAGE_ARTIFACTS


def test_no_function_masks_all_nonzero_flags() -> None:
    """The module offers nothing that masks every non-zero flag."""
    public = {n for n in dir(flags) if not n.startswith("_")}
    assert not {n for n in public if "nonzero" in n.lower() or "any_flag" in n.lower()}


def test_single_bit_values() -> None:
    """Each defined flag masks only when it is part of the requested names."""
    arr = np.array([bit("HOT"), bit("COLD"), bit("OUTLIER")], dtype=np.int32)
    assert flags_to_mask(arr).tolist() == [True, True, False]
    assert flags_to_mask(arr, ["OUTLIER"]).tolist() == [False, False, True]


def test_multi_bit_value() -> None:
    """A pixel with several flags is masked if any requested flag is set."""
    value = bit("SOURCE") | bit("OUTLIER") | bit("NONFUNC")
    arr = np.array([value], dtype=np.int32)
    assert flags_to_mask(arr).tolist() == [True]
    assert flags_to_mask(arr, UNRELIABLE_CORE).tolist() == [False]


def test_source_only_pixel_is_not_masked() -> None:
    """The SOURCE bit alone must not mask a pixel."""
    arr = np.full((4, 4), bit("SOURCE"), dtype=np.int32)
    assert not flags_to_mask(arr).any()


def test_hot_pixel_is_masked() -> None:
    """A hot pixel is masked by default."""
    arr = np.zeros((3, 3), dtype=np.int32)
    arr[1, 2] = bit("HOT")
    mask = flags_to_mask(arr)
    assert mask[1, 2]
    assert mask.sum() == 1


def test_unreliable_group_selection() -> None:
    """UNRELIABLE_CORE selects saturated and non-linear pixels only."""
    arr = np.array([bit("OVERFLOW"), bit("NONLINEAR"), bit("HOT"), 0], dtype=np.int32)
    assert flags_to_mask(arr, UNRELIABLE_CORE).tolist() == [True, True, False, False]


@pytest.mark.parametrize(
    "dtype", [np.uint16, np.int32, np.int64, np.uint32, np.float32, np.float64]
)
def test_input_dtypes(dtype) -> None:
    """Common dtypes work; a uint16 array must not raise OverflowError."""
    arr = np.array([0, bit("HOT"), bit("NONFUNC") | 1], dtype=dtype)
    assert flags_to_mask(arr).tolist() == [False, True, True]
    assert flags_to_mask(arr, ["SOURCE"]).tolist() == [False, False, False]


def test_uint16_with_high_bit_names() -> None:
    """Asking a uint16 array about bit 21 must work (NumPy 2 overflow pitfall)."""
    arr = np.array([0, bit("HOT")], dtype=np.uint16)
    assert flags_to_mask(arr, ["SOURCE"]).tolist() == [False, False]


def test_big_endian_input() -> None:
    """FITS arrays are big-endian; they must decode correctly."""
    arr = np.array([bit("HOT"), 0], dtype=">i4")
    assert flags_to_mask(arr).tolist() == [True, False]


def test_shape_preserved() -> None:
    """The mask has the same shape as the input, for any dimensionality."""
    for shape in [(5,), (6, 7), (2, 3, 4)]:
        arr = np.zeros(shape, dtype=np.int32)
        mask = flags_to_mask(arr)
        assert mask.shape == shape
        assert mask.dtype == bool


def test_non_finite_float_flags_are_masked() -> None:
    """NaN or inf flag values cannot be trusted, so they are masked."""
    arr = np.array([0.0, np.nan, np.inf, float(bit("SOURCE"))])
    assert flags_to_mask(arr).tolist() == [False, True, True, False]


def test_non_integer_float_flags_raise() -> None:
    """Float flags must be whole numbers."""
    with pytest.raises(ValueError, match="whole numbers"):
        flags_to_mask(np.array([1.5]))


def test_bad_dtype_raises() -> None:
    """Bool and string arrays are not flag words."""
    with pytest.raises(TypeError, match="integer or float"):
        flags_to_mask(np.array([True, False]))
    with pytest.raises(TypeError, match="integer or float"):
        flags_to_mask(np.array(["a"]))


def test_unknown_flag_name_raises() -> None:
    """An unknown flag name is an error, not silently ignored."""
    with pytest.raises(ValueError, match="unknown flag names"):
        flags_to_mask(np.zeros(3, dtype=np.int32), ["HOTT"])


def test_single_string_name_accepted() -> None:
    """A single name given as a string is treated as one name, not letters."""
    arr = np.array([bit("HOT"), 0], dtype=np.int32)
    assert flags_to_mask(arr, "HOT").tolist() == [True, False]


def test_negative_int32_uses_bit_31() -> None:
    """A negative int32 (bit 31 set) is an undefined bit, not a sign extension."""
    arr = np.array([np.iinfo(np.int32).min], dtype=np.int32)
    assert not flags_to_mask(arr).any()
    assert decode_flags(arr[0]) == ["UNDEFINED_BIT_31"]


def test_decode_flags() -> None:
    """decode_flags lists names ordered by bit number."""
    value = bit("SOURCE") | bit("HOT") | bit("TRANSIENT")
    assert decode_flags(value) == ["TRANSIENT", "HOT", "SOURCE"]
    assert decode_flags(0) == []
    assert decode_flags(np.int32(bit("OUTLIER"))) == ["OUTLIER"]
    assert decode_flags(float(bit("HOT"))) == ["HOT"]


def test_decode_undefined_bit_is_reported() -> None:
    """Undefined bits are reported, not dropped."""
    assert decode_flags(1 << 3) == ["UNDEFINED_BIT_3"]
    assert decode_flags((1 << 3) | bit("HOT")) == ["UNDEFINED_BIT_3", "HOT"]


def test_decode_non_finite_and_arrays() -> None:
    """NaN decodes as NON_FINITE; arrays are rejected."""
    assert decode_flags(float("nan")) == ["NON_FINITE"]
    with pytest.raises(ValueError, match="single pixel value"):
        decode_flags(np.zeros(3, dtype=np.int32))


def test_summarize_counts() -> None:
    """summarize counts pixels per flag, including multi-flag pixels."""
    arr = np.array(
        [
            [0, bit("HOT"), bit("HOT") | bit("SOURCE")],
            [bit("SOURCE"), 1 << 3, 1 << 3],
        ],
        dtype=np.int32,
    )
    counts = summarize(arr)
    assert counts["HOT"] == 2
    assert counts["SOURCE"] == 2
    assert counts["NONFUNC"] == 0
    assert counts["UNDEFINED_BIT_3"] == 2
    assert "NON_FINITE" not in counts
    assert set(FLAG_BITS) <= set(counts)


def test_summarize_non_finite() -> None:
    """Non-finite float flags are counted separately."""
    counts = summarize(np.array([0.0, np.nan, float(bit("HOT"))]))
    assert counts["NON_FINITE"] == 1
    assert counts["HOT"] == 1


def test_extra_bits_do_not_overlap_core_bits() -> None:
    """Extra bits use their own names and bit numbers."""
    assert not set(EXTRA_FLAG_BITS) & set(FLAG_BITS)
    assert not set(EXTRA_FLAG_BITS.values()) & set(FLAG_BITS.values())
    assert len(ALL_FLAG_BITS) == len(FLAG_BITS) + len(EXTRA_FLAG_BITS)


def test_extra_bits_are_decoded_by_name_and_not_masked_by_default() -> None:
    """Documented newer bits get names, but do not mask unless asked."""
    arr = np.array([1 << 25, 1 << 26, 1 << 22], dtype=np.int64)
    assert decode_flags(arr[0]) == ["STREAK"]
    assert decode_flags(arr[1]) == ["BLOOM"]
    assert not flags_to_mask(arr).any()
    assert flags_to_mask(arr, IMAGE_ARTIFACTS).all()
    assert flags_to_mask(arr, ["GHOST"]).tolist() == [False, False, True]


def test_summarize_includes_extra_bits_only_when_present() -> None:
    """Extra flags are listed only when they occur; core flags always are."""
    counts = summarize(np.array([0, 1 << 27], dtype=np.int32))
    assert counts["SNOWBALL"] == 1
    assert "STREAK" not in counts
    assert counts["HOT"] == 0


def test_verify_accepts_extra_bits_and_rejects_wrong_ones() -> None:
    """Header keywords for extra bits are checked against their bit numbers."""
    header = {f"MP_{name}": b for name, b in FLAG_BITS.items()}
    header["MP_STREAK"] = 25
    assert verify_against_header(header) == []
    header["MP_STREAK"] = 24
    problems = verify_against_header(header)
    assert len(problems) == 1
    assert "STREAK" in problems[0]


def test_verify_non_strict_allows_missing_core_flags() -> None:
    """Newer headers may drop core flags; strict=False tolerates that."""
    header = {f"MP_{name}": b for name, b in FLAG_BITS.items() if name != "PHANTOM"}
    assert len(verify_against_header(header)) == 1
    assert verify_against_header(header, strict=False) == []
