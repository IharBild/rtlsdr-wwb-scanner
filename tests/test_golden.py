"""Regression: fixed simulator scenarios against tests/reference/golden.json."""
import pytest

import golden_scenarios as G


@pytest.mark.parametrize('name', sorted(G.SCENARIOS))
def test_R1_golden_scenario(name, monkeypatch, tmpdir):
    ref = G.load_golden()
    if ref is None or name not in ref.get('scenarios', {}):
        pytest.skip('no reference yet: python tools/qa_sandbox.py --update-golden')
    got = G.metrics(G.run(name, monkeypatch, tmpdir))
    diff = G.compare(got, ref['scenarios'][name])
    assert not diff, 'changed against the reference (%s): %s' % (ref.get('version'), '; '.join(diff))
