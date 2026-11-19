# Method: what is measured and how each decision is made

Read this when a result looks odd, when the user asks "why this value", or when you need to adapt the design by hand.

## The recording stages

| Stage | Used for |
|---|---|
| silence | noise floor, hum and fan tones, the gate's "must be silent" level |
| normal | reference loudness, voice spectrum, pitch, the gain |
| loud, quiet | the user's real dynamic range, which sets the compressor |
| sibilance | whether "s" sounds are too hot after EQ |
| keyboard | what the noise remover lets through and what the gate must close on |
| speech + typing | the hard case, mostly for listening and the spectrogram |

The first 0.3 s of each file is dropped, and the first 3 s of silence is ignored when judging noise removal, because
capture start-up and model adaptation are not representative.

## Decisions

**Gain.** Chosen so normal speech sits near -22 LUFS before the compressor. That puts thresholds mid-range for the
plugins and leaves headroom for loud speech.

**High-pass.** 0.72 x the median voice pitch, clamped to 60-120 Hz. It removes rumble, plosive thump and desk bumps
without thinning the voice's fundamental. 4th order with noise removal on; steeper with it off because nothing else
removes mains hum.

**Tonal EQ.** The voice's 1/3-octave spectrum (loud frames only) is compared with a long-term average speech spectrum.
Three bands are fitted to about 60 % of the difference: a low-mid bell (180-400 Hz, -4..+3 dB), a mid bell
(700-1400 Hz, +-4 dB) and a high shelf (3.5-6.5 kHz, -4..+8 dB, or at most +6 dB without noise removal, where a treble boost lifts hiss
and key clicks along with the voice). Partial correction and caps are deliberate: the
reference is an average of many talkers on a flat mic at close range, one two-minute take is noisy evidence, and every
dB of boost also boosts whatever noise or artefacts live there. In testing, perceptual estimates barely moved between
"no tonal EQ" and "full correction", and were lowest for a hand-made EQ with many narrow bands, so the fit stays at three broad bands.

**Notches (noise removal off only).** Tones that are present in every third of the silence stage, 15 dB above their
surroundings, between 85 and 1200 Hz. Tones that wander get a wider notch. Tones that come and go are skipped: a notch
for an absent tone only removes voice.

**Gate.** The level detector listens above 500 Hz, where speech is strong and room noise, hum and desk thumps are weak,
which roughly doubles the separation between "noise" and "quiet speech". The gate is fully closed just above the
loudest thing that must be silent, opens over a 5-6 dB zone, and only attenuates 20-30 dB rather than muting, so word
tails fade instead of chopping. If the opening point would cut into quiet speech, it is capped and a note is written.

**Compressor.** A grid of thresholds and ratios is simulated on the user's own quiet/normal/loud speech. With noise
removal on, the gentlest setting that brings the quiet-to-loud spread to 6 dB or less wins. With it off, ratio is
limited to 2:1 and median gain reduction to 3 dB, because compression raises the noise between words by the amount
it turns speech down. Make-up gain then sets normal speech to the target loudness.

**Limiter.** Ceiling -2 dBFS with 5 ms look-ahead. It catches consonant transients that are faster than the
compressor's attack. -2 rather than -1 because true peaks overshoot sample peaks by about a decibel.

**De-esser.** Recommended only if sibilant peaks land within 3 dB of vowel peaks after EQ and compression. Dull mics,
which need the high-shelf boost, usually end up with natural sibilance and no need for one.

## Reading the numbers

- **LUFS**: perceived loudness. -17 is a comfortable talk level for calls and streams. Differences under 1 dB are not audible.
- **Noise in silence (dBFS)**: -60 is faintly audible on headphones next to speech at -17 LUFS; -80 and below is inaudible;
  values below -120 mean digital silence.
- **Quiet-to-loud spread**: how much the level swings between the user's softest and loudest speech. Untreated voices
  are often 12-18 dB; 4-6 dB sounds controlled without sounding squashed.
- **DNSMOS (overall / speech / background) and NISQA MOS**: neural estimates of how listeners would rate the audio, 1-5.
  They are useful for ranking alternatives on the same recording, and they are not listening tests. Differences
  under about 0.05 are noise. "At equal loudness" versions remove the effect of level so only tone, noise and artefacts count;
  the plain versions also punish a mic that is simply too quiet.
- **speech_change_db / speech_hf_change_db** (noise-removal benchmark): how much the model altered speech where speech
  clearly dominates. Near 0 is good; a few dB negative in the highs means it is dulling the voice.

## Worked example

A USB dynamic mic used from too far away, in a room with mains hum and PC fans:

- Raw: speech at -47 LUFS (hardware gain already at maximum), noise floor -63 dBFS, highs 8-17 dB below reference above 5 kHz.
- Noise removal, estimates at equal loudness: raw 2.74 DNSMOS overall; RNNoise and DeepFilterNet about 3.3 (DeepFilterNet
  needed +24 dB input gain to do anything in silence); DPDFNet 3.49 and digital silence in pauses.
- Design with noise removal on: +24 dB gain, high-pass 75 Hz, +4 dB at 1 kHz, +8 dB shelf at 4.7 kHz, gate at -50 dB
  (20 dB range), compressor -33 dB 3:1 with +16 dB make-up, limiter -2 dB.
- Measured on the real pipeline: normal speech -44 -> -16.6 LUFS, quiet-to-loud spread 16.4 -> 4.8 dB, noise in silence
  -63 -> -130 dBFS, NISQA 3.08 -> 4.38. Prediction and measurement agreed within about 1 dB.
- Without noise removal the same mic only reached NISQA 2.95: usable loudness, but the room noise came up with the voice.
  The honest advice there was to get closer to the mic.
