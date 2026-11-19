#!/usr/bin/env python3
"""Benchmark the noise-removal models available on this Linux machine against the user's own recordings.

  python bench_nr.py --rec rec --out work

Candidates (each is skipped if not installed):
  dpdfnet     libdpdfnet_ladspa.so  (HushMic; Easy Effects >= 8.3 "Deep Noise Remover", model DPDFNet)
  deepfilter  libdeep_filter_ladspa.so (DeepFilterNet 3; Easy Effects "Deep Noise Remover")
  rnnoise     librnnoise.so         (Easy Effects "Noise Reduction")

The LADSPA plugins are run through ffmpeg IN REAL TIME (-re). They process on a worker thread, and when
fed faster than real time their output arrives with unpredictable delay, which silently corrupts every
metric. Each run therefore takes as long as the recordings (about 2 minutes); runs go in parallel.

Writes work/nr/<name>.wav (aligned to the raw timeline), work/nr_bench.json and work/nr/best.wav.
"""
import argparse, os, sys, glob, ctypes, shutil, subprocess
import numpy as np, soundfile as sf, scipy.signal as ss
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from micdsp import *  # noqa


def find_ladspa(name):
    dirs = (os.environ.get('LADSPA_PATH', '').split(':') if os.environ.get('LADSPA_PATH') else []) + \
        ['/usr/lib/ladspa', '/usr/lib64/ladspa', '/usr/local/lib/ladspa', '/usr/lib/x86_64-linux-gnu/ladspa', os.path.expanduser('~/.ladspa')]
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p


def find_rnnoise():
    for pat in ('/usr/lib/librnnoise.so*', '/usr/lib64/librnnoise.so*', '/usr/lib/x86_64-linux-gnu/librnnoise.so*', '/usr/local/lib/librnnoise.so*'):
        g = sorted(glob.glob(pat))
        if g:
            return g[0]


def run_ladspa(inp, out, lib, label, controls, pregain, timeout):
    af = (f'volume={pregain}dB,' if pregain else '') + f'ladspa=f={lib}:p={label}:c=' + '|'.join(f'c{i}={v}' for i, v in enumerate(controls))
    if pregain:
        af += f',volume={-pregain}dB'
    r = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-re', '-i', inp, '-af', af, '-c:a', 'pcm_f32le', out],
                       capture_output=True, text=True, timeout=timeout)   # these plugins occasionally hang when several start at once
    if not os.path.exists(out) or os.path.getsize(out) < 1000:
        raise RuntimeError(r.stderr[-400:])
    return read_mono(out)


def run_rnnoise(x, lib, pregain):
    L = ctypes.CDLL(lib); L.rnnoise_create.restype = ctypes.c_void_p; L.rnnoise_create.argtypes = [ctypes.c_void_p]
    fp = ctypes.POINTER(ctypes.c_float)
    L.rnnoise_process_frame.restype = ctypes.c_float; L.rnnoise_process_frame.argtypes = [ctypes.c_void_p, fp, fp]
    st = L.rnnoise_create(None); g = 10 ** (pregain / 20); n = len(x) // 480
    xi = (x[:n * 480] * 32768 * g).astype(np.float32).reshape(n, 480); out = np.zeros_like(xi)
    for i in range(n):
        L.rnnoise_process_frame(st, out[i].ctypes.data_as(fp), xi[i].ctypes.data_as(fp))
    y = np.zeros(len(x)); y[:n * 480] = out.reshape(-1) / 32768 / g
    return y


