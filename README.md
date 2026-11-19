# mic-voice-tuner

An agent skill that tunes your microphone from a recording of your own voice. It supports Easy Effects on Linux and Equalizer APO on Windows.

Preset numbers from a forum post were tuned for someone else's mic, room and voice. This skill has you record about two minutes of audio, measures it, and builds the chain (noise removal, EQ, gate, compressor, limiter) from what it finds. On Linux it then plays your recording through the real Easy Effects pipeline and measures what comes out, so you end up with before and after numbers and audio files you can compare by ear.

## Install

```bash
npx skills add Etherll/mic-voice-tuner
```

For claude.ai, download `mic-voice-tuner.skill` from the [releases page](https://github.com/Etherll/mic-voice-tuner/releases) and upload it in Settings under Capabilities.

Then ask for what you want: "tune my mic for Discord", "best Easy Effects settings for my mic", "make an Equalizer APO config for my mic".

## Requirements

- Python 3.10 or newer, and ffmpeg
- Linux: PipeWire and Easy Effects. Developed against Easy Effects 8.3.2. The preset writer also looks in the 7.x preset folder, but I have not tested 7.x.
- Windows: Equalizer APO
- Optional: about 1.5 GB of disk for the quality estimates (DNSMOS and NISQA need PyTorch). Everything else runs without them.

## What happens

1. You run a guided recorder in your own terminal. It takes seven short stages: silence, normal talking, loud, quiet, S and P sounds, keyboard only, and talking while typing.
2. The agent measures your level, noise floor, hum and fan tones, voice pitch, and how your spectrum compares with average speech. If something is cheaper to fix at the mic than in software, such as sitting too far away, it tells you before touching any effect.
3. On Linux it runs each noise removal model you have installed (RNNoise, DeepFilterNet, DPDFNet) on your recording and picks one.
4. It designs two chains, one with noise removal and one without, and writes them as Easy Effects presets or an Equalizer APO config. Your existing presets are left alone.
5. On Easy Effects it feeds your recording through the live effects chain in place of the microphone, records the output, and compares it with what the design predicted.

The scripts never upload your recordings. The optional quality models download their weights the first time they run.

## Results on the mic it was built with

A USB mic used from too far away, in a room with mains hum and PC fans. Each column is the same two-minute recording played through Easy Effects.

| | Raw mic | NR off | NR on |
|---|---|---|---|
| Normal speech | -47.2 LUFS | -18.2 LUFS | -17.2 LUFS |
| Quiet-to-loud spread | 15.2 dB | 7.6 dB | 5.2 dB |
| Noise in silence | -62.7 dBFS | -66.6 dBFS | -117.7 dBFS |
| Keyboard peak below speech peak | 25.5 dB | 8.5 dB | 34.5 dB |
| NISQA estimate (1 to 5) | 2.58 | 2.85 | 4.38 |

The noise removal here was DPDFNet, which won the benchmark on this voice. Without noise removal the chain fixes loudness and evens out the level, but the room noise comes up with the voice and key clicks end up much closer to it. With a mic this quiet at the source, use noise removal or move closer.

NISQA is a neural network's estimate of listening quality. Treat it as a way to rank options and listen to the files as well.

## Equalizer APO status

I have not run the Windows side. The measuring and design scripts are plain Python, and the filter math is the same math that was checked against Easy Effects. The install steps, the `Benchmark.exe` check and the VST notes come from Equalizer APO's documentation and source code.

Equalizer APO has no built-in noise removal, gate, compressor or limiter. Without VST plugins you get gain and EQ only, with the gain capped so your loudest speech cannot clip. The skill also writes a full-chain config and a settings sheet for the plugins if you want to add them.

If you try it on Windows, please open an issue and tell me what broke.

## Other things to know

- DPDFNet adds about 80 ms of latency in Easy Effects. RNNoise and DeepFilterNet add roughly 10 to 20 ms.
- Each verification run disconnects your real microphone from Easy Effects for about two minutes. Don't run it during a call.
- Easy Effects has to be restarted after you install a new noise removal model, or the effect passes audio through untouched.
- Turn off noise suppression and automatic gain in Discord, OBS and similar apps afterwards, so your voice is not processed twice.

## What's in the repo

```
skills/mic-voice-tuner/
  SKILL.md      the workflow the agent follows
  scripts/      recorder, analysis, noise removal benchmark, design, preset and config writers, verification
  references/   Easy Effects notes, Equalizer APO notes, how each setting is decided
  evals/        three test prompts with graders, and a generator for synthetic test recordings
```

## License

MIT
