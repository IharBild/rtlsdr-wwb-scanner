# -*- coding: utf-8 -*-
"""V18 "Bands": where wireless microphones and in-ear monitors may work.

Two tables and a few helpers, no Qt (the window, a future web page and the
tests use the same code):

* SYSTEMS   - frequency bands of Shure and Sennheiser systems (EU variants).
* COUNTRIES - what the regulator of each country allows for PMSE audio
              (radio microphones, in-ear monitors) in each part of the
              spectrum the RTL-SDR can see.

Sources (checked 2026-11):
* CEPT ECO, ERC/REC 25-10 Annex 4, national implementation table (EFIS)
  https://efis.cept.org/ -> Reports -> ERC/REC 25-10 Annex 4
* Germany: Bundesnetzagentur general allocation for PMSE (until 31.12.2032)
* Romania: ANCOM RO-IR MICRO, edition 5/2025 (22.12.2025)
* Ukraine: NKEK resolution No 361 of 03.07.2024 (lists of radio equipment)
* Manufacturer band charts (Shure, Sennheiser) for the systems.

The tables are a guide for planning, not a legal document: rules change
and many countries ask for a licence or registration. Always check the
national regulator before a show.
"""
from __future__ import division

import math

DATA_DATE = '2026-11'
DONGLE_MIN_MHZ = 24.0
DONGLE_MAX_MHZ = 1766.0

# status -> (short label, explanation, colour)
STATUS = {
    'free':    ('Licence-free', 'general authorisation, nothing to apply for', '#2e9e5b'),
    'light':   ('Registration', 'allowed after a simple registration / light licence', '#5fb35a'),
    'allowed': ('Allowed', 'allowed for PMSE; ask the regulator for the conditions', '#8cbf3f'),
    'licence': ('Licence', 'needs a licence / frequency assignment', '#e0a526'),
    'limited': ('Limited', 'only part of the band, lower power or indoor only', '#e07b26'),
    'no':      ('Not allowed', 'not for wireless mics here (mobile network, military...)', '#d64545'),
    'unknown': ('No data', 'no reliable data: check with the regulator', '#6f7680'),
}
# for a warning the worst part counts
SEVERITY = ['free', 'light', 'allowed', 'licence', 'limited', 'unknown', 'no']


