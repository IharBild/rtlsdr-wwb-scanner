"""V18: the Bands tables (wwb_scanner.bands) for the QML window."""
import json

from PySide2 import QtCore, QtQml
from PySide2.QtCore import Slot

from wwb_scanner import bands


def _row(r):
    st = bands.STATUS[r['status']]
    return dict(lo=float(r['lo']), hi=float(r['hi']), status=r['status'], label=st[0],
                color=st[2], note=r['note'] or st[1])


class BandsData(QtCore.QObject):
    @Slot(str, result='QVariantList')
    def regions(self, code):
        if not code or code not in bands.COUNTRIES:
            return []
        return [_row(r) for r in bands.regions(code)]

    @Slot(result='QVariantList')
    def countries(self):
        return [dict(c, text='%s (%s)' % (c['name'], c['code'])) for c in bands.countries_list()]

    @Slot(result='QVariantList')
    def legend(self):
        return [dict(status=k, label=bands.STATUS[k][0], text=bands.STATUS[k][1], color=bands.STATUS[k][2])
                for k in bands.SEVERITY]

    @Slot(str, result='QVariantList')
    def systems(self, code):
        out = []
        for s in bands.SYSTEMS:
            sm = bands.system_summary(code, s['id']) if code in bands.COUNTRIES else \
                dict(status='unknown', lines=[], note=s['note'])
            st = bands.STATUS[sm['status']]
            out.append(dict(id=s['id'], maker=s['maker'], system=s['system'], band=s['band'],
                            section='%s %s' % (s['maker'], s['system']),
                            ranges=' + '.join('%s-%s' % (bands.fmt_mhz(a), bands.fmt_mhz(b))
                                              for a, b in s['ranges']),
                            status=sm['status'], label=st[0], color=st[2],
                            detail='\n'.join(sm['lines'] + ([s['note']] if s['note'] else []))))
        return out

    @Slot(str, str, result='QVariantMap')
    def summary(self, code, ids_json):
        """What a scan of the chosen bands would be: ranges, time, rules."""
        try:
            ids = [str(i) for i in json.loads(ids_json or '[]')]
        except ValueError:
            ids = []
        chosen = bands.selection(ids)
        segs, outside = bands.scan_segments(chosen)
        lines = []
        for lo, hi, parts, w in bands.check(code, segs):
            lines.append('%s-%s MHz: %s' % (bands.fmt_mhz(lo), bands.fmt_mhz(hi), bands.STATUS[w][0]))
            for p in parts:
                if p['status'] != 'free':
                    lines.append('    %s-%s: %s%s' % (bands.fmt_mhz(p['lo']), bands.fmt_mhz(p['hi']),
                                                    bands.STATUS[p['status']][0],
                                                    (' - ' + p['note']) if p['note'] else ''))
        for lo, hi in outside:
            lines.append('%s-%s MHz: the RTL-SDR can not scan it (above %d MHz)'
                         % (bands.fmt_mhz(lo), bands.fmt_mhz(hi), bands.DONGLE_MAX_MHZ))
        secs = bands.estimate_seconds(segs)
        full = bands.estimate_seconds([(segs[0][0], segs[-1][1])]) if segs else 0
        c = bands.COUNTRIES.get(code)
        return dict(segments=json.dumps([[a, b] for a, b in segs]), count=len(segs),
                    lo=segs[0][0] if segs else 0, hi=segs[-1][1] if segs else 0,
                    seconds=round(secs), fullSeconds=round(full),
                    text='\n'.join(lines),
                    worst=bands.worst(w for _, _, _, w in bands.check(code, segs)) if segs else 'unknown',
                    source=(c['source'] if c else ''), regulator=(c['regulator'] or '') if c else '',
                    url=(c['url'] or '') if c else '', note=(c['note'] if c else ''))


def register_qml_types():
    QtQml.qmlRegisterType(BandsData, 'ScanTools', 1, 0, 'BandsData')
