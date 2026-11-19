#!/usr/bin/env python3
"""Design the voice chain from measurements and write work/chain.json (platform-neutral).

  python design.py --rec rec --work work [--target-lufs -17] [--ceiling -2]

Produces up to two variants:
  nr_on   designed on the output of the best noise-removal model (work/nr/best.wav from bench_nr.py)
  nr_off  designed on the raw mic: steeper high-pass, notches for stable tones, deeper gate, gentler compression
Every number is derived from the recordings; chain.json also stores what the simulation predicts so the
real pipeline can be checked against it.
"""
import argparse, os, sys, json
import numpy as np, scipy.signal as ss, scipy.optimize as so
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from micdsp import *  # noqa
from analyze import sibilance


def r_to(v, step):
    return round(round(v / step) * step, 3)


def design_eq(src, segs, f0, on, strength=0.6):
    """High-pass from the voice pitch + a 3-band tonal correction toward the long-term average speech spectrum.
    Only ~60 % of the measured deviation is corrected and boosts are capped: the reference is an average of
    many talkers on a flat mic, so chasing it exactly would overfit one take and amplify whatever is boosted.
    Without noise removal the treble boost is capped lower, because it lifts hiss and key clicks as much as the voice."""
    hp = int(np.clip(r_to(0.72 * f0, 5), 60, 120)) if f0 else 80
    sp = np.mean([third_octave(seg(src, segs, n))[2:] for n in SPEECH if n in segs], axis=0)
    fc = FC3[2:]; diff = (sp - sp[6:9].mean()) - (LTASS - LTASS[6:9].mean()); want = -strength * diff
    w = np.ones_like(fc); w[fc < 200] = 0.2; w[(fc >= 200) & (fc <= 250)] = 0.3; w[fc >= 12500] = 0.4; w[fc >= 16000] = 0.2

    def mk(p):
        return [dict(type='bell', f=p[0], gain=p[1], q=p[2]), dict(type='hs', f=p[3], gain=p[4], q=0.7071), dict(type='bell', f=p[5], gain=p[6], q=p[7])]

    def cost(p):
        e = eq_resp(mk(p), fc) - want; e = e - e[6:9].mean()
        return np.concatenate([w * e, 0.15 * np.array([p[1], p[4], p[6]])])
    r = so.least_squares(cost, [1000, 0, 1.2, 4700, 0, 280, 0, 1.0],
                         bounds=([700, -4, 0.8, 3500, -4, 180, -4, 0.7], [1400, 4, 2.0, 6500, 8 if on else 6, 400, 3, 2.0]))
    bands = [dict(type='hp', f=hp, order=4)]
    for b in mk(r.x):
        b = dict(b, f=int(r_to(b['f'], 50 if b['f'] < 2000 else 100)), gain=r_to(b['gain'], 0.5), q=round(b['q'], 2))
        if abs(b['gain']) >= 1.0:
            bands.append(b)
    return bands, dict(fc=fc.tolist(), deviation_from_reference_db=[round(float(v), 1) for v in diff])


def design_gate(z, segs, masks, on):
    """Threshold sits just above what must be silenced and below quiet speech, measured on the same
    500 Hz-high-passed sidechain the plugin uses (room noise and desk thumps live below 500 Hz, speech does not)."""
    L = db(sc_rms(sc_filter(z, 500, 4)))
    ref = float(np.percentile(seg(L, segs, '01_silence')[3 * SR:], 99.9))
    if on and '06_keyboard' in segs:
        ref = max(ref, float(np.percentile(seg(L, segs, '06_keyboard'), 99.9)))
    if on and ref < -90:
        return None, ['Noise remover leaves nothing audible between words; no gate needed.']
    zone = -6.0 if on else -5.0
    T = ref + (2.0 if on else 1.5) - zone
    q = '04_quiet' if '04_quiet' in masks else '02_normal'
    sc = seg(L, segs, q)[::480]; m = masks[q][:len(sc)]; quiet = float(np.percentile(sc[:len(m)][m], 25))
    notes = []
    if T > quiet - 2:
        T = quiet - 2; notes.append('Noise level overlaps quiet speech, so the gate threshold was capped to protect soft words; some noise will pass.')
    T = float(np.clip(round(T), -60, -20))
    g = dict(threshold=T, zone=zone, reduction=-20.0 if on else -30.0, attack=3.0 if on else 5.0, release=150.0 if on else 250.0,
             hysteresis_threshold=-6.0 if on else -4.0, hysteresis_zone=-6.0 if on else -5.0, sc_hp_hz=500, lookahead=5.0,
             measured=dict(must_silence_below_db=round(ref, 1), quiet_speech_p25_db=round(quiet, 1)))
    return g, notes


