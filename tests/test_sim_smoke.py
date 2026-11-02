import numpy as np
import sim


def test_full_scan_with_fake_dongle(monkeypatch, tmpdir):
    air = sim.Air(noise=0.01).tone(475.0125e6, 0.05)
    scanner, sdr, dt = sim.run_scan(monkeypatch, tmpdir, 470, 480, sdr_kwargs={'air': air})
    f, y = sim.spectrum_arrays(scanner)
    print('points', f.size, 'range', f.min(), f.max(), 'time %.2f' % dt, 'tiles', len(sdr.tuned))
    i = np.argmax(y)
    print('peak at', f[i], y[i], 'median', np.median(y))
    assert abs(f[i] - 475.0125) <= 0.0125
