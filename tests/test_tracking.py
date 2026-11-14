# AI-assisted: implemented with Codex.
"""Known-answer synthetic tests for independent per-frame association."""
from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from astropy.wcs import WCS

from frame import Frame
import tracking
from tracking import Prediction, TrackingConfig, track_frames


def frame(x=20., y=20., mjd=60000., amplitude=100., extra=()):
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [359.999, 20.]
    wcs.wcs.crpix = [21, 21]
    wcs.wcs.cdelt = [-6.15 / 3600, 6.15 / 3600]
    yy, xx = np.indices((41, 41))
    image = amplitude * np.exp(-((xx-x)**2 + (yy-y)**2) / (2 * 1.1**2))
    for sx, sy, brightness in extra:
        image += brightness * np.exp(-((xx-sx)**2 + (yy-sy)**2) / (2 * .8**2))
    return Frame(image=image, variance=np.ones(image.shape), mask=np.zeros(image.shape, bool),
                 wavelength=4.1, detector=5, mjd=mjd, wcs=wcs, version=27,
                 uri=f"synthetic://frame/{mjd}", meta={"synthetic": True})


def predict(f, x, y):
    ra, dec = f.wcs.celestial.pixel_to_world_values(x, y)
    return Prediction(float(ra), float(dec))


def test_known_track_positions_errors_and_no_mutation():
    frames = [frame(19.3, 21.2, mjd=60000.), frame(22.2, 19.4, mjd=60180.)]
    originals = copy.deepcopy(frames)
    predictions = [predict(f, x, y) for f, x, y in zip(frames, [19.1, 22.], [21., 19.2])]
    result = track_frames(frames, predictions)
    assert result["synthetic"] is True
    assert result["n_detected"] == 2
    for i, (f, original, truth) in enumerate(zip(frames, originals, [(19.3,21.2), (22.2,19.4)])):
        row = result["frames"][i]
        c = row["measured"]
        assert row["index"] == i
        assert row["mjd"] == f.mjd
        assert row["status"] == "detected"
        assert c["fits_x"] == pytest.approx(truth[0], abs=.15)
        assert c["fits_y"] == pytest.approx(truth[1], abs=.15)
        assert c["x"] == pytest.approx(c["fits_x"] + .5)
        assert c["y"] == pytest.approx(40.5 - c["fits_y"])
        assert c["sigma_x"] > 0 and c["sigma_y"] > 0 and c["snr"] >= 5
        assert c["sigma_ra_arcsec"] == pytest.approx(c["sigma_x"] * 6.15, rel=.01)
        assert np.asarray(c["covariance_arcsec2"]).shape == (2, 2)
        np.testing.assert_array_equal(f.image, original.image)
        np.testing.assert_array_equal(f.mask, original.mask)
        assert f.wcs.to_header() == original.wcs.to_header()


def test_missing_frame_does_not_reuse_centroid_or_guess_nearby_star():
    frames = [frame(), frame(amplitude=0, mjd=60001., extra=[(29,20,500)]), frame(mjd=60002.)]
    result = track_frames(frames, predict(frames[0],20,20))
    assert [r["status"] for r in result["frames"]] == ["detected", "rejected", "detected"]
    assert result["frames"][1]["measured"] is None
    assert result["frames"][1]["predicted"]["x"] == pytest.approx(20.5)


def test_blended_sources_are_ambiguous_and_not_brightest_selected():
    f = frame(17,20,amplitude=80,extra=[(23,20,100)])
    result = track_frames([f], predict(f,20,20))
    assert result["n_detected"] == 0
    assert result["frames"][0]["reason"] == "ambiguous"
    assert result["frames"][0]["measured"] is None


def test_low_snr_masked_and_off_frame_predictions():
    f = frame(amplitude=.1)
    assert track_frames([f], predict(f,20,20))["n_detected"] == 0
    f = frame()
    f.mask[:] = True
    assert track_frames([f], predict(f,20,20))["frames"][0]["measured"] is None
    assert track_frames([f], predict(f,60,20))["frames"][0]["reason"] == "off_frame"


def test_edge_and_search_gate_rejection():
    f = frame(4,20)
    assert track_frames([f], predict(f,4,20))["frames"][0]["reason"] == "edge_truncated"
    f = frame(26,20)
    assert track_frames([f], predict(f,20,20))["n_detected"] == 0


def test_unreliable_core_is_reported_and_version_rejections():
    f = frame()
    f.unreliable = np.zeros(f.image.shape, dtype=bool)
    f.unreliable[20,20] = True
    row = track_frames([f], predict(f,20,20))["frames"][0]
    assert row["status"] == "detected"
    assert any("saturated" in message for message in row["warnings"])
    with pytest.raises(ValueError, match="versions"):
        track_frames([f, f.copy_with(version=28)], predict(f,20,20))


@pytest.mark.parametrize("change,message", [
    ({"detector": 2}, "detector"), ({"wavelength":4.3}, "window"),
    ({"wcs":None}, "WCS"), ({"mjd":59999}, "sorted"),
    ({"zodi":np.ones((41,41))}, "Clean"),
])
def test_sequence_contract(change,message):
    f = frame()
    with pytest.raises(ValueError, match=message):
        track_frames([f,f.copy_with(**change)], predict(f,20,20))


def test_invalid_configuration_and_prediction_counts():
    with pytest.raises(ValueError):
        Prediction(400,0)
    with pytest.raises(ValueError):
        Prediction(0,0,float('nan'))
    with pytest.raises(ValueError):
        TrackingConfig(search_radius_px=-1)
    with pytest.raises(ValueError):
        TrackingConfig(bg_inner_radius=2)
    with pytest.raises(ValueError,match="Prediction"):
        track_frames([frame()], [])
    with pytest.raises(ValueError,match="No SPHEREx"):
        track_frames([],Prediction(0,0))


def test_ra_wrap_is_finite():
    f = frame(18,20)
    row = track_frames([f],predict(f,18,20))["frames"][0]
    assert row["status"] == "detected"
    assert 0 <= row["measured"]["ra"] < 360
    assert np.isfinite(row["measured"]["covariance_arcsec2"]).all()


def test_atomic_report_and_cli(tmp_path,monkeypatch):
    import data
    f=frame()
    result=track_frames([f],predict(f,20,20))
    path=tmp_path/'results'/'tracking.json'
    tracking.write_result(result,path,'python tracking.py --mode synthetic')
    report=json.loads(path.read_text())
    assert report['data_versions']==['27']
    assert report['frames'][0]['uri']==f.uri
    assert report['command']=='python tracking.py --mode synthetic'
    assert not list(path.parent.glob('*.tmp'))
    # Test explicit synthetic CLI mode using the existing data/processing path.
    data.clear_memory_cache()
    path2=tmp_path/'results'/'cli.json'
    assert tracking.main(['--ra','133.76','--dec=-7.24','--mode','synthetic',
                          '--output',str(path2)])==0
    saved=json.loads(path2.read_text())
    assert saved['synthetic'] is True
    assert saved['selection']['mode']=='synthetic'
    assert '--mode synthetic' in saved['command']
    assert saved['n_frames'] > 0
    data.clear_memory_cache()