def design_comp(zg, segs, masks, on, target):
    """Pick the gentlest compression that evens out quiet vs loud speech. With no noise removal, compression is
    kept light on purpose: every dB of gain reduction on speech is a dB the room noise rises between words."""
    have = all(n in segs for n in ('03_loud', '04_quiet'))
    act = np.repeat(masks['02_normal'], 480)[:len(seg(zg, segs, '02_normal'))]
    best = None; rows = []
    for T in range(-39, -17, 3):
        for R in (2.0, 2.5, 3.0, 4.0):
            o, g = compressor(zg, T, R)
            L = {n: lufs(seg(o, segs, n)) for n in ('02_normal', '03_loud', '04_quiet') if n in segs}
            spread = (L['03_loud'] - L['04_quiet']) if have else None
            grn = seg(g, segs, '02_normal'); gr = float(np.median(grn[:len(act)][act[:len(grn)]]))
            rows.append(dict(T=T, R=R, spread=spread, gr=gr, L=L['02_normal']))
    if not have:
        Ln = lufs(seg(zg, segs, '02_normal')); c = min(rows, key=lambda r: abs(r['T'] - (Ln - 11)) + abs(r['R'] - (3 if on else 2)))
    elif on:
        ok = [r for r in rows if r['spread'] <= 6.0]
        c = min(ok, key=lambda r: (r['R'], abs(r['gr']))) if ok else min(rows, key=lambda r: r['spread'])   # lowest ratio first: it is the least audible
    else:
        ok = [r for r in rows if r['R'] <= 2.0 and abs(r['gr']) <= 3.0]
        c = min(ok, key=lambda r: r['spread']) if ok else min(rows, key=lambda r: abs(r['gr']))
    return dict(threshold=float(c['T']), ratio=c['R'], knee=-6.0, attack=10.0, release=120.0, makeup=r_to(target - c['L'], 0.5), sc_hp_hz=120,
                measured=dict(quiet_to_loud_spread_db=round(c['spread'], 1) if c['spread'] is not None else None, median_gain_reduction_db=round(c['gr'], 1)))


def notches(analysis):
    out = []
    for t in analysis.get('noise', {}).get('tones', []):
        if t['stable'] and 85 <= t['f'] <= 1200 and t['prominence'] >= 15 and len(out) < 4:
            wd = t.get('wander_hz') or 0
            out.append(dict(type='notch', f=t['f'], q=30.0 if wd <= 1 else float(np.clip(round(t['f'] / (4 * wd)), 8, 30))))
    out = sorted(out, key=lambda b: b['f']); merged = []
    for b in out:                                 # two tones a few Hz apart (a fan pair, a wandering tone) get one wider notch
        if merged and b['f'] / merged[-1]['f'] < 1.03:
            a = merged[-1]; span = b['f'] - a['f']; f = round((a['f'] + b['f']) / 2, 1)
            merged[-1] = dict(type='notch', f=f, q=float(np.clip(round(f / (3 * span)), 8, 30)))
        else:
            merged.append(b)
    return merged


def predict(src, raw, segs, ch):
    y = apply_chain(src, ch)
    P = dict(lufs={n[3:]: round(lufs(seg(y, segs, n)), 1) for n in SPEECH if n in segs},
             peak_db=round(float(db(np.abs(y).max())), 1), silence_rms_db=round(float(db(rms(seg(y, segs, '01_silence')[3 * SR:]))), 1))
    if '06_keyboard' in segs:
        P['keyboard_peak_db'] = round(float(db(np.abs(seg(y, segs, '06_keyboard')).max())), 1)
    s = [sibilance(seg(y, segs, n)) for n in ('02_normal', '05_sibilance') if n in segs]; s = [v['sib_minus_voiced'] for v in s if v]
    P['sibilance_minus_vowels_db'] = round(float(np.mean(s)), 1) if s else None
    q = quality_avg(y, segs, None)
    if q:
        P['quality_at_real_level'] = q; P['quality_at_equal_loudness'] = quality_avg(y, segs)
    return P, y


