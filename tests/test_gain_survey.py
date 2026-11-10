"""V20.5.4: tools/gain_survey.py on the simulator (all gains x frequency)."""
import csv
import glob
import json
import os
import subprocess
import sys
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), 'tools', 'gain_survey.py')


def test_survey_runs_and_saves_everything(tmpdir):
    tools = tmpdir.mkdir('kit')
    script = str(tools.join('gain_survey.py'))
    with open(SCRIPT) as src, open(script, 'w') as dst:
        dst.write(src.read())
    os.symlink(HERE, str(tools.join('tests')))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [os.path.dirname(HERE)] + [p for p in [os.environ.get('PYTHONPATH')] if p]))
    r = subprocess.run([sys.executable, script, '--antenna', '--simulate', '--step', '150', '--hi', '650'],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=600)
    out = r.stdout.decode('utf-8', 'replace')
    assert r.returncode == 0, out[-2000:]
    zips = glob.glob(str(tools.join('gain_survey_ant_*_SIM.zip')))
    assert len(zips) == 1, out[-2000:]
    names = zipfile.ZipFile(zips[0]).namelist()
    for want in ('levels.csv', 'psd.npz', 'meta.json', 'console.txt', 'summary.txt'):
        assert any(n.endswith(want) for n in names), names
    d = zips[0][:-4]
    meta = json.load(open(os.path.join(d, 'meta.json')))
    tiles = [30.0 + 150 * k for k in range(5)]            # 30 ... 630
    assert meta['tiles_done'] == len(tiles) and meta['errors'] == 0
    rows = list(csv.DictReader(open(os.path.join(d, 'levels.csv'))))
    assert len(rows) == len(tiles) * len(meta['gains']) * 2
    z = np.load(os.path.join(d, 'psd.npz'))
    assert z['db'].shape[:3] == (len(tiles), 2, len(meta['gains']))
    # both passes measured the same thing (a steady simulated air)
    assert np.nanmax(np.abs(z['db'][:, 0].astype(float) - z['db'][:, 1].astype(float))) < 3.
    assert 'sleep is held off' not in out or sys.platform == 'win32'
