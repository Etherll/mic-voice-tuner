#!/usr/bin/env python3
"""Turn a chain.json variant into Equalizer APO (Windows) configuration files.

  python apo_config.py --rec rec --work work --variant nr_off --out work/apo

Writes:
  mic_voice_native.txt   gain + EQ only, with the gain capped so the loudest recorded speech cannot clip.
                         Works on a stock Equalizer APO install. No noise removal, gate, compressor or limiter.
  mic_voice_full.txt     the same filters at the full design gain, with markers where VST plugins go.
                         ONLY safe with a limiter VST as the last stage.
  apo_vst_settings.md    the values to dial into the noise-removal / gate / compressor / limiter plugins.

Equalizer APO has no native dynamics or noise removal; those need 64-bit VST2 plugins added through its
Configuration Editor (the editor writes the VSTPlugin line, including the plugin's saved state).
"""
import argparse, os, sys, json
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from micdsp import *  # noqa


def filt_lines(bands):
    out = []
    for b in bands:
        t = b['type']
        if t == 'hp':     # APO high-pass is 2nd order; cascade sections with Butterworth Qs for a steeper one
            out += [f"Filter: ON HPQ Fc {b['f']:g} Hz Q {q:.4f}" for q in butter_qs(b.get('order', 4))]
        elif t == 'bell':
            out.append(f"Filter: ON PK Fc {b['f']:g} Hz Gain {b['gain']:g} dB Q {b['q']:g}")
        elif t == 'hs':
            out.append(f"Filter: ON HSC Fc {b['f']:g} Hz Gain {b['gain']:g} dB Q {b.get('q', 0.7071):g}")
        elif t == 'ls':
            out.append(f"Filter: ON LSC Fc {b['f']:g} Hz Gain {b['gain']:g} dB Q {b.get('q', 0.7071):g}")
        elif t == 'notch':
            out.append(f"Filter: ON NO Fc {b['f']:g} Hz Q {b['q']:g}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rec', required=True); ap.add_argument('--work', default='work'); ap.add_argument('--variant', required=True, choices=['nr_on', 'nr_off'])
    ap.add_argument('--out', default=None); ap.add_argument('--device', default='all', help='Equalizer APO Device pattern, e.g. "Microphone USB"')
    a = ap.parse_args(); out = a.out or os.path.join(a.work, 'apo'); os.makedirs(out, exist_ok=True)
    chain = json.load(open(os.path.join(a.work, 'chain.json')))
    if a.variant not in chain['variants']:
        sys.exit(f'chain.json has no {a.variant} variant')
    ch = chain['variants'][a.variant]
    x, segs = load_all(a.rec)
    # native-only: no limiter exists, so the loudest thing the user recorded sets how much gain is safe
    z = eq(x, ch['eq']); pk = float(db(np.abs(z).max())); safe = float(min(ch['gain_db'], np.floor(-3.0 - pk)))
    y = z * 10 ** (safe / 20)
    pred = {n[3:]: round(lufs(seg(y, segs, n)), 1) for n in SPEECH if n in segs}
    head = [f'Device: {a.device}', 'Stage: capture', 'Channel: all']
    f = filt_lines(ch['eq'])
    native = ['# mic-voice-tuner: gain + EQ only (no plugins needed).',
              f'# Gain is capped at {safe:+g} dB so the loudest recorded speech peaks at {pk + safe:.1f} dBFS; the design wanted {ch["gain_db"]:+g} dB.',
              f'# Predicted loudness: {json.dumps(pred)}', *head, f'Preamp: {safe:g} dB', *f]
    open(os.path.join(out, 'mic_voice_native.txt'), 'w', newline='\r\n').write('\n'.join(native) + '\n')
    # offline check with Equalizer APO's Benchmark.exe: it presents itself as device "Benchmark" and is not a capture endpoint,
    # so this copy selects that device and has no Stage line
    bench = ['# For Benchmark.exe only (see references/equalizer-apo.md). Not for daily use.', 'Device: Benchmark', 'Channel: all', f'Preamp: {safe:g} dB', *f]
    open(os.path.join(out, 'mic_voice_benchmark_test.txt'), 'w', newline='\r\n').write('\n'.join(bench) + '\n')
    import soundfile as sf
    sf.write(os.path.join(out, 'raw_concat_48k.wav'), x, SR, subtype='PCM_24')
    nr = ch.get('nr')
    full = ['# mic-voice-tuner: full chain. Lines run top to bottom. Add each VST with the Configuration Editor at its marker',
            '# (Plugins -> VST plugin), then set it from apo_vst_settings.md. DO NOT enable this file without the limiter:', '# the gain below will clip loud speech.', *head]
    if a.variant == 'nr_on':
        full.append('# [VST 1] noise removal goes HERE, before any gain or EQ')
    full += [f'Preamp: {ch["gain_db"]:g} dB', *f]
    if ch.get('gate'):
        full.append('# [VST 2] gate goes HERE')
    full += ['# [VST 3] compressor goes HERE', '# [VST 4] limiter goes HERE and must stay last']
    open(os.path.join(out, 'mic_voice_full.txt'), 'w', newline='\r\n').write('\n'.join(full) + '\n')
    g, c, l = ch.get('gate'), ch['compressor'], ch['limiter']
    md = ['# VST settings for the full Equalizer APO chain', '',
          'Values were designed for RMS level detection with a ~10 ms window. Plugins differ, so after dialing them in, run the',
          'recordings through Benchmark.exe (see references/equalizer-apo.md) and check the result with evaluate.py.', '']
    if a.variant == 'nr_on':
        md += ['## 1. Noise removal (first in the chain)',
               f'- The design was made on the output of "{nr["model"]}" on Linux. On Windows use an RNNoise VST2 (werman noise-suppression-for-voice) or another',
               '  48 kHz noise-suppression VST. The mic must run at 48000 Hz for RNNoise.',
               '- RNNoise VST: VAD Threshold 0 % to start (pure denoise, no hard muting); raise toward 85-95 % only if noise still leaks between words.',
               f'- If the plugin has input/output gain and the voice is very quiet, {nr.get("pregain_db", 0):+g} dB in and the opposite out helped the Linux models.', '']
    md += ['## Preamp and EQ (native, already in mic_voice_full.txt)', f'- Preamp {ch["gain_db"]:+g} dB, then: ' + '; '.join(s.replace('Filter: ON ', '') for s in f), '']
    if g:
        md += ['## 2. Gate / expander', f'- Threshold (opens fully at): {g["threshold"]:g} dB', f'- Fully closed below: {g["threshold"] + g["zone"]:g} dB (soft zone {abs(g["zone"]):g} dB)',
               f'- Range / floor (attenuation when closed): {g["reduction"]:g} dB (not a hard mute)', f'- Attack {g["attack"]:g} ms, release {g["release"]:g} ms, hysteresis {abs(g["hysteresis_threshold"]):g} dB',
               f'- Sidechain / detector high-pass: {g["sc_hp_hz"]} Hz. Detector: RMS ~10 ms. Look-ahead {g["lookahead"]:g} ms if available.', '']
    md += ['## 3. Compressor', f'- Threshold {c["threshold"]:g} dB, ratio {c["ratio"]:g}:1, soft knee {abs(c["knee"]) * 2:g} dB wide',
           f'- Attack {c["attack"]:g} ms, release {c["release"]:g} ms, detector RMS ~10 ms, detector high-pass {c["sc_hp_hz"]} Hz',
           f'- Make-up / output gain {c["makeup"]:+g} dB, auto make-up OFF', '',
           '## 4. Limiter (last)', f'- Ceiling {l["threshold"]:g} dBFS, look-ahead {l["lookahead"]:g} ms, release {l["release"]:g} ms, no extra input gain', '',
           '## What the design predicts', '', '    ' + json.dumps(ch.get('predicted', {}))]
    open(os.path.join(out, 'apo_vst_settings.md'), 'w').write('\n'.join(md) + '\n')
    print('wrote', out); print('native-only: gain %+g dB (design wanted %+g), predicted loudness %s' % (safe, ch['gain_db'], json.dumps(pred)))
    if safe < ch['gain_db'] - 6:
        print('note: native-only stays', round(ch['gain_db'] - safe), 'dB quieter than the design because nothing limits peaks. '
              'Getting closer to the mic or adding the VST chain closes that gap.')


if __name__ == '__main__':
    main()