def variant(src, raw, segs, masks, analysis, on, target, ceiling, nr):
    f0 = (analysis.get('f0') or {}).get('p50'); notes = []
    eqb, eqinfo = design_eq(src, segs, f0, on)
    if not on:
        eqb[0]['order'] = 8                       # hum and rumble are not removed by a model here, so cut harder
        eqb[1:1] = notches(analysis)
    gain = float(np.clip(round(-22 - lufs(seg(eq(src, eqb), segs, '02_normal'))), -12, 36))
    z = eq(src, eqb) * 10 ** (gain / 20)
    g, n2 = design_gate(z, segs, masks, on); notes += n2
    zg = gate(z, g['threshold'], g['zone'], g['reduction'], g['attack'], g['release'], g['sc_hp_hz'], g['lookahead'])[0] if g else z
    c = design_comp(zg, segs, masks, on, target)
    ch = dict(nr=nr if on else None, gain_db=gain, eq=eqb, gate=g, compressor=c,
              limiter=dict(threshold=ceiling, lookahead=5.0, attack=5.0, release=20.0), eq_fit=eqinfo)
    P, y = predict(src, raw, segs, ch)
    miss = target - P['lufs']['normal']           # the limiter shaves peaks, which costs a little loudness: top it up once
    if abs(miss) >= 0.5:
        c['makeup'] = r_to(c['makeup'] + miss, 0.5); P, y = predict(src, raw, segs, ch)
    sib = P['sibilance_minus_vowels_db']
    ch['deesser'] = dict(recommended=bool(sib is not None and sib > -3.0), sibilance_minus_vowels_db=sib,
                         why='Sibilants within 3 dB of vowel peaks sound harsh; further below is natural. If you add a de-esser, A/B measure it: '
                             'some plugins cut far more than their threshold suggests.')
    ch['predicted'] = P; ch['notes'] = notes
    return ch


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rec', required=True); ap.add_argument('--work', default='work')
    ap.add_argument('--target-lufs', type=float, default=-17.0); ap.add_argument('--ceiling', type=float, default=-2.0)
    a = ap.parse_args()
    ap_ = os.path.join(a.work, 'analysis.json')
    if not os.path.exists(ap_):
        sys.exit('run analyze.py first (work/analysis.json not found)')
    analysis = json.load(open(ap_)); x, segs = load_all(a.rec); noise = seg(x, segs, '01_silence')
    masks = {n: vad_mask(seg(x, segs, n), noise) for n in SPEECH if n in segs}
    out = dict(version=1, targets=dict(lufs_nr_on=a.target_lufs, lufs_nr_off=a.target_lufs - 1, ceiling_db=a.ceiling), variants={})
    best = os.path.join(a.work, 'nr', 'best.wav'); nb = os.path.join(a.work, 'nr_bench.json')
    if os.path.exists(best) and os.path.exists(nb):
        w = json.load(open(nb))['winner']; y = read_mono(best)
        y = np.concatenate([y, np.zeros(max(0, len(x) - len(y)))])[:len(x)]
        nr = dict(model=w['name'].split('_pre')[0], pregain_db=w.get('pregain', 0), plugin=w.get('plugin'))
        out['variants']['nr_on'] = variant(y, x, segs, masks, analysis, True, a.target_lufs, a.ceiling, nr)
    else:
        print('no work/nr/best.wav: designing the NR-off variant only (run bench_nr.py first for an NR-on variant)')
    out['variants']['nr_off'] = variant(x, x, segs, masks, analysis, False, a.target_lufs - 1, a.ceiling, None)
    jdump(out, os.path.join(a.work, 'chain.json'))
    for k, ch in out['variants'].items():
        print(f'== {k}' + (f"  noise removal: {ch['nr']['model']} (pre-gain {ch['nr']['pregain_db']} dB)" if ch['nr'] else ''))
        print('  gain %+.0f dB | eq: ' % ch['gain_db'] + '; '.join(
            f"{b['type']} {b['f']} Hz" + (f" {b['gain']:+} dB" if 'gain' in b else '') + (f" Q{b['q']}" if 'q' in b else f" order {b['order']}") for b in ch['eq']))
        g, c = ch['gate'], ch['compressor']
        print('  gate:', f"thr {g['threshold']} zone {g['zone']} reduction {g['reduction']} dB" if g else 'none',
              f"| comp: thr {c['threshold']} ratio {c['ratio']} makeup {c['makeup']:+} dB | limiter {ch['limiter']['threshold']} dB")
        print('  predicted:', json.dumps(ch['predicted']['lufs']), 'silence', ch['predicted']['silence_rms_db'], 'dBFS | de-esser recommended:', ch['deesser']['recommended'])
        for n in ch['notes']: print('  note:', n)
    print('wrote', os.path.join(a.work, 'chain.json'))


if __name__ == '__main__':
    main()
