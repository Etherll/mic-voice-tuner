# Easy Effects (Linux, PipeWire)

## Contents
1. Checks before starting
2. Noise-removal models: what exists, how to install, the restart
3. Presets: where they live and how loading behaves
4. How injection works and what it does to the microphone
5. Measured plugin behaviour (trust these over intuition)
6. Things to tell the user at the end

## 1. Checks before starting

```bash
easyeffects --version
pgrep -a easyeffects                       # must be running for presets to load and for injection
pactl get-default-source                   # the hardware mic, not easyeffects_source
command -v pw-link pw-play ffmpeg          # all three are needed for injection
ls /usr/lib/ladspa ~/.ladspa 2>/dev/null   # which noise-removal plugins are installed
strings "$(command -v easyeffects)" | grep -c -i dpdfnet    # 0 = this build has no DPDFNet support
```

Flatpak installs are sandboxed: they do not see LADSPA plugins installed on the host, and their presets live under
`~/.var/app/com.github.wwmm.easyeffects/`. `ee_preset.py` looks there too. If the model the benchmark picked is not
visible to the Flatpak build, fall back to the best one that is (RNNoise is built in).

## 2. Noise-removal models

| Model | Easy Effects effect | Needs | Added latency | Notes |
|---|---|---|---|---|
| RNNoise | Noise Reduction (`rnnoise#0`) | built in | ~10-20 ms | light, decent on steady noise |
| DeepFilterNet 3 | Deep Noise Remover, model DeepFilterNet | `libdeep_filter_ladspa.so` | ~10-20 ms | level-sensitive: on a quiet mic it needs input gain |
| DPDFNet | Deep Noise Remover, model DPDFNet | `libdpdfnet_ladspa.so` from HushMic, Easy Effects 8.3+ | ~80 ms | strongest in the reference session, not level-sensitive |

Installing:
- DeepFilterNet: distro package if there is one (Arch: `deepfilternet-ladspa`), otherwise the `libdeep_filter_ladspa`
  build from the DeepFilterNet GitHub releases placed in `/usr/lib/ladspa` or `~/.ladspa`.
- DPDFNet: the HushMic project (Arch AUR: `hushmic-bin`; otherwise its GitHub releases). It is a third-party prebuilt
  binary that Easy Effects' own README points to. Tell the user that is what it is before installing.

**The restart.** Easy Effects checks which models exist when it starts. After installing one, restart it, or the effect
passes audio straight through and the journal shows `the selected model is not installed`:

```bash
easyeffects -q
systemd-run --user --collect easyeffects          # or ask the user to reopen it
journalctl --user -b --no-pager -o cat | grep -i "not installed" | tail -3
```

Audio that other apps route through Easy Effects blips for a second during the restart. Mention it.

## 3. Presets

- Input presets are JSON files in `~/.local/share/easyeffects/input/` (8.x) or `~/.config/easyeffects/input/` (7.x).
- `easyeffects -l "Name"` loads one and replaces the whole input chain. `easyeffects -p` lists presets,
  `easyeffects -a input` prints the one currently loaded. Check that after loading: a load issued right after another
  command occasionally does not take, and you would then verify the wrong chain.
- The settings files under `~/.config/easyeffects/db/` are written lazily. They do not reliably show what is loaded.
- A preset that omits an enum-like key (a mode, a filter type, the noise-removal model) keeps whatever value was
  active before. `ee_preset.py` always writes them; do the same if you edit presets by hand.
- Many setups have a "fallback preset" for device changes. If the user has one, Easy Effects may switch back to it when
  the mic reconnects. Point this out instead of changing it silently.

## 4. Injection

The input effects are only linked into the graph while something records from `easyeffects_source`. `ee_inject.py`:
1. loads the preset if asked, and starts an ffmpeg recorder on `easyeffects_source`;
2. finds the link from the hardware mic into the first effect (`ee_sie_*`);
3. starts a private `pw-play` stream (unique node name, autoconnect off), unlinks the mic, links the stream in its place;
4. when playback ends, re-links the mic (also on failure) and stops the recorder.

Consequences worth knowing:
- For those two minutes the real microphone is not reaching Easy Effects. Anyone on a call would notice.
- Never run two injections at once. There is one pipeline, and streams are linked by name.
- `evaluate.py` prints a WARNING when a capture does not track the input. Believe it: that column is not a measurement.
- "No microphone -> Easy Effects link found" means Easy Effects is not running, has no input device, or the input
  chain has no effects at all.

## 5. Measured plugin behaviour

From feeding known audio through the real plugins (Easy Effects 8.3, LSP 1.2, Calf 0.90):

- **Equalizer, mode "APO (DR)":** bells and shelves match the textbook biquad to within 0.1 dB. This is what the design assumes.
- **Equalizer high-pass, mode "BWC (BT)":** steeper than its label suggests. At 75 Hz with slope x4 it measured -3 dB at
  75 Hz, -23 dB at 60 Hz and -39 dB at 50 Hz. Good for hum; the simulation slightly under-predicts the cut.
- **Compressor, gate, limiter (LSP):** real loudness landed within about 1 dB of the simulation.
- **Limiter:** sample peaks hold the threshold exactly; true peaks overshoot by up to about 1 dB. Hence the -2 dB default ceiling.
- **Deep Noise Remover with DPDFNet:** silence becomes digital silence; "Process as Mono" halves CPU for a mono mic.
- **De-esser (Calf), Split mode:** removed 6-7 dB from every sibilant regardless of threshold (-21 and -12 dB gave the same
  loss) and 12 dB around 8 kHz from the average spectrum. Leave it out unless an on/off injection shows it behaving.

## 6. Tell the user

- How to switch: the Presets menu, or `easyeffects -l "Mic NR on"` / `"Mic NR off"`.
- The added latency of the model you chose, and that it only matters if they monitor themselves.
- To disable noise suppression, echo cancellation and automatic gain in Discord, OBS, Teams and friends.
- That their old presets were not touched.
