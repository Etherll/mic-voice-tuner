#!/usr/bin/env python3
"""Guided raw-microphone capture: 7 short stages, about 2 minutes. Run it in a real terminal.

  python record.py --list                 # show capture devices
  python record.py --out rec              # Linux: uses the default source
  python record.py --out rec --device "Microphone (USB Audio Device)"   # Windows needs the device name

The capture must be RAW: bypass Easy Effects / Equalizer APO / vendor "enhancements" while recording,
otherwise the measurements describe the old processing instead of the microphone.
Needs ffmpeg on PATH. Uses PulseAudio/PipeWire on Linux and DirectShow on Windows.
"""
import argparse, os, platform, subprocess, sys, time

STAGES = [
    ('01_silence', 15, 'STAY SILENT. Do not move, type or breathe into the mic.', ''),
    ('02_normal', 35, 'TALK NORMALLY, like on a call. Normal distance and volume. Read this, keep talking if you finish early (any language is fine):',
     '  The quick brown fox jumps over the lazy dog near the quiet river bank.\n'
     '  Yesterday I spent about three hours fixing a problem that turned out to be very simple.\n'
     '  My favourite thing about weekends is having enough time to cook something properly.\n'
     '  Usually I start with coffee, check my messages, and then plan the rest of the day.\n'
     '  If you can hear me clearly, the microphone settings are probably working fine.'),
    ('03_loud', 12, 'BE LOUD: the loudest you ever get on this mic. Laugh, shout, excited voice.', ''),
    ('04_quiet', 12, 'TALK QUIETLY: soft, low-energy, late-night voice. Same distance.',
     '  I am speaking quietly now so we can measure the softest voice level I actually use.'),
    ('05_sibilance', 15, 'READ THIS at normal volume (S and P sounds):',
     '  She sells seashells by the seashore. Six slippery snakes slide silently south.\n'
     '  Peter Piper picked a peck of pickled peppers. Big bad Bobby bought a better bat.\n'
     '  Sixty-six sizzling sausages. Pop the purple paper packet, please.'),
    ('06_keyboard', 12, 'DO NOT TALK. Type on your keyboard and click your mouse like you normally do.', ''),
    ('07_speech_typing', 18, 'TALK NORMALLY WHILE TYPING and clicking at the same time.', ''),
]
WIN = platform.system() == 'Windows'


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors='replace')


def list_devices():
    if WIN:
        r = sh(['ffmpeg', '-hide_banner', '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'])
        return [l.split('"')[1] for l in r.stderr.splitlines() if '(audio)' in l and '"' in l]
    r = sh(['pactl', 'list', 'sources', 'short'])
    return [l.split('\t')[1] for l in r.stdout.splitlines() if '\t' in l and '.monitor' not in l]


def default_device():
    if WIN:
        return None
    d = sh(['pactl', 'get-default-source']).stdout.strip()
    return d or None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default='rec')
    ap.add_argument('--device')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--only', help='comma-separated stage names to (re)record')
    a = ap.parse_args()
    if a.list:
        print('\n'.join(list_devices()) or 'no capture devices found'); return
    dev = a.device or default_device()
    if not dev:
        sys.exit('pass --device NAME (see --list)')
    if 'easyeffects' in dev.lower():
        sys.exit(f'"{dev}" is the PROCESSED Easy Effects source. Pass the hardware mic with --device (see --list).')
    os.makedirs(a.out, exist_ok=True)
    print('Mic measurement capture\n  device:', dev, '\n')
    print('Sit exactly as you normally do, mic where it normally is, room as it normally is')
    print('(AC / fans / PC on if they usually are). Do not touch the mic gain during the run.')
    input('\nPress ENTER to begin... ')
    only = set(a.only.split(',')) if a.only else None
    for name, secs, title, text in STAGES:
        if only and name not in only:
            continue
        print('\n' + '=' * 70 + f'\nStage {name}  ({secs} s)\n\n{title}\n')
        if text:
            print(text + '\n')
        input('Press ENTER when ready... ')
        for i in (3, 2, 1):
            print(f'\r  starting in {i} ', end='', flush=True); time.sleep(1)
        src = ['-f', 'dshow', '-i', 'audio=' + dev] if WIN else ['-f', 'pulse', '-i', dev]
        out = os.path.join(a.out, name + '.wav')
        p = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *src, '-t', str(secs),
                              '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s24le', out], stdin=subprocess.DEVNULL)
        print('\r  ** RECORDING - go! **          ')
        for s in range(secs, 0, -1):
            print(f'\r  {s:2d} s left ', end='', flush=True); time.sleep(1)
        p.wait()
        ok = os.path.exists(out) and os.path.getsize(out) > 48000
        print('\r  saved ' + out if ok else '\r  FAILED to record ' + out + ' (wrong device name? mic in exclusive use?)')
        if not ok:
            sys.exit(1)
    print('\nAll done. Recordings are in', os.path.abspath(a.out), '\nGo back to Claude and say: done')


if __name__ == '__main__':
    main()
