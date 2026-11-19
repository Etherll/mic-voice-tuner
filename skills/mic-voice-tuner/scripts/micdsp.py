"""Shared DSP, measurement and chain-simulation helpers for mic-voice-tuner.

Everything works at 48 kHz mono float. Filters are RBJ cookbook biquads, which is what
Equalizer APO implements and what Easy Effects' "APO (DR)" equalizer mode implements,
so one model predicts both.
"""
import os, json, math
import numpy as np, soundfile as sf, scipy.signal as ss

SR = 48000
STAGES = ['01_silence', '02_normal', '03_loud', '04_quiet', '05_sibilance', '06_keyboard', '07_speech_typing']
SPEECH = ['02_normal', '03_loud', '04_quiet', '05_sibilance', '07_speech_typing']
FC3 = np.array([63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500,
                3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000.])
# Long-term average speech spectrum (Byrne et al. 1994, male, 1/3-octave dB) for the bands in FC3[2:]
LTASS = np.array([54.4, 57.7, 56.8, 60.2, 60.3, 59.0, 62.1, 62.1, 60.5, 56.8, 53.7, 53.0, 52.0, 48.7, 48.1,
                  46.8, 45.6, 44.5, 44.3, 43.7, 43.4, 41.3, 40.7])


def db(x):
    return 20 * np.log10(np.maximum(x, 1e-12))


def rms(x):
    return float(np.sqrt(np.mean(np.square(x)) + 1e-30))