def metrics(x, y, segs, noise, masks, f, Npsd, RAW):
    R = {}
    s = seg(y, segs, '01_silence')[3 * SR:]; r = noise[3 * SR:]          # skip 3 s of model adaptation
    R['silence_nr_db'] = round(float(db(rms(r)) - db(rms(s))), 1); R['silence_resid_db'] = round(float(db(rms(s))), 1)
    if '06_keyboard' in segs:
        R['keyboard_peak_db'] = round(float(db(np.abs(seg(y, segs, '06_keyboard')).max())), 1)
    lsd, att, hf = [], [], []
    for n in masks:
        P = np.abs(ss.stft(seg(y, segs, n), SR, nperseg=1024, noverlap=768)[2]) ** 2; Rw = RAW[n]
        sel = Rw > (Npsd[:, None] * 100); sel[:4] = False                 # bins where raw speech is >= 20 dB above noise
        if sel.sum() < 50:
            continue
        d = 10 * np.log10(P[sel] + 1e-20) - 10 * np.log10(Rw[sel])
        lsd.append(float(np.sqrt(np.mean(d ** 2)))); att.append(float(10 * np.log10(P[sel].sum() / Rw[sel].sum())))
        h = sel.copy(); h[f < 4000] = False
        if h.sum() > 20:
            hf.append(float(10 * np.log10((P[h].sum() + 1e-20) / Rw[h].sum())))
    R['speech_change_db'] = round(float(np.mean(att)), 2) if att else None      # should stay near 0
    R['speech_hf_change_db'] = round(float(np.mean(hf)), 2) if hf else None     # dullness introduced by the model
    R['speech_distortion_lsd'] = round(float(np.mean(lsd)), 2) if lsd else None
    R.update(quality_avg(y, segs))
    return R


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rec', required=True); ap.add_argument('--out', default='work')
    ap.add_argument('--only', help='comma list: dpdfnet,deepfilter,rnnoise'); ap.add_argument('--keep', action='store_true', help='keep every candidate wav')
    a = ap.parse_args(); nrd = os.path.join(a.out, 'nr'); os.makedirs(nrd, exist_ok=True)
    x, segs = load_all(a.rec); noise = seg(x, segs, '01_silence')
    concat = os.path.join(nrd, '_concat.wav'); sf.write(concat, x, SR, subtype='PCM_24')
    # neural denoisers are trained on speech around -26 dBFS; a very quiet mic can sit outside what they handle
    auto = int(np.clip(round(-24 - lufs(seg(x, segs, '02_normal'))), 0, 36))
    cands = []
    dp, df, rn = find_ladspa('libdpdfnet_ladspa.so'), find_ladspa('libdeep_filter_ladspa.so'), find_rnnoise()
    only = set(a.only.split(',')) if a.only else None
    if dp and (not only or 'dpdfnet' in only):
        cands.append(dict(name='dpdfnet', kind='ladspa', lib=dp, label='dpdfnet_mono', controls=[100], pregain=0))
    if df and (not only or 'deepfilter' in only):
        for g in sorted({0, auto}):
            cands.append(dict(name=f'deepfilter_pre{g}', kind='ladspa', lib=df, label='deep_filter_mono', controls=[100, -15, 35, 35, 0, 0.02], pregain=g))
    if rn and (not only or 'rnnoise' in only):
        for g in sorted({0, auto}):
            cands.append(dict(name=f'rnnoise_pre{g}', kind='rnnoise', lib=rn, pregain=g))
    if not cands:
        sys.exit('no noise-removal plugin found (looked for libdpdfnet_ladspa.so, libdeep_filter_ladspa.so, librnnoise.so). '
                 'Install one (see references/easyeffects.md) or continue with the NR-off design only.')
    print('candidates:', ', '.join(c['name'] for c in cands), f'(auto pre-gain {auto} dB; real-time runs, ~{len(x) // SR} s)')

    def job(c):
        try:
            out = os.path.join(nrd, c['name'] + '.wav')
            y = run_rnnoise(x, c['lib'], c['pregain']) if c['kind'] == 'rnnoise' else run_ladspa(concat, out + '.tmp.wav', c['lib'], c['label'], c['controls'], c['pregain'], len(x) / SR + 45)
            y, lag = align(x, y); sf.write(out, y, SR, subtype='FLOAT')
            if os.path.exists(out + '.tmp.wav'): os.remove(out + '.tmp.wav')
            return y, lag
        except Exception as e:
            return e, None
    with ThreadPoolExecutor(max(1, len(cands))) as ex:
        outs = list(ex.map(job, cands))
    for i, c in enumerate(cands):                 # one sequential retry for anything that hung or failed in the parallel pass
        if isinstance(outs[i][0], Exception):
            print('retrying', c['name'], 'on its own ...'); outs[i] = job(c)
    masks = {n: vad_mask(seg(x, segs, n), noise) for n in SPEECH if n in segs}
    f, _, Sn = ss.stft(noise, SR, nperseg=1024, noverlap=768); Npsd = np.mean(np.abs(Sn) ** 2, axis=1)
    RAW = {n: np.abs(ss.stft(seg(x, segs, n), SR, nperseg=1024, noverlap=768)[2]) ** 2 for n in masks}
    res = [dict(name='raw', **metrics(x, x, segs, noise, masks, f, Npsd, RAW))]
    for c, (y, lag) in zip(cands, outs):
        if isinstance(y, Exception):
            res.append(dict(name=c['name'], error=str(y)[-300:])); continue
        m = metrics(x, y, segs, noise, masks, f, Npsd, RAW)
        m.update(name=c['name'], lag_samples=lag, pregain=c['pregain'], kind=c['kind'], plugin=os.path.basename(c['lib']))
        res.append(m)

    def score(m):
        if 'error' in m or m['name'] == 'raw':
            return -1e9
        if 'dnsmos_ovrl' in m:
            return (m['dnsmos_ovrl'] + m['nisqa_mos']) / 2
        return min(m['silence_nr_db'], 40) / 40 - max(0.0, -(m['speech_change_db'] or 0) - 0.5) - (m['speech_distortion_lsd'] or 0) / 20
    ok = [m for m in res if score(m) > -1e8]
    cols = ['name', 'silence_nr_db', 'silence_resid_db', 'keyboard_peak_db', 'speech_change_db', 'speech_hf_change_db', 'speech_distortion_lsd',
            'dnsmos_ovrl', 'dnsmos_sig', 'dnsmos_bak', 'nisqa_mos']
    print('\t'.join(cols))
    for m in res:
        print('\t'.join(str(m.get(c, '')) for c in cols) if 'error' not in m else f"{m['name']}\tERROR {m['error']}")
    winner = max(ok, key=score) if ok else None
    if winner:
        shutil.copy(os.path.join(nrd, winner['name'] + '.wav'), os.path.join(nrd, 'best.wav'))
        print('winner:', winner['name'], '(ranked by DNSMOS+NISQA)' if 'dnsmos_ovrl' in winner else '(ranked by noise reduction vs speech damage; install the quality packages for a better ranking)')
        if not a.keep:
            for c in cands:
                p = os.path.join(nrd, c['name'] + '.wav')
                if c['name'] != winner['name'] and os.path.exists(p): os.remove(p)
    os.remove(concat)
    jdump(dict(results=res, winner=winner, auto_pregain=auto, quality_metrics=quality_available()), os.path.join(a.out, 'nr_bench.json'))
    print('wrote', os.path.join(a.out, 'nr_bench.json'))


if __name__ == '__main__':
    main()
