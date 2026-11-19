#!/usr/bin/env python3
"""Measure the raw recordings: levels, noise floor and tones, speech spectrum, pitch, sibilance, keyboard.

  python analyze.py --rec rec --out work

Writes work/analysis.json, work/mel_raw.png, work/spectra_raw.png and prints a summary with findings.
"""
import argparse, os, sys, json
import numpy as np, scipy.signal as ss
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from micdsp import *  # noqa


def tones(v, lo=40, hi=8000, prom=12):
    f, P = ss.welch(v, SR, nperseg=min(65536, len(v) // 2 * 2), scaling='spectrum')
    Pd = 10 * np.log10(P + 1e-20); med = ss.medfilt(Pd, 201)
    pk, _ = ss.find_peaks(Pd - med, height=prom, distance=4)
    return [(float(f[i]), float(Pd[i]), float(Pd[i] - med[i])) for i in pk if lo < f[i] < hi]


def stable_tones(noise):
    """Tones present in every third of the silence stage; wander tells whether a narrow notch can track them."""
    n = len(noise) // 3
    wins = [tones(noise[i * n:(i + 1) * n]) for i in range(3)]
    out = []
    for f0, lvl, pr in tones(noise):
        hits = [[t for t in w if abs(t[0] - f0) <= max(2.5, 0.02 * f0)] for w in wins]
        fs = [h[0][0] for h in hits if h]
        out.append(dict(f=round(f0, 1), level=round(lvl, 1), prominence=round(pr, 1), stable=len(fs) == 3,
                        wander_hz=round(max(fs) - min(fs), 1) if fs else None))
    return sorted(out, key=lambda t: -t['level'])[:20]


def sibilance(v):
    f, t, S = ss.spectrogram(v, SR, nperseg=1024, noverlap=768, scaling='spectrum', mode='psd')
    tot = S.sum(0); hf = S[(f >= 4000) & (f < 12000)].sum(0); lf = S[(f >= 100) & (f < 3000)].sum(0)
    loud = tot > np.percentile(tot, 40); sib = loud & (hf > 2 * lf); voi = loud & (lf > 4 * hf)
    if sib.sum() < 5 or voi.sum() < 5:
        return None
    L = 10 * np.log10(tot + 1e-20)
    return dict(sib_frac=round(float(sib.mean()), 3), sib_p95=round(float(np.percentile(L[sib], 95)), 1),
                voiced_p95=round(float(np.percentile(L[voi], 95)), 1),
                sib_minus_voiced=round(float(np.percentile(L[sib], 95) - np.percentile(L[voi], 95)), 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rec', required=True); ap.add_argument('--out', default='work'); ap.add_argument('--no-plots', action='store_true')
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    x, segs = load_all(a.rec); noise = seg(x, segs, '01_silence')
    R = dict(stages={}, missing_stages=[n for n in STAGES if n not in segs])
    nf = float(np.median(frames_rms(noise)))
    for n in segs:
        v = seg(x, segs, n); fr = frames_rms(v)
        d = dict(dur=round(len(v) / SR, 1), peak=round(float(db(np.abs(v).max())), 1), rms=round(float(db(rms(v))), 1),
                 rms_A=round(float(db(rms(aweight(v)))), 1), lufs=round(lufs(v), 1), clipped=int((np.abs(v) > 0.999).sum()))
        if n in SPEECH:
            m = vad_mask(v, noise)[:len(fr)]; fr = fr[:len(m)]
            if m.sum() > 10:
                act = float(db(np.sqrt(np.mean(fr[m] ** 2))))
                d.update(active_frac=round(float(m.mean()), 2), active_level=round(act, 1), snr_db=round(act - float(db(nf)), 1),
                         crest=round(d['peak'] - act, 1), active_pct={p: round(float(db(np.percentile(fr[m], p))), 1) for p in (5, 25, 50, 75, 95)})
            d['third_oct'] = [round(float(t), 1) for t in third_octave(v)]
        R['stages'][n] = d
    fr = frames_rms(noise)
    bands = [(20, 80), (80, 200), (200, 1000), (1000, 4000), (4000, 8000), (8000, 16000)]
    f, P = ss.welch(noise, SR, nperseg=16384, scaling='spectrum')
    R['noise'] = dict(floor_db=round(float(db(nf)), 1), floor_p99=round(float(db(np.percentile(fr, 99))), 1),
                      floor_A_db=round(float(db(rms(aweight(noise)))), 1), tones=stable_tones(noise),
                      third_oct=[round(float(t), 1) for t in third_octave(noise, loud_only=False)],
                      bands={f'{lo}-{hi}': round(float(10 * np.log10(P[(f >= lo) & (f < hi)].sum() + 1e-20)), 1) for lo, hi in bands})
    try:
        import librosa
        f0, vf, _ = librosa.pyin(seg(x, segs, '02_normal').astype(np.float32), fmin=60, fmax=400, sr=SR, frame_length=4096, hop_length=512)
        f0v = f0[vf & ~np.isnan(f0)]
        R['f0'] = {f'p{p}': round(float(np.percentile(f0v, p)), 1) for p in (5, 25, 50, 75, 95)} if len(f0v) > 20 else None
    except Exception as e:
        R['f0'] = None; print('pitch tracking skipped:', e)
    R['sibilance'] = {n: sibilance(seg(x, segs, n)) for n in ('02_normal', '05_sibilance') if n in segs}
    if '06_keyboard' in segs:
        k = seg(x, segs, '06_keyboard'); frk = frames_rms(k, 0.005, 0.0025)
        R['keyboard'] = dict(peak=round(float(db(np.abs(k).max())), 1), p99=round(float(db(np.percentile(frk, 99))), 1))
    # speech spectrum vs the long-term average speech spectrum (levels relative to the 400-630 Hz region)
    sp = np.mean([np.array(R['stages'][n]['third_oct']) for n in SPEECH if n in segs], axis=0)[2:]
    R['spectrum'] = dict(fc=FC3[2:].tolist(), speech_rel=[round(float(v), 1) for v in sp - sp[6:9].mean()],
                         ltass_rel=[round(float(v), 1) for v in LTASS - LTASS[6:9].mean()],
                         deficit=[round(float(v), 1) for v in (sp - sp[6:9].mean()) - (LTASS - LTASS[6:9].mean())])
    # findings: plain-language problems worth telling the user about before any DSP
    F = []; nrm = R['stages']['02_normal']
    act = nrm.get('active_level')
    if act is not None and act < -35:
        F.append(f'Voice is very quiet at the converter ({act} dBFS active). Fix physically first if possible: get closer and on-axis '
                 '(each halving of distance is about +6 dB of voice with no extra noise) or raise the hardware gain.')
    worst_peak = max(d['peak'] for d in R['stages'].values())
    if worst_peak > -1.0 or any(d['clipped'] for d in R['stages'].values()):
        F.append(f'Clipping or near-clipping at the converter (peak {worst_peak} dBFS). Lower the hardware gain; no plugin can undo clipping.')
    if nrm.get('snr_db') is not None and nrm['snr_db'] < 25:
        F.append(f"Raw signal-to-noise is only {nrm['snr_db']} dB, so a chain without AI noise removal will stay audibly noisy once levelled.")
    hum = [t for t in R['noise']['tones'] if t['stable'] and min(abs(t['f'] / b - round(t['f'] / b)) * b for b in (50, 60)) < 1.5 and t['f'] < 400]
    if hum:
        F.append('Mains hum present at ' + ', '.join(f"{t['f']} Hz ({t['level']} dB)" for t in hum[:4]) + '.')
    other = [t for t in R['noise']['tones'] if t not in hum and t['prominence'] > 15 and 80 < t['f'] < 2000][:4]
    if other:
        F.append('Other tonal noise (fans, coil whine): ' + ', '.join(f"{t['f']} Hz" + ('' if t['stable'] else ' (comes and goes)') for t in other) + '.')
    hf = float(np.mean(R['spectrum']['deficit'][15:20]))   # 5-12.5 kHz
    if hf < -8:
        F.append(f'Highs are {abs(round(hf))} dB below a typical voice above 5 kHz: dull mic, off-axis talking or distance. EQ can lift part of it.')
    if hf > 6:
        F.append(f'Highs are {round(hf)} dB above a typical voice above 5 kHz: bright/harsh mic; expect a high-shelf cut.')
    R['findings'] = F
    jdump(R, os.path.join(a.out, 'analysis.json'))
    if not a.no_plots:
        plots(x, segs, R, a.out)
    print(f"noise floor {R['noise']['floor_db']} dBFS ({R['noise']['floor_A_db']} dBA-weighted)")
    for n, d in R['stages'].items():
        print(f"  {n:18s} peak {d['peak']:6.1f}  LUFS {d['lufs']:6.1f}" + (f"  active {d['active_level']:6.1f}  SNR {d['snr_db']:5.1f} dB" if 'active_level' in d else ''))
    print('pitch (Hz):', R['f0']); print('findings:'); [print('  -', s) for s in F] if F else print('  - none')
    print('wrote', os.path.join(a.out, 'analysis.json'))


def plots(x, segs, R, out):
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, librosa, librosa.display
    names = list(segs); fig, axs = plt.subplots(len(names), 1, figsize=(16, 2.5 * len(names)))
    for ax, n in zip(np.atleast_1d(axs), names):
        M = librosa.feature.melspectrogram(y=seg(x, segs, n).astype(np.float32), sr=SR, n_fft=2048, hop_length=256, n_mels=128, fmin=20, fmax=24000)
        im = librosa.display.specshow(librosa.power_to_db(M, ref=1.0, top_db=None, amin=1e-14), sr=SR, hop_length=256, x_axis='time', y_axis='mel',
                                      fmin=20, fmax=24000, ax=ax, vmin=-120, vmax=-20, cmap='magma')
        d = R['stages'][n]; ax.set_title(f"{n}  peak {d['peak']} dBFS  LUFS {d['lufs']}", fontsize=9); ax.set_xlabel(''); fig.colorbar(im, ax=ax, pad=0.01)
    plt.tight_layout(); plt.savefig(os.path.join(out, 'mel_raw.png'), dpi=70); plt.close()
    fig, ax = plt.subplots(1, 2, figsize=(16, 5))
    f, P = ss.welch(seg(x, segs, '01_silence'), SR, nperseg=65536, scaling='spectrum')
    ax[0].semilogx(f, 10 * np.log10(P + 1e-20), lw=.5); ax[0].set_xlim(20, 24000); ax[0].set_title('silence spectrum (dB)'); ax[0].grid(True, which='both', alpha=.3)
    for n in names:
        t = R['stages'][n].get('third_oct') or (R['noise']['third_oct'] if n == '01_silence' else None)
        if t: ax[1].semilogx(FC3, t, marker='o', ms=3, label=n)
    ax[1].legend(); ax[1].grid(True, which='both', alpha=.3); ax[1].set_title('1/3-octave levels (dBFS)'); ax[1].set_xlim(50, 20000)
    plt.tight_layout(); plt.savefig(os.path.join(out, 'spectra_raw.png'), dpi=70); plt.close()


if __name__ == '__main__':
    main()
