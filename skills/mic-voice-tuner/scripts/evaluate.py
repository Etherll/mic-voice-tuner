#!/usr/bin/env python3
"""Measure processed captures against the raw recordings and draw the before/after mel spectrogram.

  # captures of the SAME audio pushed through the pipeline (ee_inject.py, or Equalizer APO's Benchmark.exe)
  python evaluate.py --rec rec --work work --capture before=work/cap_before.wav --capture "NR on=work/cap_nr_on.wav"
  # or a fresh take recorded THROUGH the processed mic with record.py (no alignment possible, still valid)
  python evaluate.py --rec rec --work work --respoken "after=rec_after"

Writes work/eval.json and work/mel_compare.png and prints a table. DNSMOS/NISQA rows are model estimates of
quality (1-5), not listening tests; say so when reporting them.
"""
import argparse, os, sys, json
import numpy as np, scipy.signal as ss
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from micdsp import *  # noqa


def measure(y, segs, masks):
    R = {}
    s = seg(y, segs, '01_silence')[3 * SR:]
    R['noise in silence (dBFS)'] = round(float(db(rms(s))), 1)
    for n in SPEECH:
        if n in segs:
            R[f'{n[3:]} loudness (LUFS)'] = round(lufs(seg(y, segs, n)), 1)
    if '03_loud' in segs and '04_quiet' in segs:
        R['quiet-to-loud spread (dB)'] = round(R['loud loudness (LUFS)'] - R['quiet loudness (LUFS)'], 1)
    sp = np.concatenate([seg(y, segs, n) for n in SPEECH if n in segs])
    R['speech peak (dBFS)'] = round(float(db(np.abs(sp).max())), 1); R['true peak (dBTP)'] = round(true_peak_db(sp), 1)
    R['clipped samples'] = int((np.abs(y) >= 0.999).sum())
    if '06_keyboard' in segs:
        R['keyboard peak below speech peak (dB)'] = round(R['speech peak (dBFS)'] - float(db(np.abs(seg(y, segs, '06_keyboard')).max())), 1)
    q = quality_avg(y, segs, None)
    if q:
        R['DNSMOS overall (est.)'] = q['dnsmos_ovrl']; R['DNSMOS speech (est.)'] = q['dnsmos_sig']; R['DNSMOS background (est.)'] = q['dnsmos_bak']
        R['NISQA MOS (est.)'] = q['nisqa_mos']
        e = quality_avg(y, segs)
        R['DNSMOS overall at equal loudness (est.)'] = e['dnsmos_ovrl']; R['NISQA MOS at equal loudness (est.)'] = e['nisqa_mos']
    return R


def mel_figure(rows, path):
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, librosa, librosa.display
    fig, axs = plt.subplots(len(rows), 1, figsize=(18, 3.2 * len(rows)))
    for ax, (title, v, segs) in zip(np.atleast_1d(axs), rows):
        M = librosa.feature.melspectrogram(y=v.astype(np.float32), sr=SR, n_fft=2048, hop_length=512, n_mels=128, fmin=20, fmax=24000)
        im = librosa.display.specshow(librosa.power_to_db(M, ref=1.0, top_db=None, amin=1e-14), sr=SR, hop_length=512, x_axis='time', y_axis='mel',
                                      fmin=20, fmax=24000, ax=ax, vmin=-110, vmax=0, cmap='magma')
        ax.set_title(title, fontsize=11, loc='left'); ax.set_xlabel(''); fig.colorbar(im, ax=ax, pad=0.01, label='dB')
        for n, (a, b) in segs.items():
            ax.axvline(a / SR, color='w', lw=.6, alpha=.6); ax.text(a / SR + 0.3, 15000, n[3:], color='w', fontsize=8, va='top')
    plt.tight_layout(); plt.savefig(path, dpi=80); plt.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rec', required=True); ap.add_argument('--work', default='work')
    ap.add_argument('--capture', action='append', default=[], metavar='LABEL=WAV'); ap.add_argument('--respoken', action='append', default=[], metavar='LABEL=DIR')
    ap.add_argument('--keep-aligned', action='store_true', help='save aligned captures as work/aligned_<label>.wav for listening')
    a = ap.parse_args()
    x, segs = load_all(a.rec); noise = seg(x, segs, '01_silence')
    masks = {n: vad_mask(seg(x, segs, n), noise) for n in SPEECH if n in segs}
    res = {'raw mic': measure(x, segs, masks)}; rows = [('raw mic', x, segs)]
    for item in a.capture:
        label, path = item.split('=', 1); y, lag = align(x, read_mono(path))
        a0, b0 = segs['02_normal']; c = float(np.corrcoef(np.abs(ss.hilbert(x[a0:b0]))[::480], np.abs(ss.hilbert(y[a0:b0]))[::480])[0, 1])
        if c < 0.3:
            print(f'WARNING {label}: capture does not track the input (envelope correlation {c:.2f}). The injection probably failed; do not trust this column.')
        res[label] = measure(y, segs, masks); res[label]['delay incl. capture start (s)'] = round(lag / SR, 2); rows.append((label, y, segs))
        if a.keep_aligned:
            import soundfile as sf; sf.write(os.path.join(a.work, 'aligned_' + label.replace(' ', '_') + '.wav'), y, SR, subtype='FLOAT')
    for item in a.respoken:
        label, d = item.split('=', 1); y, s2 = load_all(d)
        res[label + ' (new take)'] = measure(y, s2, None); rows.append((label + ' (new take)', y, s2))
    jdump(res, os.path.join(a.work, 'eval.json')); mel_figure(rows, os.path.join(a.work, 'mel_compare.png'))
    keys = []
    for r in res.values():
        keys += [k for k in r if k not in keys]
    labels = list(res); w = max(len(k) for k in keys) + 2
    print(' ' * w + ''.join(f'{l[:16]:>18s}' for l in labels))
    for k in keys:
        print(f'{k:<{w}s}' + ''.join(f"{str(res[l].get(k, '')):>18s}" for l in labels))
    print('wrote', os.path.join(a.work, 'eval.json'), 'and', os.path.join(a.work, 'mel_compare.png'))


if __name__ == '__main__':
    main()
