#!/usr/bin/env python3
"""Play the raw recordings through the REAL Easy Effects input pipeline and record what comes out.

  python ee_inject.py --rec rec --out work/cap_nr_on.wav --preset "Mic NR on"
  python ee_inject.py --rec rec --out work/cap_before.wav            # whatever is loaded right now

Why: plugin models are approximations. Feeding identical audio through the actual plugins is the only way to
compare settings fairly and to catch surprises (a de-esser cutting 7 dB too much, a model that is not installed).

How: while a recorder is attached to easyeffects_source, the microphone's link into the first effect is swapped
for a private pw-play stream, then swapped back. The mic is disconnected from Easy Effects for the duration
(about 2 minutes) and is always re-linked on exit. Requires PipeWire tools (pw-link, pw-play) and ffmpeg.
"""
import argparse, os, sys, subprocess, time, tempfile
import numpy as np, soundfile as sf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from micdsp import load_all, SR


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def ee_input_links():
    """[(mic_port, ee_port)] for links from a non-Easy-Effects source into the first input effect (ee_sie_*)."""
    out, cur = [], None
    for line in sh('pw-link', '-l').splitlines():
        if line and not line[0].isspace():
            cur = line.strip()
        elif '|->' in line and cur:
            dst = line.split('|->')[1].strip()
            if dst.startswith('ee_sie_') and not cur.startswith(('ee_', 'easyeffects')):
                out.append((cur, dst))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rec', required=True); ap.add_argument('--out', required=True); ap.add_argument('--preset')
    a = ap.parse_args()
    x, _ = load_all(a.rec); dur = len(x) / SR + 3
    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False, dir=os.path.dirname(os.path.abspath(a.out))).name
    sf.write(tmp, np.concatenate([np.zeros(3 * SR), x]), SR, subtype='PCM_24')   # 3 s lead-in covers the link swap
    node = f'mvt_inject_{int(time.time())}'
    subprocess.run(['pkill', '-f', 'node.name = mvt_inject_'], capture_output=True)   # a stale player would steal the link by name
    if a.preset:
        r = subprocess.run(['easyeffects', '-l', a.preset], capture_output=True, text=True)
        if r.returncode:
            sys.exit('could not load preset: ' + r.stderr)
        time.sleep(2.5)
    rec = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'pulse', '-i', 'easyeffects_source', '-t', str(dur + 6),
                            '-ac', '2', '-ar', str(SR), '-c:a', 'pcm_f32le', a.out], stdin=subprocess.DEVNULL)
    time.sleep(2.0)                                # the input chain is only linked while something records from it
    links = ee_input_links(); play = None
    try:
        if not links:
            sys.exit('No microphone -> Easy Effects link found. Is Easy Effects running with an input device and at least one input effect?')
        play = subprocess.Popen(['pw-play', '--target', '0', '-P', '{ node.name = %s node.autoconnect = false media.class = Stream/Output/Audio }' % node, tmp])
        port = None
        for _ in range(25):
            time.sleep(0.2)
            ports = [l.strip() for l in sh('pw-link', '-o').splitlines() if l.strip().startswith(node + ':')]
            if ports:
                port = ports[0]; break
        if not port:
            sys.exit('injection stream did not appear in PipeWire')
        for mic, dst in links:
            subprocess.run(['pw-link', '-d', mic, dst], capture_output=True)
            r = subprocess.run(['pw-link', port, dst], capture_output=True, text=True)
            if r.returncode:
                print('link warning:', r.stderr.strip())
        print(f'injecting {dur:.0f} s through', ', '.join(sorted({d.split(":")[0] for _, d in links})), '...')
        play.wait(timeout=dur + 60)
        time.sleep(1.5)
    finally:
        if play and play.poll() is None:
            play.kill()
        for mic, dst in links:
            subprocess.run(['pw-link', mic, dst], capture_output=True)      # give the user their microphone back
        try:
            rec.wait(timeout=30)
        except subprocess.TimeoutExpired:
            rec.kill()
        os.remove(tmp)
    print('wrote', a.out)


if __name__ == '__main__':
    main()