# ------------------------------------------------------------------ systems
# (maker, system, band name, [(lo, hi), ...] MHz, note)
_S = [
    ('Shure', 'PSM 300', 'H8E/H20', [(518, 542)], ''),
    ('Shure', 'PSM 300', 'K3E', [(606, 630)], ''),
    ('Shure', 'PSM 300', 'K14', [(614, 638)], ''),
    ('Shure', 'PSM 300', 'L19', [(630, 654)], ''),
    ('Shure', 'PSM 300', 'M16', [(686, 710)], ''),
    ('Shure', 'PSM 300', 'Q25', [(742, 766)], ''),
    ('Shure', 'PSM 300', 'S8', [(823, 832), (863, 865)], ''),
    ('Shure', 'PSM 300', 'T11', [(863, 865)], ''),
    ('Shure', 'PSM 900', 'G6E', [(470, 506)], ''),
    ('Shure', 'PSM 900', 'G7E', [(506, 542)], ''),
    ('Shure', 'PSM 900', 'K1E', [(596, 632)], ''),
    ('Shure', 'PSM 900', 'L6E', [(656, 692)], ''),
    ('Shure', 'PSM 900', 'P7', [(702, 742)], ''),
    ('Shure', 'PSM 900', 'Q15', [(750, 790)], ''),
    ('Shure', 'PSM 900', 'R22', [(790, 830)], ''),
    ('Shure', 'PSM 1000', 'G10E', [(470, 542)], ''),
    ('Shure', 'PSM 1000', 'J8E', [(554, 626)], ''),
    ('Shure', 'PSM 1000', 'K10E', [(596, 668)], ''),
    ('Shure', 'PSM 1000', 'L8E', [(626, 698)], ''),
    ('Shure', 'PSM 1000', 'L9E', [(670, 742)], ''),
    ('Shure', 'PSM 1000', 'Q22E', [(750, 822)], ''),
    ('Shure', 'PSM 200', 'H2', [(518, 554)], ''),
    ('Shure', 'PSM 200', 'K9E', [(606, 638)], ''),
    ('Shure', 'PSM 200', 'Q3', [(748, 784)], ''),
    ('Shure', 'PSM 200', 'S5', [(842, 865)], ''),
    ('Shure', 'QLX-D', 'G51', [(470, 534)], ''),
    ('Shure', 'QLX-D', 'H51', [(534, 598)], ''),
    ('Shure', 'QLX-D', 'K51', [(606, 670)], ''),
    ('Shure', 'QLX-D', 'L52', [(632, 694)], ''),
    ('Shure', 'QLX-D', 'P51', [(710, 782)], ''),
    ('Shure', 'QLX-D', 'V51', [(174, 216)], ''),
    ('Shure', 'QLX-D', 'S50', [(823, 832), (863, 865)], ''),
    ('Shure', 'ULX-D', 'G51', [(470, 534)], ''),
    ('Shure', 'ULX-D', 'H51', [(534, 598)], ''),
    ('Shure', 'ULX-D', 'K51', [(606, 670)], ''),
    ('Shure', 'ULX-D', 'P51', [(710, 782)], ''),
    ('Shure', 'ULX-D', 'V51', [(174, 216)], ''),
    ('Shure', 'ULX-D', 'S50', [(823, 832), (863, 865)], ''),
    ('Shure', 'SLX-D', 'G59', [(470, 514)], ''),
    ('Shure', 'SLX-D', 'H55', [(514, 558)], ''),
    ('Shure', 'SLX-D', 'H56', [(518, 562)], ''),
    ('Shure', 'SLX-D', 'J53', [(562, 606)], ''),
    ('Shure', 'SLX-D', 'K59', [(606, 650)], ''),
    ('Shure', 'SLX-D', 'L56', [(650, 694)], ''),
    ('Shure', 'SLX-D', 'S50', [(823, 832)], ''),
    ('Shure', 'Axient Digital', 'G56', [(470, 636)], ''),
    ('Shure', 'Axient Digital', 'K55', [(606, 694)], ''),
    ('Shure', 'Axient Digital', 'K56', [(606, 714)], ''),
    ('Shure', 'UHF-R', 'G1E', [(470, 530)], ''),
    ('Shure', 'UHF-R', 'H4E', [(518, 578)], ''),
    ('Shure', 'UHF-R', 'J5E', [(578, 638)], ''),
    ('Shure', 'UHF-R', 'K4E', [(606, 666)], ''),
    ('Shure', 'UHF-R', 'L3E', [(638, 698)], ''),
    ('Shure', 'UHF-R', 'P8', [(710, 790)], ''),
    ('Shure', 'UHF-R', 'Q5', [(740, 814)], ''),
    ('Shure', 'UHF-R', 'R9', [(790, 865)], ''),
    ('Shure', 'BLX', 'H8E', [(518, 542)], ''),
    ('Shure', 'BLX', 'K3E', [(606, 630)], ''),
    ('Shure', 'BLX', 'K14', [(614, 638)], ''),
    ('Shure', 'BLX', 'M17', [(662, 686)], ''),
    ('Shure', 'BLX', 'Q25', [(742, 766)], ''),
    ('Shure', 'BLX', 'S8', [(823, 832), (863, 865)], ''),
    ('Shure', 'BLX', 'T11', [(863, 865)], ''),
    ('Shure', 'SLX', 'G4E', [(470, 494)], ''),
    ('Shure', 'SLX', 'G5E', [(494, 518)], ''),
    ('Shure', 'SLX', 'H5', [(518, 542)], ''),
    ('Shure', 'SLX', 'J3', [(572, 596)], ''),
    ('Shure', 'SLX', 'K3E', [(606, 630)], ''),
    ('Shure', 'SLX', 'L4E', [(638, 662)], ''),
    ('Shure', 'SLX', 'P4', [(702, 726)], ''),
    ('Shure', 'SLX', 'S6', [(838, 865)], ''),
    ('Shure', 'PGX', 'H6', [(524, 542)], ''),
    ('Shure', 'PGX', 'K5E', [(606, 630)], ''),
    ('Shure', 'GLX-D', '2.4 GHz', [(2400, 2483.5)], 'above 1766 MHz: the RTL-SDR can not see it'),
    ('Sennheiser', 'ew 100 G3/G4', 'A1', [(470, 516)], ''),
    ('Sennheiser', 'ew 100 G3/G4', 'A', [(516, 558)], ''),
    ('Sennheiser', 'ew 100 G3/G4', 'AS', [(520, 558)], ''),
    ('Sennheiser', 'ew 100 G3/G4', 'G', [(566, 608)], ''),
    ('Sennheiser', 'ew 100 G3/G4', 'GB', [(606, 648)], ''),
    ('Sennheiser', 'ew 100 G3/G4', 'B', [(626, 668)], ''),
    ('Sennheiser', 'ew 100 G3/G4', 'C', [(734, 776)], ''),
    ('Sennheiser', 'ew 100 G3/G4', 'D', [(780, 822)], ''),
    ('Sennheiser', 'ew 100 G3/G4', 'E', [(823, 865)], ''),
    ('Sennheiser', 'ew 100 G3/G4', '1G8', [(1785, 1800)], 'above 1766 MHz: the RTL-SDR can not see it'),
    ('Sennheiser', 'ew 300 G4', 'Aw+', [(470, 558)], ''),
    ('Sennheiser', 'ew 300 G4', 'AS', [(520, 558)], ''),
    ('Sennheiser', 'ew 300 G4', 'Gw1', [(558, 608)], ''),
    ('Sennheiser', 'ew 300 G4', 'Gw', [(558, 626)], ''),
    ('Sennheiser', 'ew 300 G4', 'GBw', [(606, 678)], ''),
    ('Sennheiser', 'ew 300 G4', 'Bw', [(626, 698)], ''),
    ('Sennheiser', 'ew 300 G4', 'Cw', [(718, 790)], ''),
    ('Sennheiser', 'ew 300 G4', 'Dw', [(790, 865)], ''),
    ('Sennheiser', 'ew 300 G4', '1G8', [(1785, 1800)], 'above 1766 MHz: the RTL-SDR can not see it'),
]


