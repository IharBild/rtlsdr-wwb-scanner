# -*- coding: utf-8 -*-
"""Проверка стабильности p90 сценария uhf_clean_autogain."""
import os
import sys
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests'))
import golden_scenarios as G
from _pytest.monkeypatch import MonkeyPatch

for i in range(4):
    mp = MonkeyPatch()
    try:
        m = G.metrics(G.run('uhf_clean_autogain', mp, tempfile.mkdtemp()))
    finally:
        mp.undo()
    print(i, 'p90=%.3f' % m['p90'], 'remeasured=%d' % m['remeasured'], 'overload=%r' % m['overload'])
