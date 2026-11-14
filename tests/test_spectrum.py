# AI-assisted: implemented with Codex.
"""Synthetic photometry with analytic flux/variance and independent-noise checks."""
from __future__ import annotations

import copy
import json
import math

import numpy as np
import pytest

import spectrum
from spectrum import PhotometryConfig, aperture_weights, extract_spectrum
from tests.test_tracking import frame, predict


def row_for(f, i=0, detected=True):
    p = predict(f,20,20)
    position = dict(ra=p.ra, dec=p.dec, sigma_ra_arcsec=.1, sigma_dec_arcsec=.1)
    return dict(index=i, mjd=f.mjd, detector=f.detector, wavelength_um=f.wavelength,
                version=f.version, uri=f.uri, status="detected" if detected else "rejected",
                measured=position if detected else None,
                predicted=dict(ra=p.ra, dec=p.dec, sigma_arcsec=None),
                reason=None if detected else "no_qualified_source", warnings=[])


def test_analytic_fractional_aperture_sum_and_uncertainty():
    f=frame(amplitude=0.)
    yy,xx=np.indices(f.image.shape)
    f.image[:]=5.
    f.image[(xx-20)**2+(yy-20)**2<=9]+=10.
    f.variance[:]=4.
    result=extract_spectrum([f],[row_for(f)])
    p=result['frames'][0]
    weights=aperture_weights(f.image.shape,20,20,2.5,5)
    distance=np.hypot(xx-20,yy-20)
    n_bg=np.count_nonzero((distance>=5)&(distance<=8))
    conversion=(6.15/3600*math.pi/180)**2*1e6
    expected_flux=10*np.sum(weights)*conversion
    expected_var=(4*np.sum(weights**2)+np.sum(weights)**2*math.pi/(2*n_bg)*4)*conversion**2
    assert p['flux_jy']==pytest.approx(expected_flux,rel=.002)
    assert p['flux_sigma_jy']==pytest.approx(math.sqrt(expected_var),rel=.002)
    assert p['background_mjy_sr']==pytest.approx(5.)
    assert result['flux_unit']=='Jy'
    assert p['significant_aperture_signal'] is True


def test_mc_scatter_matches_reported_statistical_error():
    rng=np.random.default_rng(20261114)
    measured=[]; sigmas=[]
    for _ in range(120):
        f=frame(amplitude=0.)
        f.image[:]=rng.normal(0,2,f.image.shape)
        f.variance[:]=4
        p=extract_spectrum([f],[row_for(f)])['frames'][0]
        measured.append(p['flux_jy']);sigmas.append(p['flux_sigma_jy'])
    assert np.std(measured,ddof=1)==pytest.approx(np.mean(sigmas),rel=.25)


def test_mask_and_unreliable_pixels_never_enter_flux():
    f=frame(amplitude=0.)
    f.image[:]=5.
    f.image[20,20]=1e9;f.mask[20,20]=True
    f.unreliable=np.zeros(f.image.shape,bool)
    f.image[20,21]=np.nan;f.unreliable[20,21]=True
    p=extract_spectrum([f],[row_for(f)])['frames'][0]
    assert p['flux_jy']==pytest.approx(0,abs=1e-15)
    assert p['flux_sigma_jy']>0
    assert .8<p['aperture_fraction']<1
    assert any('Partial aperture' in w for w in p['warnings'])


def test_missing_positions_require_explicit_forced_and_negative_flux_is_retained():
    f=frame(amplitude=0.)
    yy,xx=np.indices(f.image.shape)
    f.image[(xx-20)**2+(yy-20)**2<=9]=-10
    r=row_for(f,detected=False)
    missing=extract_spectrum([f],[r])['frames'][0]
    assert missing['flux_jy'] is None and missing['reason']=='no_position'
    forced=extract_spectrum([f],[r],allow_forced=True)['frames'][0]
    assert forced['status']=='forced' and forced['position_kind']=='forced'
    assert forced['flux_jy']<0 and forced['snr']<0
    assert forced['significant_aperture_signal'] is False
    assert forced['position']['sigma_arcsec'] is None
    assert any('Forced photometry' in w for w in forced['warnings'])


def test_reject_bad_aperture_background_and_edges():
    f=frame()
    f.mask[17:24,17:24]=True
    assert extract_spectrum([f],[row_for(f)])['frames'][0]['reason']=='insufficient_aperture'
    f=frame()
    yy,xx=np.indices(f.image.shape)
    f.mask[np.hypot(xx-20,yy-20)>4]=True
    assert extract_spectrum([f],[row_for(f)])['frames'][0]['reason']=='insufficient_background'
    f=frame();r=row_for(f)
    p=predict(f,0,20);r['measured'].update(ra=p.ra,dec=p.dec)
    assert extract_spectrum([f],[r])['frames'][0]['reason']=='edge_truncated'


def test_different_bands_dates_versions_are_separate_and_inputs_unchanged():
    a=frame(mjd=60000.);b=frame(mjd=60180.).copy_with(detector=2,wavelength=1.3,version=28)
    originals=copy.deepcopy([a,b]);rows=[row_for(a),row_for(b,1)]
    result=extract_spectrum([a,b],rows)
    assert result['n_points']==2
    assert [p['wavelength_um'] for p in result['frames']]==[4.1,1.3]
    assert [p['detector'] for p in result['frames']]==[5,2]
    assert any('Mixed processing' in w for w in result['warnings'])
    assert any('non-simultaneous' in w for w in result['warnings'])
    for f,original in zip([a,b],originals):
        np.testing.assert_array_equal(f.image,original.image)
        np.testing.assert_array_equal(f.mask,original.mask)
        assert f.wcs.to_header()==original.wcs.to_header()


def test_frame_matching_cleaning_and_configuration_validation():
    f=frame()
    with pytest.raises(ValueError,match='matched'):
        extract_spectrum([f],[])
    r=row_for(f);r['mjd']+=1
    with pytest.raises(ValueError,match='mjd'):
        extract_spectrum([f],[r])
    with pytest.raises(ValueError,match='zodiacal'):
        extract_spectrum([f.copy_with(zodi=np.zeros(f.image.shape))],[row_for(f)])
    with pytest.raises(ValueError,match='WCS'):
        extract_spectrum([f.copy_with(wcs=None)],[row_for(f)])
    with pytest.raises(ValueError):
        PhotometryConfig(subpixels=True)
    with pytest.raises(ValueError):
        PhotometryConfig(annulus_inner_px=2)
    with pytest.raises(ValueError):
        PhotometryConfig(min_aperture_fraction=1.1)
    with pytest.raises(ValueError,match='boolean'):
        extract_spectrum([f],[row_for(f)],allow_forced='yes')


def test_synthetic_cli_writes_report_without_network(tmp_path):
    import data
    data.clear_memory_cache()
    destination=tmp_path/'spectrum.json'
    assert spectrum.main(['--ra','133.76','--dec=-7.24','--mode','synthetic',
                          '--allow-forced','--output',str(destination)])==0
    result=json.loads(destination.read_text())
    assert result['synthetic'] is True
    assert result['n_points']>0
    assert result['flux_unit']=='Jy'
    assert '--allow-forced' in result['command']
    assert result['data_versions']==['20']
    assert all(p['flux_sigma_jy']>0 for p in result['frames'] if p['flux_jy'] is not None)
    assert not list(tmp_path.glob('*.tmp'))
    data.clear_memory_cache()