def _sys_id(maker, system, band):
    return '%s|%s|%s' % (maker, system, band)


SYSTEMS = [dict(id=_sys_id(m, s, b), maker=m, system=s, band=b,
                ranges=[(float(lo), float(hi)) for lo, hi in r], note=n)
           for m, s, b, r, n in _S]
SYSTEMS_BY_ID = dict((s['id'], s) for s in SYSTEMS)


# ------------------------------------------------------------------ countries
# CEPT ERC/REC 25-10 annex 4 bands
A2 = (174.0, 216.0)
A3 = (470.0, 694.0)
A5 = (823.0, 832.0)
A6 = (863.0, 865.0)
A7 = (1350.0, 1400.0)
A8 = (1518.0, 1525.0)
GAP700 = [(694.0, 703.0), (733.0, 758.0)]       # centre and duplex gap of the 700 MHz band

# Bands used by mobile networks in the EU/EEA, CH and UK (700 MHz band
# re-farmed). Applied first; a national PMSE rule on top wins.
_MOBILE = [
    (694, 703, 'no', '700 MHz centre gap: not open for PMSE here'),
    (733, 758, 'no', '700 MHz duplex gap: not open for PMSE here'),
    (788, 791, 'no', 'guard band of the mobile networks'),
    (821, 823, 'no', 'guard band of the mobile networks'),
    (862, 863, 'no', 'guard band of the mobile networks'),
    (703, 733, 'no', 'LTE/5G 700 MHz (mobile uplink)'),
    (758, 788, 'no', 'LTE/5G 700 MHz (mobile downlink)'),
    (791, 821, 'no', 'LTE 800 MHz (mobile downlink)'),
    (832, 862, 'no', 'LTE 800 MHz (mobile uplink)'),
    (880, 915, 'no', 'GSM/LTE 900 (mobile)'),
    (925, 960, 'no', 'GSM/LTE 900 (mobile)'),
    (1427, 1517, 'no', '1.5 GHz mobile downlink (SDL)'),
    (1710, 1766, 'no', 'LTE 1800 (mobile uplink)'),
]
_SRD = [(863, 865, 'free', 'EU short range devices: wireless audio 10 mW, no licence')]


def _r(rng, status, note=''):
    return (float(rng[0]), float(rng[1]), status, note)


def _gaps(status, note=''):
    return [_r(g, status, note) for g in GAP700]


