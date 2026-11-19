#!/usr/bin/env python3
"""Deterministic synthetic 'recordings' for testing the pipeline without anyone's real voice.

  python make_synth_rec.py rec

A buzz source through formant filters stands in for speech, with fricative noise bursts, 50 Hz hum, a fan tone,
broadband hiss and key clicks. It is not intelligible, so noise-removal models and DNSMOS/NISQA scores are
meaningless on it, but levels, spectra, pitch, the gate, the compressor and the config writers all behave normally.
"""
import os, sys
import numpy as np, scipy.signal as ss, soundfile as sf

SR = 48000
rng = np.random.default_rng(7)


def voice(dur, level_db, f0=118.0):
    n = int(dur * SR); t = np.arange(n) / SR
    f = f0 * (1 + 0.06 * np.sin(2 * np.pi * 0.7 * t) + 0.02 * np.sin(2 * np.pi * 5.1 * t))
    src = ss.sawtooth(2 * np.pi * np.cumsum(f) / SR)
    y = np.zeros(n)
    for fc, bw, g in ((620, 90, 1.0), (1250, 120, 0.45), (2600, 180, 0.22), (3400, 250, 0.1)):
        b, a = ss.iirpeak(fc, fc / bw, fs=SR); y += g * ss.lfilter(b, a, src)
    env = np.zeros(n); pos = int(0.2 * SR)                          # syllables separated by short gaps
    while pos < n:
        L = int(rng.uniform(0.12, 0.35) * SR); e = np.hanning(L) ** 0.5 * rng.uniform(0.5, 1.0)
        env[pos:pos + L] = e[:max(0, min(L, n - pos))]; pos += L + int(rng.uniform(0.03, 0.4) * SR)
    y *= env
    hiss = ss.sosfilt(ss.butter(4, [4500, 9000], 'band', fs=SR, output='sos'), rng.standard_normal(n))
    senv = np.zeros(n); pos = int(0.5 * SR)                         # "s" bursts
    while pos < n:
        L = int(rng.uniform(0.06, 0.14) * SR); senv[pos:pos + L] = np.hanning(L)[:max(0, min(L, n - pos))]; pos += int(rng.uniform(0.5, 1.4) * SR)
    y = y / (np.sqrt(np.mean(y[env > 0.3] ** 2)) + 1e-9) + 0.35 * hiss * senv / np.sqrt(np.mean(hiss ** 2))
    y = ss.sosfilt(ss.butter(2, 4200, 'low', fs=SR, output='sos'), y) * 0.8 + 0.2 * y      # a dull microphone
    return y * 10 ** (level_db / 20)


def room(dur):
    n = int(dur * SR); t = np.arange(n) / SR
    hum = 10 ** (-66 / 20) * np.sin(2 * np.pi * 50 * t) + 10 ** (-76 / 20) * np.sin(2 * np.pi * 100 * t)
    fan = 10 ** (-72 / 20) * np.sin(2 * np.pi * 187.0 * t)
    low = ss.sosfilt(ss.butter(2, 300, 'low', fs=SR, output='sos'), rng.standard_normal(n)) * 10 ** (-58 / 20)
    return hum + fan + low + rng.standard_normal(n) * 10 ** (-84 / 20)


def keys(dur):
    n = int(dur * SR); y = np.zeros(n); pos = int(0.4 * SR)
    while pos < n - 2000:
        L = 900; click = rng.standard_normal(L) * np.exp(-np.arange(L) / 120.0)
        thump = np.sin(2 * np.pi * 85 * np.arange(L * 3) / SR) * np.exp(-np.arange(L * 3) / 900.0)
        y[pos:pos + L] += 10 ** (-46 / 20) * click; y[pos:pos + L * 3] += 10 ** (-50 / 20) * thump[:max(0, min(L * 3, n - pos))]
        pos += int(rng.uniform(0.12, 0.5) * SR)
    return y


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'rec'; os.makedirs(out, exist_ok=True)
    stages = {'01_silence': (10, None, False), '02_normal': (16, -34, False), '03_loud': (8, -26, False), '04_quiet': (8, -42, False),
              '05_sibilance': (8, -36, False), '06_keyboard': (8, None, True), '07_speech_typing': (8, -36, True)}
    for name, (dur, lvl, kb) in stages.items():
        y = room(dur)
        if lvl is not None: y += voice(dur, lvl)
        if kb: y += keys(dur)
        sf.write(os.path.join(out, name + '.wav'), y, SR, subtype='PCM_16')
    print('wrote synthetic recordings to', out)


if __name__ == '__main__':
    main()
