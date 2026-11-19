# Equalizer APO (Windows)

**Status of this path.** The measuring and design scripts are plain Python and behave the same on Windows. The filter
math is verified: Equalizer APO's biquads are the same textbook formulas the design uses. The Windows-side steps below
come from Equalizer APO's documentation and source code and were not executed while writing this skill. Treat each
step as something to confirm on the user's machine, and say in the report what you actually verified.

## Contents
1. What Equalizer APO can and cannot do
2. Getting a raw recording on Windows
3. Installing on the microphone and applying the config
4. Config syntax used by this skill
5. The full chain: VST plugins
6. Verifying
7. Other routes to noise removal

## 1. What it can and cannot do

Natively: gain (`Preamp`), parametric filters, delay, convolution. That covers the design's gain, high-pass, tonal EQ and
notches exactly. It has **no** noise removal, gate, compressor or limiter. Those need VST plugins, and without a
limiter any gain boost can clip. So `apo_config.py` writes two configs:

- `mic_voice_native.txt`: gain + EQ only. The gain is capped so the loudest speech the user recorded stays below
  -3 dBFS. Safe on a stock install, but it will be quieter than the design target and it removes no noise.
- `mic_voice_full.txt`: the design gain with markers for four VSTs. Only correct once a limiter is in place.

Be straightforward with the user about this gap. If the mic is very quiet, native-only cannot reach a normal loudness
safely; the honest options are the VST chain, getting closer to the mic, or raising hardware gain.

## 2. Raw recording

```powershell
ffmpeg -hide_banner -list_devices true -f dshow -i dummy          # or: python record.py --list
python record.py --out rec --device "Microphone (USB Audio Device)"
```

While recording, the capture must be unprocessed:
- Equalizer APO off for that device: empty `config.txt` temporarily, or use the power toggle in the Configuration Editor.
- Windows Sound settings -> the mic -> turn off "Audio enhancements" and any automatic gain control.
- Set the mic's format to 48000 Hz (Sound settings -> the mic -> Advanced). RNNoise-based plugins require 48 kHz.
- Close apps that take exclusive control of the mic.

## 3. Installing and applying

1. Install Equalizer APO. In the Configurator, open the **Capture devices** tab and tick the microphone. Reboot.
   Re-run `C:\Program Files\EqualizerAPO\Configurator.exe` later to add devices.
2. Copy `mic_voice_native.txt` into `C:\Program Files\EqualizerAPO\config\`.
3. Add one line at the **end** of `config.txt` in that folder: `Include: mic_voice_native.txt`. Keeping the user's
   existing lines above it preserves their playback EQ; the file's `Stage: capture` line keeps it off playback devices.
4. Changes apply immediately. If nothing changes, check the mic's Enhancements/Advanced tab ("Enable audio
   enhancements" must be allowed for APOs to run) and the log at
   `C:\Windows\ServiceProfiles\LocalService\AppData\Local\Temp\EqualizerAPO.log`.

Pass `--device "words from the mic name"` to `apo_config.py` if the user has several capture devices with APO installed
and only one should be processed. The default `Device: all` with `Stage: capture` applies to every capture device that
has the APO.

## 4. Config syntax used

Verified against the Equalizer APO configuration reference:

```
Device: all                                 # or words that must all appear in the device/connection name
Stage: capture                              # input devices only have this stage
Channel: all
Preamp: 12 dB                               # plain gain; usually negative in playback configs, positive is allowed
Filter: ON HPQ Fc 75 Hz Q 0.5412            # 2nd-order high-pass with Q; two of them (0.5412, 1.3066) = 4th-order Butterworth
Filter: ON PK Fc 1000 Hz Gain 4 dB Q 1.2    # bell
Filter: ON HSC Fc 4700 Hz Gain 8 dB Q 0.7071   # high-shelf, centre frequency, with Q (needs 1.2.1+)
Filter: ON NO Fc 230.3 Hz Q 15              # notch
Include: other_file.txt
```

Lines are processed top to bottom. Long lines are fine; unknown lines are skipped silently, so a typo simply does nothing.
That is another reason to verify.

## 5. The full chain: VST plugins

Equalizer APO loads **VST2** plugins whose bitness matches the install (64-bit on 64-bit Windows). Add them with the
Configuration Editor (the "+" menu -> Plugins -> VST plugin), at the positions marked in `mic_voice_full.txt`. The editor
writes the `VSTPlugin:` line for you. Its format, from the source, is quoted key/value pairs:

```
VSTPlugin: Library "C:\path\plugin.dll" ChunkData "<saved state>" "<param name>" <value> ...
```

Do not hand-write the saved-state part; set the controls in the plugin's own window from `apo_vst_settings.md`.

What people commonly use (none of these were tested while writing this skill; confirm each loads and behaves):
- Noise removal: the RNNoise VST2 from the `werman/noise-suppression-for-voice` project (mono or stereo DLL).
- Gate and compressor: Cockos ReaPlugs (ReaGate, ReaComp), or any VST2 gate/compressor with a sidechain high-pass.
- Limiter: any 64-bit VST2 brickwall limiter. It goes last.

Plugins run inside the Windows audio engine, where some refuse to initialise. If one fails, the log names it. Remove
it rather than leaving a broken line, and never run the full config without a working limiter.

## 6. Verifying

**Offline, through the real engine.** Equalizer APO ships `Benchmark.exe`, which pushes a WAV through the installed
configuration. Its options (from the source; run `Benchmark.exe -h` to confirm on the installed version):
`-i/--input`, `-o/--output`, `--devicename` (default `Benchmark`), `--connectionname` (default `File output`), `--nopause`, `-v`.

`apo_config.py` writes two helpers for this: `raw_concat_48k.wav` (the user's recording as one file) and
`mic_voice_benchmark_test.txt` (the same filters behind `Device: Benchmark`, because Benchmark is not a capture device).

```powershell
# temporarily make config.txt contain only:  Include: mic_voice_benchmark_test.txt
cd "C:\Program Files\EqualizerAPO"
.\Benchmark.exe -i C:\path\raw_concat_48k.wav -o C:\path\apo_out.wav --nopause -v
python evaluate.py --rec rec --work work --capture "APO=C:\path\apo_out.wav"
# then restore config.txt
```

If the output equals the input, the config was not matched: `-v` prints what was parsed. To test the VST chain the same
way, add the plugins to that test file in the editor.

**Live.** Enable the real config, record a new take with `record.py` through the processed mic, and compare:

```powershell
python record.py --out rec_after --device "Microphone (USB Audio Device)"
python evaluate.py --rec rec --work work --respoken "APO=rec_after"
```

It is a different performance, so loudness, noise in silence, peaks and the quality estimates are comparable, sample-level
detail is not.

Either way, compare with `predicted` in `chain.json` (for native-only, with the loudness `apo_config.py` printed).

## 7. Other routes to noise removal on Windows

If the VST route does not work out, noise removal can live outside Equalizer APO: NVIDIA Broadcast or similar GPU
vendor tools, or the suppression built into the user's chat/recording app. In that case use `mic_voice_native.txt` for
tone and level, let the other tool remove noise, and tell the user to keep only **one** noise suppressor active.
