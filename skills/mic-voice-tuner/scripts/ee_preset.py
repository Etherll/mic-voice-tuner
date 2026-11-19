#!/usr/bin/env python3
"""Turn a chain.json variant into an Easy Effects input preset.

  python ee_preset.py --work work --variant nr_on  --name "Mic NR on"  [--load]
  python ee_preset.py --work work --variant nr_off --name "Mic NR off"

Keys follow Easy Effects 7/8 preset files (verified against 8.3 source). Enum-like values (mode, type, model)
are always written, because Easy Effects keeps the PREVIOUS value when such a key is missing from a preset.
"""
import argparse, os, sys, json, subprocess

SC = {'input-to-sidechain': -80.01, 'input-to-link': -80.01, 'sidechain-to-input': -80.01, 'sidechain-to-link': -80.01,
      'link-to-input': -80.01, 'link-to-sidechain': -80.01}


def presets_dir():
    h = os.path.expanduser('~')
    c = [h + '/.local/share/easyeffects/input', h + '/.config/easyeffects/input',
         h + '/.var/app/com.github.wwmm.easyeffects/data/easyeffects/input', h + '/.var/app/com.github.wwmm.easyeffects/config/easyeffects/input']
    for d in c:
        if os.path.isdir(d):
            return d
    sys.exit('Easy Effects preset folder not found; pass --presets-dir (create and save any preset in the app once to find it)')


def band(b):
    t = b['type']; o = dict(frequency=b['f'], gain=b.get('gain', 0), mode='APO (DR)', mute=False, q=b.get('q', 0.7071), slope='x1', solo=False, width=4)
    if t == 'hp':
        # Butterworth mode; measured in the real plugin, x2 and x4 are steeper than textbook order 4 / 8, which is fine here
        o.update(type='Hi-pass', mode='BWC (BT)', slope={2: 'x1', 4: 'x2', 8: 'x4'}.get(b.get('order', 4), 'x2'), q=0.7071)
    else:
        o['type'] = {'bell': 'Bell', 'hs': 'Hi-shelf', 'ls': 'Lo-shelf', 'notch': 'Notch'}[t]
    return o


def build(ch):
    P = {}; order = []
    nr = ch.get('nr')
    if nr:
        pre = float(nr.get('pregain_db', 0))
        if nr['model'] in ('dpdfnet', 'deepfilter'):
            P['deepfilternet#0'] = {'bypass': False, 'input-gain': pre, 'output-gain': -pre, 'model': 'DPDFNet' if nr['model'] == 'dpdfnet' else 'DeepFilterNet',
                                    'mono-processing': True, 'attenuation-limit': 100, 'min-processing-threshold': -15, 'max-erb-processing-threshold': 35,
                                    'max-df-processing-threshold': 35, 'min-processing-buffer': 0, 'post-filter-beta': 0.02}
            order.append('deepfilternet#0')
        elif nr['model'] == 'rnnoise':
            P['rnnoise#0'] = {'bypass': False, 'input-gain': pre, 'output-gain': -pre, 'enable-vad': False, 'vad-thres': 50, 'wet': 0, 'release': 20,
                              'use-standard-model': True, 'model-name': ''}
            order.append('rnnoise#0')
    bands = {f'band{i}': band(b) for i, b in enumerate(ch['eq'])}
    P['equalizer#0'] = {'bypass': False, 'input-gain': ch['gain_db'], 'output-gain': 0, 'num-bands': len(bands), 'split-channels': False, 'balance': 0,
                        'pitch-left': 0, 'pitch-right': 0, 'mode': 'IIR', 'decramp': 'Off', 'left': bands, 'right': json.loads(json.dumps(bands))}
    order.append('equalizer#0')
    g = ch.get('gate')
    if g:
        P['gate#0'] = {'bypass': False, 'input-gain': 0, 'output-gain': 0, 'dry': -80.01, 'wet': 0, 'attack': g['attack'], 'release': g['release'],
                       'curve-threshold': g['threshold'], 'curve-zone': g['zone'], 'hysteresis': True, 'hysteresis-threshold': g['hysteresis_threshold'],
                       'hysteresis-zone': g['hysteresis_zone'], 'reduction': g['reduction'], 'makeup': 0, 'stereo-split': False,
                       'hpf-mode': '24 dB/oct', 'hpf-frequency': g['sc_hp_hz'], 'lpf-mode': 'Off', 'lpf-frequency': 20000,
                       'sidechain': {'preamp': 0, 'reactivity': 10, 'lookahead': g['lookahead'], 'type': 'Internal', 'mode': 'RMS', 'source': 'Middle',
                                     'stereo-split-source': 'Left/Right'}, **SC}
        order.append('gate#0')
    c = ch['compressor']
    P['compressor#0'] = {'bypass': False, 'input-gain': 0, 'output-gain': 0, 'dry': -80.01, 'wet': 0, 'mode': 'Downward', 'attack': c['attack'],
                         'release': c['release'], 'release-threshold': -80.01, 'threshold': c['threshold'], 'ratio': c['ratio'], 'knee': c['knee'],
                         'makeup': c['makeup'], 'boost-threshold': -72, 'boost-amount': 6, 'stereo-split': False, 'hpf-mode': '12 dB/oct',
                         'hpf-frequency': c['sc_hp_hz'], 'lpf-mode': 'Off', 'lpf-frequency': 20000,
                         'sidechain': {'preamp': 0, 'reactivity': 10, 'lookahead': 0, 'type': 'Feed-forward', 'mode': 'RMS', 'source': 'Middle',
                                       'stereo-split-source': 'Left/Right'}, **SC}
    order.append('compressor#0')
    l = ch['limiter']
    P['limiter#0'] = {'bypass': False, 'input-gain': 0, 'output-gain': 0, 'mode': 'Herm Thin', 'oversampling': 'None', 'dithering': 'None',
                      'sidechain-type': 'Internal', 'lookahead': l['lookahead'], 'attack': l['attack'], 'release': l['release'], 'threshold': l['threshold'],
                      'gain-boost': False, 'sidechain-preamp': 0, 'stereo-link': 100, 'alr': False, 'alr-attack': 5, 'alr-release': 50, 'alr-knee': 0,
                      'alr-knee-smooth': -5, **SC}
    order.append('limiter#0')
    return {'input': {'blocklist': [], 'plugins_order': order, **P}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--work', default='work'); ap.add_argument('--variant', required=True, choices=['nr_on', 'nr_off'])
    ap.add_argument('--name', required=True); ap.add_argument('--presets-dir'); ap.add_argument('--load', action='store_true')
    a = ap.parse_args()
    chain = json.load(open(os.path.join(a.work, 'chain.json')))
    if a.variant not in chain['variants']:
        sys.exit(f'chain.json has no {a.variant} variant')
    d = a.presets_dir or presets_dir(); p = os.path.join(d, a.name + '.json')
    if os.path.exists(p):
        print('overwriting existing preset', p)
    j = build(chain['variants'][a.variant]); json.dump(j, open(p, 'w'), indent=4)
    print('wrote', p); print('plugin order:', ' -> '.join(j['input']['plugins_order']))
    if a.load:
        r = subprocess.run(['easyeffects', '-l', a.name], capture_output=True, text=True); print('loaded' if r.returncode == 0 else 'load failed: ' + r.stderr)


if __name__ == '__main__':
    main()