def read_mono(path):
    x, fs = sf.read(path, dtype='float64', always_2d=True)
    x = x.mean(axis=1)
    if fs != SR:
        g = math.gcd(SR, fs)
        x = ss.resample_poly(x, SR // g, fs // g)
    return x


def load_all(rec_dir, trim=0.3):
    """Concatenate the recorded stages. Returns (signal, {stage: (start, end)}).
    The first 0.3 s of each file is dropped because capture start-up is unreliable."""
    xs, segs, pos = [], {}, 0
    for n in STAGES:
        p = os.path.join(rec_dir, n + '.wav')
        if not os.path.exists(p):
            continue
        x = read_mono(p)[int(trim * SR):]
        segs[n] = (pos, pos + len(x)); pos += len(x); xs.append(x)
    for need in ('01_silence', '02_normal'):
        if need not in segs:
            raise SystemExit(f'missing {need}.wav in {rec_dir}: the silence and normal-speech stages are required')
    return np.concatenate(xs), segs


def seg(v, segs, name):
    a, b = segs[name]
    return v[a:b]


def aweight(x):
    f1, f2, f3, f4 = 20.598997, 107.65265, 737.86223, 12194.217
    z = [0, 0, 0, 0]
    p = [-2 * np.pi * f4] * 2 + [-2 * np.pi * f1] * 2 + [-2 * np.pi * f2, -2 * np.pi * f3]
    k = (2 * np.pi * f4) ** 2 * 10 ** (1.9997 / 20)
    return ss.sosfilt(ss.zpk2sos(*ss.bilinear_zpk(z, p, k, SR)), x)


def frames_rms(x, win=0.02, hop=0.01):
    w, h = int(win * SR), int(hop * SR)
    n = 1 + (len(x) - w) // h
    if n <= 0:
        return np.array([rms(x)])
    idx = np.arange(w)[None, :] + h * np.arange(n)[:, None]
    return np.sqrt(np.mean(x[idx] ** 2, axis=1))


def vad_mask(x, noise, margin_db=10.0):
    """Speech-activity mask per 10 ms frame, from the 300-3400 Hz band where room noise is weakest."""
    sos = ss.butter(4, [300, 3400], 'band', fs=SR, output='sos')
    fb = frames_rms(ss.sosfilt(sos, x)); nb = np.median(frames_rms(ss.sosfilt(sos, noise)))
    m = fb > nb * 10 ** (margin_db / 20)
    return np.convolve(m.astype(float), np.ones(11), 'same') > 0   # 100 ms hangover


def align(ref, y, max_ref_s=90):
    """Shift y onto ref's timeline (any lag, either sign). Returns (aligned y with len(ref), lag samples).
    Needed because NR plugins and live pipelines add anything from 10 ms to several seconds of delay."""
    n = min(len(ref), int(max_ref_s * SR))
    c = ss.correlate(y, ref[:n], 'full', method='fft')
    lag = int(np.argmax(np.abs(c)) - (n - 1))
    out = y[lag:] if lag >= 0 else np.concatenate([np.zeros(-lag), y])
    if len(out) < len(ref):
        out = np.concatenate([out, np.zeros(len(ref) - len(out))])
    return out[:len(ref)], lag


def lufs(v):
    import pyloudnorm as pyln
    try:
        l = float(pyln.Meter(SR).integrated_loudness(v))
        return l if np.isfinite(l) else -99.0
    except Exception:
        return -99.0


def true_peak_db(v):
    return float(db(np.abs(ss.resample_poly(v, 4, 1)).max()))


def third_octave(v, loud_only=True):
    """1/3-octave band levels (dB) at FC3; loud_only keeps the louder half of frames (speech, not pauses)."""
    f, t, S = ss.spectrogram(v, SR, nperseg=4096, noverlap=3072, scaling='spectrum', mode='psd')
    if loud_only and S.shape[1] > 4:
        tot = S.sum(0); S = S[:, tot > np.percentile(tot, 50)]
    P = S.mean(1)
    return np.array([10 * np.log10(P[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-20) for c in FC3])


# ---------------------------------------------------------------- filters (RBJ)
def _norm(b, a):
    return np.concatenate([np.array(b) / a[0], np.array(a) / a[0]])


def peaking(f0, g, q):
    A = 10 ** (g / 40); w = 2 * np.pi * f0 / SR; al = np.sin(w) / (2 * q); c = np.cos(w)
    return _norm([1 + al * A, -2 * c, 1 - al * A], [1 + al / A, -2 * c, 1 - al / A])


def hishelf(f0, g, q=0.7071):
    A = 10 ** (g / 40); w = 2 * np.pi * f0 / SR; al = np.sin(w) / (2 * q); c = np.cos(w); s = 2 * np.sqrt(A) * al
    return _norm([A * ((A + 1) + (A - 1) * c + s), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - s)],
                 [(A + 1) - (A - 1) * c + s, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - s])


def loshelf(f0, g, q=0.7071):
    A = 10 ** (g / 40); w = 2 * np.pi * f0 / SR; al = np.sin(w) / (2 * q); c = np.cos(w); s = 2 * np.sqrt(A) * al
    return _norm([A * ((A + 1) - (A - 1) * c + s), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - s)],
                 [(A + 1) + (A - 1) * c + s, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - s])


def notch(f0, q):
    w = 2 * np.pi * f0 / SR; al = np.sin(w) / (2 * q); c = np.cos(w)
    return _norm([1, -2 * c, 1], [1 + al, -2 * c, 1 - al])


def hpq(f0, q):
    w = 2 * np.pi * f0 / SR; al = np.sin(w) / (2 * q); c = np.cos(w)
    return _norm([(1 + c) / 2, -(1 + c), (1 + c) / 2], [1 + al, -2 * c, 1 - al])


