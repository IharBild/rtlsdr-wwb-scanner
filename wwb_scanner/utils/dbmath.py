import numpy as np

REF_DB = 1.#1e-4

# Floor for the argument of log10: log10(0) would give -inf (and negative
# values nan). It is the smallest positive float, so no real value is changed.
MIN_VALUE = np.finfo(np.float64).tiny

def amplitude_to_dB(a, ref=REF_DB):
    return 20 * np.log10(np.maximum(a / ref, MIN_VALUE))

def dB_to_amplitude(dB, ref=REF_DB):
    return 10 ** (dB/20.) * ref

def power_to_dB(p, ref=REF_DB):
    return 10 * np.log10(np.maximum(p / ref, MIN_VALUE))

def dB_to_power(dB, ref=REF_DB):
    return 10 ** (dB/10.) * ref

def to_dB(p, ref=REF_DB):
    return power_to_dB(p, ref)

def from_dB(dB, ref=REF_DB):
    return dB_to_power(dB, ref)