def _country(code, name, group, rules, source='CEPT', regulator=None, url=None, note='',
             mobile=True, srd=True):
    full = []
    if mobile:
        full += [_r((a, b), s, n) for a, b, s, n in _MOBILE]
    if srd:
        full += [_r((a, b), s, n) for a, b, s, n in _SRD]
    full += list(rules)
    return dict(code=code, name=name, group=group, rules=full, source=source,
                regulator=regulator, url=url, note=note)


_CEPT = 'CEPT ERC/REC 25-10 Annex 4, national implementation (EFIS), 2026-11'
_EU, _EEA, _OTHER = 'EU', 'Schengen / EEA', 'Other'

_C = [
    _country('PL', 'Poland', _EU, [
        _r(A2, 'allowed', 'PMSE in 174-216 MHz'),
        _r(A3, 'free', 'licence-free up to 50 mW (EN 300 422)'),
        _r((694, 698), 'limited', '700 MHz centre gap, only 694-698'),
        _r((736, 738), 'limited', '700 MHz duplex gap, only 736-738'),
        _r(A5, 'allowed', 'LTE 800 duplex gap (EU decision 2014/641)'),
        _r(A7, 'no', 'military'), _r(A8, 'no', 'military'),
    ], regulator='UKE', url='https://www.uke.gov.pl'),
    _country('DE', 'Germany', _EU, [
        _r((174, 230), 'free', 'general allocation, up to 50 mW'),
        _r((470, 608), 'free', 'general allocation for PMSE until 31.12.2032, up to 50 mW'),
        _r((608, 614), 'no', 'radio astronomy (TV channel 38)'),
        _r((614, 698), 'free', 'general allocation until 2032; 5G Broadcast tests: 614-622 Berlin, '
                               '622-630 Hamburg/Halle/Stuttgart, 646-654 Munich'),
        _r((736, 753), 'free', '700 MHz duplex gap, general allocation'),
        _r(A5, 'limited', 'up to 100 mW (823-826 MHz: 82 mW handheld)'),
        _r(A7, 'limited', 'indoor only'),
        _r((1492, 1518), 'limited', 'indoor only, with an assignment'),
        _r(A8, 'limited', 'indoor only'),
    ], source='Bundesnetzagentur general allocation; ' + _CEPT, regulator='BNetzA',
        url='https://www.bundesnetzagentur.de'),
    _country('LT', 'Lithuania', _EU, [
        _r(A2, 'light', 'light licensing (registration)'),
        _r(A3, 'light', 'light licensing (registration)'),
    ] + _gaps('limited', 'being re-farmed for mobile (IMT 700)') + [
        _r(A5, 'light', 'light licensing (registration)'),
        _r(A7, 'light', 'light licensing (registration)'),
        _r(A8, 'light', 'light licensing (registration)'),
    ], regulator='RRT', url='https://www.rrt.lt'),
    _country('AT', 'Austria', _EU, [
        _r(A2, 'light', 'light licensing, up to 50 mW'),
        _r(A3, 'light', 'TV white spaces, light licensing'),
    ] + _gaps('limited', 'only the part not used by mobile') + [
        _r(A5, 'allowed', '823-826: 100 mW body-worn / 20 mW; 826-832: 100 mW'),
        _r(A7, 'light', 'light licensing'), _r(A8, 'light', 'light licensing'),
    ]),
    _country('BE', 'Belgium', _EU, [
        _r(A2, 'limited', 'conditions apply'),
        _r(A3, 'allowed', 'conditions apply (not detailed in the CEPT table)'),
    ] + _gaps('limited', 'conditions apply') + [
        _r(A5, 'unknown', 'no entry in the CEPT table'),
        _r(A7, 'no', ''), _r(A8, 'no', ''),
    ]),
    _country('BG', 'Bulgaria', _EU, [
        _r(A2, 'allowed'), _r(A3, 'allowed'),
        _r((723, 733), 'limited', 'listed in the CEPT table: 723-753 and 778-789'),
        _r((733, 753), 'limited', 'listed in the CEPT table: 723-753 and 778-789'),
        _r((778, 789), 'limited', 'listed in the CEPT table: 723-753 and 778-789'),
        _r(A5, 'allowed'), _r(A7, 'allowed'), _r(A8, 'allowed'),
    ]),
    _country('HR', 'Croatia', _EU, [
        _r(A2, 'no'), _r(A3, 'allowed'),
    ] + _gaps('no') + [
        _r(A5, 'limited', 'only 823-832'),
        _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('CY', 'Cyprus', _EU, [
        _r(A3, 'allowed'),
    ] + _gaps('limited')),
    _country('CZ', 'Czechia', _EU, [
        _r((174, 230), 'free', 'general authorisation 50 mW (100 mW with an individual licence)'),
        _r(A3, 'free', 'general authorisation'),
    ] + _gaps('limited', 'up to 50 mW') + [
        _r(A5, 'allowed'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('DK', 'Denmark', _EU, [
        _r(A2, 'no'),
        _r(A3, 'limited', 'only TV white spaces (TV channels not used locally)'),
        _r((695, 703), 'limited', 'only 695-703'),
        _r(A5, 'allowed'),
    ]),
    _country('EE', 'Estonia', _EU, [
        _r(A2, 'allowed', 'up to 50 mW'), _r(A3, 'allowed', 'up to 50 mW'),
    ] + _gaps('no') + [
        _r(A5, 'allowed'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('FI', 'Finland', _EU, [
        _r((174, 195), 'free', 'licence-exempt'), _r((216, 225), 'free', 'licence-exempt'),
        _r(A3, 'free', 'licence-exempt, up to 50 mW'),
    ] + _gaps('no') + [
        _r(A5, 'limited', 'only 823-832'),
        _r(A7, 'no'), _r((1519.2, 1524.8), 'limited', 'only 1519.2-1524.8'),
    ]),
    _country('FR', 'France', _EU, [
        _r(A2, 'allowed', 'up to 50 mW'), _r(A3, 'allowed', 'up to 50 mW'),
    ] + _gaps('no', 'closed for PMSE since 2019') + [
        _r(A5, 'limited', 'only 823-832'),
        _r(A7, 'limited', 'temporary licences'), _r(A8, 'no'),
    ]),
    _country('GR', 'Greece', _EU, [
        _r(A2, 'allowed', 'licence above 50 mW'),
        _r(A3, 'limited', 'licence above 50 mW, geographic limits'),
    ] + _gaps('limited', 'licence above 50 mW') + [
        _r((788, 790), 'limited', 'up to 12 mW'),
        _r(A5, 'allowed'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('HU', 'Hungary', _EU, [
        _r(A2, 'free', 'licence-exempt'), _r(A3, 'free', 'licence-exempt'),
    ] + _gaps('limited', 'licence-exempt') + [
        _r(A5, 'allowed'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('IE', 'Ireland', _EU, [
        _r(A2, 'licence', 'temporary licence (ComReg)'),
        _r(A3, 'licence', 'temporary licence, up to 50 mW'),
        _r((733, 753), 'licence', 'licence'),
        _r(A5, 'licence', 'licence, 823-832'),
        _r((1370, 1385), 'licence', 'only 1370-1385'), _r(A8, 'no'),
    ]),
    _country('IT', 'Italy', _EU, [
        _r((174, 223), 'allowed'), _r(A3, 'allowed'),
        _r((694, 698), 'limited', 'only 694-698'), _r((736, 738), 'limited', 'only 736-738'),
        _r(A5, 'limited', 'only 823-832'),
        _r(A7, 'no'), _r(A8, 'allowed'),
    ]),
    _country('LV', 'Latvia', _EU, [
        _r(A2, 'allowed'), _r(A3, 'allowed'),
    ] + _gaps('no') + [
        _r(A5, 'allowed'), _r(A7, 'allowed'), _r(A8, 'no'),
    ]),
    _country('LU', 'Luxembourg', _EU, [
        _r(A3, 'allowed'),
    ] + _gaps('no') + [
        _r(A5, 'allowed'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('MT', 'Malta', _EU, [
        _r(A2, 'limited', 'temporary licences'), _r(A3, 'limited'),
    ] + _gaps('no') + [
        _r(A5, 'limited'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('NL', 'Netherlands', _EU, [
        _r((195, 202), 'free', 'licence-exempt, up to 100 mW'),
        _r((470, 556), 'free', 'licence-exempt, up to 100 mW'),
        _r((556, 558), 'no', 'not in the exemption'),
        _r((558, 564), 'free', 'licence-exempt, up to 100 mW'),
        _r((564, 566), 'no', 'not in the exemption'),
        _r((566, 572), 'free', 'licence-exempt, up to 100 mW'),
        _r((572, 574), 'no', 'not in the exemption'),
        _r((574, 580), 'free', 'licence-exempt, up to 100 mW'),
        _r((580, 582), 'no', 'not in the exemption'),
        _r((582, 588), 'free', 'licence-exempt, up to 100 mW'),
        _r((588, 590), 'no', 'not in the exemption'),
        _r((590, 604), 'free', 'licence-exempt, up to 100 mW'),
        _r((604, 608), 'no', 'not in the exemption'),
        _r((608, 614), 'licence', 'individual licence'),
        _r((614, 694), 'free', 'licence-exempt, up to 100 mW'),
        _r((736, 753), 'free', 'licence-exempt, up to 50 mW'),
        _r((821.5, 832), 'allowed'),
        _r(A7, 'licence'), _r(A8, 'licence'),
    ]),
    _country('PT', 'Portugal', _EU, [
        _r((174, 223), 'allowed', 'licence above 50 mW'),
        _r(A3, 'allowed'),
        _r((694, 703), 'limited'), _r((733, 750), 'limited'),
        _r((750, 758), 'no', '750-758 not allowed'),
        _r(A5, 'allowed'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('RO', 'Romania', _EU, [
        _r((29.7, 30.3), 'free', '10 mW'), _r((30.5, 32.15), 'free', '10 mW'),
        _r((32.45, 33.1), 'free', '10 mW'), _r((33.6, 34.975), 'free', '10 mW'),
        _r((37.5, 40.02), 'free', '10 mW'), _r((40.66, 41.015), 'free', '10 mW'),
        _r((44.5, 45.2), 'free', '10 mW'),
        _r(A2, 'free', '50 mW; near TV transmitters agree with ANCOM'),
        _r((470, 574), 'free', '50 mW; near TV transmitters agree with ANCOM'),
        _r((574, 582), 'licence', 'licensed sub-band'),
        _r((582, 694), 'free', '50 mW; near TV transmitters agree with ANCOM'),
        _r((821.5, 826), 'free', '100 mW body-worn / 20 mW other'),
        _r((826, 832), 'free', '100 mW'),
    ], source='ANCOM RO-IR MICRO edition 5/2025 (22.12.2025)', regulator='ANCOM',
        url='https://www.ancom.ro'),
    _country('SK', 'Slovakia', _EU, [
        _r(A2, 'licence', 'individual licence'), _r(A3, 'licence', 'individual licence'),
    ] + _gaps('licence', 'individual licence') + [
        _r(A5, 'free', 'general authorisation'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('SI', 'Slovenia', _EU, [
        _r(A2, 'allowed'), _r(A3, 'allowed'),
        _r((694, 698), 'limited', 'only 694-698'), _r((736, 738), 'limited', 'only 736-738'),
        _r(A5, 'allowed'), _r(A7, 'allowed'), _r(A8, 'limited', 'conditions not defined yet'),
    ]),
    _country('ES', 'Spain', _EU, [
        _r(A2, 'no', 'only 5 single channels'),
        _r((174.0, 174.2), 'limited', 'channel 174.1 MHz'), _r((174.2, 174.4), 'limited', 'channel 174.3 MHz'),
        _r((175.4, 175.6), 'limited', 'channel 175.5 MHz'), _r((176.2, 176.4), 'limited', 'channel 176.3 MHz'),
        _r((179.2, 179.4), 'limited', 'channel 179.3 MHz'),
        _r(A3, 'allowed', 'TV white spaces, up to 50 mW'),
    ] + _gaps('no') + [
        _r(A5, 'allowed'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('SE', 'Sweden', _EU, [
        _r(A2, 'allowed'), _r(A3, 'licence', 'licensed TV white spaces (PTS)'),
    ] + _gaps('no') + [
        _r(A5, 'free', 'licence-exempt'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('NO', 'Norway', _EEA, [
        _r(A2, 'no'),
        _r((470, 494), 'unknown', 'only 494-694 is licence-free'),
        _r((494, 694), 'free', 'licence-free, up to 50 mW'),
        _r((733, 758), 'limited', 'up to 50 mW'),
        _r(A5, 'limited', 'only 823-832'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('IS', 'Iceland', _EEA, [
        _r(A2, 'allowed', 'secondary basis'), _r(A3, 'allowed', 'TV channels not in use'),
    ] + _gaps('no') + [
        _r(A5, 'allowed'), _r(A7, 'no'), _r(A8, 'no'),
    ]),
    _country('CH', 'Switzerland', _EEA, [
        _r((174, 223), 'allowed', 'up to 50 mW'),
        _r(A3, 'allowed', 'up to 50 mW; 608-614 not near Bleien (radio astronomy)'),
    ] + _gaps('no') + [
        _r((821.5, 832), 'allowed'), _r(A7, 'limited', 'indoor only'), _r(A8, 'no'),
    ]),
    _country('LI', 'Liechtenstein', _EEA, [
        _r((174, 223), 'allowed', 'up to 50 mW'), _r(A3, 'allowed', 'up to 50 mW'),
    ] + _gaps('no') + [
        _r((821.5, 832), 'allowed'), _r(A7, 'limited', 'indoor only'), _r(A8, 'no'),
    ]),
    _country('GB', 'United Kingdom', _OTHER, [
        _r(A2, 'no', 'only designated channels'),
        _r((175.15, 175.35), 'licence', 'designated channel'),
        _r((191.6, 193.1), 'licence', 'audio links'),
        _r((207.6, 210.1), 'licence', 'designated channels'),
        _r(A3, 'licence', 'Ofcom PMSE licence (shared)'),
        _r((606, 614), 'licence', 'channel 38: UK-wide Ofcom licence'),
        _r(A5, 'limited', 'general licence, 823-832'),
        _r(A7, 'no'), _r(A8, 'licence'),
    ] + _gaps('no'), regulator='Ofcom', url='https://www.ofcom.org.uk'),
    _country('UA', 'Ukraine', _OTHER, [
        _r(A2, 'licence', 'frequency assignment (NKEK, DB-5), no interference to others'),
        _r((470, 789), 'licence', 'frequency assignment (NKEK, DB-5), no interference to others'),
        _r(A5, 'licence', 'frequency assignment (NKEK, DB-5)'),
        _r(A6, 'free', 'general authorisation (ZD-7); not within 3 km of airports'),
    ], source='NKEK resolution No 361 of 03.07.2024', regulator='NKEK',
        url='https://nkek.gov.ua', mobile=False, srd=False,
        note='700 MHz band still used for TV; mobile bands not marked'),
    _country('RS', 'Serbia', _OTHER, [
        _r(A3, 'allowed', 'CEPT table: 470-786 possible'),
        _r((694, 790), 'unknown', 'CEPT table: possible up to 786; the 700 MHz band may be '
                                  'given to mobile networks - check with RATEL'),
    ], regulator='RATEL', url='https://www.ratel.rs', mobile=False, srd=False,
        note='only partial data in the CEPT table'),
    _country('BY', 'Belarus', _OTHER, [], source='no reliable public source found',
             mobile=False, srd=False,
             note='Belarus is not in the CEPT table; check with the State Commission on Radio Frequencies'),
]

COUNTRIES = dict((c['code'], c) for c in _C)
COUNTRY_ORDER = [c['code'] for c in sorted(_C, key=lambda c: ({_EU: 0, _EEA: 1}.get(c['group'], 2), c['name']))]


# ------------------------------------------------------------------ helpers

def merge_ranges(ranges, join_gap=0.0):
    """Sorted, merged [(lo, hi)]; ranges closer than ``join_gap`` MHz become one."""
    rs = sorted((float(min(a, b)), float(max(a, b))) for a, b in ranges if a is not None and b is not None)
    out = []
    for lo, hi in rs:
        if hi <= lo:
            continue
        if out and lo <= out[-1][1] + join_gap:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def scan_segments(ranges, join_gap=2.0, lo_limit=DONGLE_MIN_MHZ, hi_limit=DONGLE_MAX_MHZ):
    """Ranges to scan: merged, clipped to what the dongle reaches.
    Returns (segments, outside) - outside: parts the dongle can not see."""
    segs, outside = [], []
    for lo, hi in merge_ranges(ranges, join_gap):
        if hi <= lo_limit or lo >= hi_limit:
            outside.append((lo, hi))
            continue
        if lo < lo_limit:
            outside.append((lo, lo_limit))
        if hi > hi_limit:
            outside.append((hi_limit, hi))
        segs.append((max(lo, lo_limit), min(hi, hi_limit)))
    return segs, outside


def estimate_seconds(segments, step_mhz=0.775, tile_s=0.078):
    """Rough scan time: tiles x time per tile (V16 timing, 2.4 MS/s)."""
    n = 0
    for lo, hi in segments:
        n += int(math.floor((hi - lo) / step_mhz)) + 1
    return n * tile_s


def regions(code, lo=DONGLE_MIN_MHZ, hi=DONGLE_MAX_MHZ):
    """What the country allows from ``lo`` to ``hi``: a list of
    dict(lo, hi, status, note), one after another without holes (places
    without a rule are 'unknown'). A later rule in the table wins."""
    c = COUNTRIES.get(code)
    rules = c['rules'] if c else []
    pts = set([float(lo), float(hi)])
    for a, b, s, n in rules:
        if b > lo and a < hi:
            pts.add(max(a, lo))
            pts.add(min(b, hi))
    pts = sorted(pts)
    out = []
    for a, b in zip(pts[:-1], pts[1:]):
        if b - a < 1e-9:
            continue
        mid = (a + b) / 2.0
        st, note = 'unknown', ''
        for ra, rb, s, n in rules:
            if ra <= mid < rb:
                st, note = s, n
        if out and out[-1]['status'] == st and out[-1]['note'] == note and abs(out[-1]['hi'] - a) < 1e-9:
            out[-1]['hi'] = b
        else:
            out.append(dict(lo=a, hi=b, status=st, note=note))
    return out


def worst(statuses):
    st = [s for s in statuses if s in SEVERITY]
    return max(st, key=SEVERITY.index) if st else 'unknown'


def check(code, ranges):
    """For each range: (lo, hi, [regions inside], worst status)."""
    out = []
    for lo, hi in ranges:
        if lo >= DONGLE_MAX_MHZ:
            out.append((lo, hi, [], 'unknown'))
            continue
        parts = regions(code, lo, hi)
        out.append((lo, hi, parts, worst(p['status'] for p in parts)))
    return out


def fmt_mhz(x):
    return ('%.3f' % x).rstrip('0').rstrip('.')


def describe(code, ranges):
    """Short lines of text for the window: per range, the parts and the rule."""
    lines = []
    for lo, hi, parts, w in check(code, ranges):
        if not parts:
            lines.append('%s-%s MHz: above %d MHz, the RTL-SDR can not scan it'
                         % (fmt_mhz(lo), fmt_mhz(hi), DONGLE_MAX_MHZ))
            continue
        for p in parts:
            lab = STATUS[p['status']][0]
            lines.append('%s-%s MHz: %s%s' % (fmt_mhz(p['lo']), fmt_mhz(p['hi']), lab,
                                              (' - ' + p['note']) if p['note'] else ''))
    return lines


def system_summary(code, sys_id):
    """dict(status, lines) for one system band in one country."""
    s = SYSTEMS_BY_ID[sys_id]
    res = check(code, s['ranges'])
    st = worst(r[3] for r in res)
    return dict(status=st, lines=describe(code, s['ranges']), note=s['note'])


def selection(sys_ids, extra_ranges=()):
    """All ranges of the chosen system bands (+ own ranges), merged."""
    rs = []
    for i in sys_ids:
        s = SYSTEMS_BY_ID.get(i)
        if s:
            rs += s['ranges']
    rs += [tuple(r) for r in extra_ranges]
    return merge_ranges(rs)


def parse_ranges(value):
    """[(lo, hi)] from a list or a JSON text such as '[[470, 534], [823, 832]]'.
    Anything wrong gives []: the scan then uses its single range."""
    import json
    if value is None or value == '':
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    out = []
    try:
        for r in value:
            lo, hi = float(r[0]), float(r[1])
            if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
                out.append((lo, hi))
    except (TypeError, ValueError, IndexError):
        return []
    return out


def countries_list():
    return [dict(code=k, name=COUNTRIES[k]['name'], group=COUNTRIES[k]['group'],
                 source=COUNTRIES[k]['source'], regulator=COUNTRIES[k]['regulator'] or '',
                 url=COUNTRIES[k]['url'] or '', note=COUNTRIES[k]['note'])
            for k in COUNTRY_ORDER]
