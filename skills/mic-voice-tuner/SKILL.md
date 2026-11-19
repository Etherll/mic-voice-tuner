---
name: mic-voice-tuner
description: Measure a microphone and build a verified voice-processing chain for it (noise removal, EQ, gate, compressor, limiter) in Easy Effects on Linux or Equalizer APO on Windows. Use this whenever someone wants better microphone sound or mic settings, for example "best Easy Effects settings for my mic", "make my mic sound good on Discord / OBS / stream", "remove background noise, hum or keyboard clicks from my mic", "my mic is too quiet", "Equalizer APO mic preset", tuning a compressor, gate, EQ, DeepFilterNet, RNNoise or DPDFNet for voice, or comparing noise removal on vs off, even if they only ask for "good settings" and never mention measuring. Generic preset numbers are wrong for almost every mic; this skill replaces them with settings derived from the user's own recordings and checked on the real audio pipeline.
---

# Mic voice tuner

Good mic settings cannot be guessed. The right gain, EQ, gate threshold and compression depend on how loud this
person is at this mic, what their room noise is, how their mic colours their voice, and their pitch. A two-minute
recording reveals all of it. So the job is a loop:

**record -> analyze -> pick noise removal -> design -> apply -> verify on the real pipeline -> report**

The bundled scripts do the measuring and the math. Your part is running the loop, looking at the results with
judgment, and reporting honestly, including when a result is disappointing.

## Before you start

**Platform.** Decide which path applies and read its reference before step 5:
- Linux with PipeWire + Easy Effects: the whole loop is automated. Read `references/easyeffects.md`.
- Windows with Equalizer APO: measuring and design are identical; applying and verifying differ. Read `references/equalizer-apo.md`.
- Anything else (macOS, OBS filters, a hardware mixer): steps 1-4 still work. Hand over the values from `chain.json` as a
  settings table and say plainly that you could not verify them on their pipeline.

**Environment.** The scripts need Python 3.10+, ffmpeg, and the packages in `scripts/requirements.txt`:

```bash
python -m venv ~/.cache/mic-voice-tuner/venv        # or: uv venv ~/.cache/mic-voice-tuner/venv
~/.cache/mic-voice-tuner/venv/bin/pip install numpy scipy soundfile librosa matplotlib pyloudnorm numba
# optional but worth it: perceptual quality estimates (DNSMOS, NISQA), about 1.5 GB
~/.cache/mic-voice-tuner/venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
~/.cache/mic-voice-tuner/venv/bin/pip install onnxruntime speechmos torchmetrics requests
```

Without the optional packages everything still runs; you just lose the quality rows and noise-removal models are
ranked by a cruder rule. Say so in the report if that happened.

**Working folder.** Use a folder on a real disk (for example `~/mic-tune`), not `/tmp`. Captures are ~20 MB each, a
virtualenv with torch is over a gigabyte, and `/tmp` is often RAM-backed and fills up mid-run.

**Permission.** Installing system packages, restarting Easy Effects, and swapping the microphone out of the live
pipeline all affect the user's machine. Ask first unless they already told you to go ahead. Their existing presets
are theirs: add new ones, never overwrite or delete.

## The loop

### 1. Record (the user does this)

The capture is interactive, so the user must run it in their own terminal:

```bash
python <skill>/scripts/record.py --out ~/mic-tune/rec        # --list shows devices; Windows needs --device "name"
```

Seven short stages: silence, normal talking, loud, quiet, S/P sounds, keyboard only, talking while typing. Tell them
to sit as they normally do with the room as it normally is, then wait for them to say they are done.

The recording has to be **raw**. If it goes through existing processing, you measure that processing instead of the
mic. On Linux the script refuses the Easy Effects virtual source. On Windows, Equalizer APO and any driver
"enhancements" or automatic gain must be off for that device while recording (details in the APO reference).

If the user already has recordings, they need the stage names the scripts expect (`01_silence.wav`, `02_normal.wav`,
...). Silence and normal speech are required. Each missing stage costs something specific: without loud and quiet the
compressor cannot be fitted to their real range; without keyboard the gate ignores key clicks.

### 2. Analyze

```bash
python scripts/analyze.py --rec ~/mic-tune/rec --out ~/mic-tune/work
```

Read the printed findings, then look at `work/mel_raw.png` and `work/spectra_raw.png` yourself. You are checking the
recording is sane before trusting numbers built on it: speech visible in the speech stages, the silence stage really
silent, nothing clipped.

Tell the user the physical findings before any DSP, because no plugin beats fixing the source. A voice that arrives
30 dB too quiet has to be amplified 30 dB, and the room noise comes up with it; halving the distance to the mic buys
about 6 dB of voice for free. Clipping cannot be repaired afterwards. Offer to re-measure if they change something,
and carry on with what you have.

### 3. Choose noise removal

On Linux, benchmark whatever is installed against their own voice and noise:

```bash
python scripts/bench_nr.py --rec ~/mic-tune/rec --out ~/mic-tune/work      # takes as long as the recording, by design
```

It compares DPDFNet, DeepFilterNet and RNNoise where present, and keeps the winner as `work/nr/best.wav`. Do not just
read the winner line. Check that speech survived (`speech_change_db` near 0, small `speech_hf_change_db`) and what happened
to keyboard noise. Scores within about 0.05 of each other are a tie; break ties toward lower latency (RNNoise and
DeepFilterNet add roughly 10-20 ms, DPDFNet about 80 ms) or ask what the user cares about. If nothing is installed,
or a better model is one package away, the Easy Effects reference says what to install and what to restart.

On Windows this step cannot run; the APO reference covers the options.

### 4. Design

```bash
python scripts/design.py --rec ~/mic-tune/rec --work ~/mic-tune/work       # --target-lufs -17 --ceiling -2 by default
```