def butter_qs(order):
    """Q of each 2nd-order section of an even-order Butterworth filter (order 4 -> 0.5412, 1.3066)."""
    n = int(order)
    return [1 / (2 * math.sin((2 * k + 1) * math.pi / (2 * n))) for k in range(n // 2)]


def eq_sos(bands):
    """bands: list of dicts {type: hp|bell|hs|ls|notch, f, gain, q, order}."""
    sos = []
    for b in bands:
        t = b['type']
        if t == 'hp':
            sos += [hpq(b['f'], q) for q in butter_qs(b.get('order', 4))]
        elif t == 'bell':
            sos.append(peaking(b['f'], b['gain'], b['q']))
        elif t == 'hs':
            sos.append(hishelf(b['f'], b['gain'], b.get('q', 0.7071)))
        elif t == 'ls':
            sos.append(loshelf(b['f'], b['gain'], b.get('q', 0.7071)))
        elif t == 'notch':
            sos.append(notch(b['f'], b['q']))
        else:
            raise ValueError('unknown band type ' + t)
    return np.array(sos) if sos else np.array([[1, 0, 0, 1, 0, 0.]])


def eq(x, bands):
    return ss.sosfilt(eq_sos(bands), x)


def eq_resp(bands, f):
    _, h = ss.sosfreqz(eq_sos(bands), worN=np.asarray(f, float), fs=SR)
    return 20 * np.log10(np.abs(h) + 1e-12)


# ---------------------------------------------------------------- dynamics models
def _coef(ms):
    return 1.0 - math.exp(math.log(1.0 - 0.70710678) / (max(ms, 0.01) * SR / 1000.0))


def _env(s, ta, tr):
    e = np.empty_like(s); cur = 0.0
    for i in range(len(s)):
        v = s[i]
        cur += (ta if v > cur else tr) * (v - cur)
        e[i] = cur
    return e


try:  # the envelope loop is ~100x faster with numba; without it everything still works, slowly
    import numba
    _env = numba.njit(cache=True, nogil=True)(_env)
except Exception:
    pass


def envelope(s, att_ms, rel_ms):
    return _env(np.ascontiguousarray(s, dtype=np.float64), _coef(att_ms), _coef(rel_ms))


def sc_rms(x, react_ms=10.0):
    a = 1.0 - math.exp(-1.0 / (react_ms * SR / 1000.0))
    return np.sqrt(np.maximum(ss.lfilter([a], [1, -(1 - a)], x * x), 1e-20))


def sc_filter(x, hp_hz=None, order=4):
    return x if not hp_hz else ss.sosfilt(ss.butter(order, hp_hz, 'high', fs=SR, output='sos'), x)


def comp_gain_db(L, T, R, K=6.0):
    L = np.asarray(L); g = np.zeros_like(L); k = abs(K); lo, hi = T - k, T + k
    over = L >= hi; g[over] = (T + (L[over] - T) / R) - L[over]
    mid = (L > lo) & (L < hi)
    if k > 0:
        g[mid] = (1 / R - 1) * (L[mid] - lo) ** 2 / (4 * k)
    return g


def compressor(x, T, R, K=6.0, att=10.0, rel=120.0, makeup=0.0, sc_hp=120):
    e = envelope(sc_rms(sc_filter(x, sc_hp, 2)), att, rel)
    g = comp_gain_db(db(e), T, R, K)
    return x * 10 ** ((g + makeup) / 20), g


def gate(x, T, zone=-6.0, red=-20.0, att=3.0, rel=150.0, sc_hp=500, look_ms=5.0):
    """Downward gate: full 'red' dB below T+zone, unity above T, smooth in between."""
    e = envelope(sc_rms(sc_filter(x, sc_hp, 4)), att, rel)
    L = db(e); lo, hi = T + zone, T
    t = np.clip((L - lo) / (hi - lo), 0, 1); t = t * t * (3 - 2 * t)
    g = red * (1 - t)
    n = int(look_ms * SR / 1000)
    xd = np.concatenate([np.zeros(n), x])[:len(x)]
    return xd * 10 ** (g / 20), g


def limiter(x, thr_db=-2.0, look_ms=5.0):
    from scipy.ndimage import maximum_filter1d
    th = 10 ** (thr_db / 20); n = max(1, int(look_ms * SR / 1000)); a = np.abs(x)
    pk = maximum_filter1d(a, size=2 * n + 1, mode='constant')
    g = np.minimum(1.0, th / np.maximum(pk, 1e-12))
    g = ss.lfilter(np.ones(n) / n, [1], np.concatenate([g, np.ones(n)]))[n // 2:n // 2 + len(x)]
    g = np.minimum(g, np.minimum(1.0, th / np.maximum(a, 1e-12)))
    return x * g, 20 * np.log10(g)


def apply_chain(x, ch, stages=('gain', 'eq', 'gate', 'compressor', 'limiter')):
    """Simulate a chain variant from chain.json on x (x = raw mic, or the NR plugin's output for NR-on)."""
    y = x
    if 'eq' in stages:
        y = eq(y, ch['eq'])
    if 'gain' in stages:
        y = y * 10 ** (ch['gain_db'] / 20)
    g = ch.get('gate')
    if g and 'gate' in stages:
        y, _ = gate(y, g['threshold'], g['zone'], g['reduction'], g['attack'], g['release'], g['sc_hp_hz'], g['lookahead'])
    c = ch.get('compressor')
    if c and 'compressor' in stages:
        y, _ = compressor(y, c['threshold'], c['ratio'], abs(c['knee']), c['attack'], c['release'], c['makeup'], c['sc_hp_hz'])
    l = ch.get('limiter')
    if l and 'limiter' in stages:
        y, _ = limiter(y, l['threshold'], l['lookahead'])
    return y


# ---------------------------------------------------------------- perceptual estimates (optional)
_Q = {}


def quality_available():
    if 'ok' not in _Q:
        try:
            import torch, librosa  # noqa
            from speechmos import dnsmos  # noqa
            from torchmetrics.functional.audio.nisqa import non_intrusive_speech_quality_assessment  # noqa
            _Q['ok'] = True
        except Exception as e:
            _Q['ok'] = False; _Q['why'] = str(e)
    return _Q['ok']


def quality(v, norm_lufs=-26.0):
    """DNSMOS P.835 (ovrl/sig/bak) and NISQA (mos/noisiness/discontinuity/coloration/loudness), each 1-5.
    These are model estimates, not listening tests. norm_lufs=None scores the audio at its real level
    (so loudness problems show); a number scores at equal loudness (so only timbre/noise/artefacts show)."""
    if not quality_available():
        return {}
    import torch, librosa
    from speechmos import dnsmos
    from torchmetrics.functional.audio.nisqa import non_intrusive_speech_quality_assessment as nisqa
    if norm_lufs is not None:
        l = lufs(v)
        if l > -90:
            v = v * 10 ** ((norm_lufs - l) / 20)
    v = np.clip(v, -1, 1).astype(np.float32)
    v16 = np.clip(librosa.resample(v, orig_sr=SR, target_sr=16000), -1, 1)
    d = dnsmos.run(v16, sr=16000)
    n = nisqa(torch.tensor(v), SR).numpy()
    return dict(dnsmos_ovrl=float(d['ovrl_mos']), dnsmos_sig=float(d['sig_mos']), dnsmos_bak=float(d['bak_mos']),
                nisqa_mos=float(n[0]), nisqa_noisiness=float(n[1]), nisqa_discontinuity=float(n[2]),
                nisqa_coloration=float(n[3]), nisqa_loudness=float(n[4]))


def quality_avg(y, segs, norm_lufs=-26.0, names=None):
    names = [n for n in (names or SPEECH) if n in segs]
    rs = [quality(seg(y, segs, n), norm_lufs) for n in names]
    rs = [r for r in rs if r]
    return {k: round(float(np.mean([r[k] for r in rs])), 3) for k in rs[0]} if rs else {}


def jdump(obj, path):
    def conv(o):
        if isinstance(o, (np.floating,)): return float(o)
        if isinstance(o, (np.integer,)): return int(o)
        if isinstance(o, np.ndarray): return o.tolist()
        raise TypeError(str(type(o)))
    with open(path, 'w') as f:
        json.dump(obj, f, indent=1, default=conv)
