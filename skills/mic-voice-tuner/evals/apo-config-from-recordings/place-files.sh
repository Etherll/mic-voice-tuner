#!/usr/bin/env bash
# Builds the synthetic recordings (needs python3 with numpy, scipy, soundfile).
set -eu
python3 "$(dirname "$0")/../make_synth_rec.py" rec