This writes `work/chain.json` with an `nr_on` variant (designed on the winning model's output) and an `nr_off` variant
(raw mic: steeper high-pass, notches for steady tones, deeper gate, deliberately lighter compression). It also stores
what the simulation predicts, which step 5 checks against reality.

Read the design before passing it along:
- Gain above about +30 dB means the mic is far too quiet at the source. It will work with noise removal on, but say so.
- A high-shelf pinned at its cap (+8 dB, +6 dB without noise removal) means the mic is duller than EQ should fully correct.
- A note that noise overlaps quiet speech means the gate cannot fully clean the NR-off chain. No gate setting fixes that, because the noise and the soft words sit at the same level.
- `deesser.recommended` is usually false, and that is a real result: see "Things that go wrong".

The default target of -17 LUFS suits calls and streams. Go quieter (-20) if another app applies its own automatic
gain, louder (-16) for podcast-style delivery.

### 5. Apply and verify

**Easy Effects**

```bash
python scripts/ee_preset.py --work ~/mic-tune/work --variant nr_on  --name "Mic NR on"
python scripts/ee_preset.py --work ~/mic-tune/work --variant nr_off --name "Mic NR off"
python scripts/ee_inject.py --rec ~/mic-tune/rec --out ~/mic-tune/work/cap_before.wav                  # current state, before loading anything
python scripts/ee_inject.py --rec ~/mic-tune/rec --out ~/mic-tune/work/cap_on.wav  --preset "Mic NR on"
python scripts/ee_inject.py --rec ~/mic-tune/rec --out ~/mic-tune/work/cap_off.wav --preset "Mic NR off"
python scripts/evaluate.py --rec ~/mic-tune/rec --work ~/mic-tune/work --keep-aligned \
    --capture "before=$HOME/mic-tune/work/cap_before.wav" --capture "NR off=$HOME/mic-tune/work/cap_off.wav" --capture "NR on=$HOME/mic-tune/work/cap_on.wav"
```

`ee_inject.py` plays the user's recording through the real effects chain in place of the microphone. Run injections
one at a time (there is one Easy Effects) and finish by loading the preset the user should end up with.

**Equalizer APO**

```bash
python scripts/apo_config.py --rec ~/mic-tune/rec --work ~/mic-tune/work --variant nr_off
```

This writes a config that works on a stock install (gain + EQ, gain capped so nothing clips), a full-chain config
that needs VST plugins, and the plugin settings. The APO reference explains installing, the plugins, and how to
verify with APO's own `Benchmark.exe` or a second recording.

**The verification rule.** Compare the measured table with `predicted` in `chain.json`. Loudness should land within
about 1.5 dB and silence should be close. A larger gap means some plugin is not doing what the design assumed. Find
out which (bypass one effect, inject again, compare) before reporting anything. Then look at `work/mel_compare.png`:
silence should go dark, speech should stay intact, and nothing should look chopped.

### 6. Report

Lead with what the user got, then the evidence. A shape that works:

```
<one or two sentences: what is loaded now and the headline before -> after numbers>

| | Before | NR off | NR on |          <- from evaluate.py: loudness, quiet-to-loud spread, noise in silence,
|---|---|---|---|                          keyboard, peaks, quality estimates

## Settings            <- every effect and value, so they can see and tweak it
## What I found        <- physical findings, why this noise-removal model, anything surprising
## Changes to your system   <- packages installed, apps restarted, presets added (and that old ones are untouched)
## Things to know      <- added latency, leftover noises, the app-side advice below, how to switch presets
```

Say that DNSMOS and NISQA are model estimates, not listening tests, and point the user at the aligned WAV files so
they can listen. If the NR-off variant measures worse than what they had, say so. Remind them to turn off noise
suppression and automatic gain in Discord, OBS, Teams and similar, or the voice gets processed twice.

## Things that go wrong, and why

- **Noise-removal plugins fed faster than real time produce garbage timing.** They process on a worker thread;
  offline rendering makes their output arrive with random delay and every comparison silently breaks.
  `bench_nr.py` runs them in real time for this reason. Do not "optimise" that away.
- **Easy Effects only notices a newly installed noise-removal model after a restart.** Until then it passes audio
  through untouched and logs "the selected model is not installed". If noise removal measures as doing nothing,
  check this first.
- **A plugin can ignore your model of it.** In the session this skill came from, a de-esser removed 6-7 dB of every
  "s" no matter where its threshold was set, quietly undoing the EQ. It was only caught by injecting with it on and
  off. This is why a de-esser is not added by default, and why any effect you add by hand deserves an on/off measurement.
- **Compression lifts noise.** Each dB of gain reduction on speech is a dB the room noise rises between words. With
  noise removal on that is free; without it, heavy compression makes the result worse than no processing. The NR-off
  design is intentionally gentler, and its louder-to-quieter spread is wider as a result.
- **Neural denoisers are level-sensitive.** A very quiet mic can sit below what a model was trained on, so it barely
  removes anything. The benchmark tries each model with and without input gain; the preset applies the winning gain.
- **Do not leave the user on a test preset or with the mic unlinked.** `ee_inject.py` re-links the microphone even when
  it fails, but check the final state yourself: the intended preset loaded, the mic feeding it.
- **Recordings are the user's voice.** Keep them on their machine. Do not upload them, attach them to anything, or
  copy them into a shared folder.

## Reference files

- `references/easyeffects.md`: installing noise-removal models, restarting, presets, how injection works, measured plugin behaviour.
- `references/equalizer-apo.md`: installing on a mic, config syntax, VST plugins, verification with Benchmark.exe, limits.
- `references/method.md`: what each measurement means, how each design decision is made, how to read the metrics, a worked example.
