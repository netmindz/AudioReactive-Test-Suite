# AudioReactive-Test-Suite

An implementation-independent harness for comparing WLED Audio Sync v2 output.
The current CLI provides strict v2 capture decoding, saved-capture comparison,
time-based pairing, settings preflight and versioned JSON reports.

## Quick start

The suite has no runtime dependencies beyond Python 3.11+:

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
python3 -m audioreactive_suite compare \
  --reference reference.capture.json \
  --candidate candidate.capture.json \
  --case tone.case.json \
  --output result.report.json
```

`compare` returns `0` for a pass, `2` for a declared threshold failure and `3`
for invalid or inconclusive evidence. A capture requires an explicit
`metadata.identity`, and each artifact may contain packets from exactly one
source endpoint. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for
delivery scope and [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md) for the comparison
contract.

Capture a bounded UDP stream with:

```bash
python3 -m audioreactive_suite capture \
  --port 11988 \
  --duration 10 \
  --metadata '{"identity":"reference","settings":{"fft_size":512}}' \
  --output reference.capture.json
```

## Sample audio fixtures

Usable 22,050 Hz mono PCM WAV fixtures are committed in
[`fixtures/audio`](fixtures/audio). They include silence; low-to-high single
tones; a multi-tone signal; a logarithmic sweep; a click train; amplitude
modulation; stepped levels; and deterministic white and pink noise. Their exact duration,
frequency, amplitude and noise seeds are in
[`fixtures/audio/manifest.json`](fixtures/audio/manifest.json).

Regenerate the identical fixture set with:

```bash
python3 -m audioreactive_suite generate-samples --output-dir fixtures/audio
```

## Running a real comparison

The `run` command opens one UDP listener **before** starting an external audio
player, separates traffic by the two explicit sender endpoints, then writes
both raw captures, `report.json` and `summary.txt` into the work directory.
The player is supplied as a JSON argument array; `{audio}` is replaced with the
absolute fixture path and no shell is invoked.

```bash
python3 -m audioreactive_suite run \
  --case fixtures/cases/tone_688hz.json \
  --audio fixtures/audio/tone_688hz.wav \
  --playback-command '["aplay","{audio}"]' \
  --host 0.0.0.0 --port 11988 \
  --reference-endpoint 192.0.2.10:11988 \
  --candidate-endpoint 192.0.2.11:11988 \
  --reference-metadata '{"identity":"pinned-wled","settings":{"sample_rate_hz":22050,"fft_size":512,"fft_window":"blackman_harris","fft_scaling":"sqrt"}}' \
  --candidate-metadata '{"identity":"candidate","settings":{"sample_rate_hz":22050,"fft_size":512,"fft_window":"blackman_harris","fft_scaling":"sqrt"}}' \
  --work-dir artifacts/tone-688
```

The supplied tone, click and sweep cases are starting profiles, not universal
WLED tolerances. Calibrate thresholds against repeated captures for the pinned
firmware, input routing and settings before treating a result as conformance.
Cases estimate candidate latency using envelope correlation when their
`alignment.mode` is `auto`; the report includes the signed offset, confidence,
search range and ambiguity decision. An ambiguous estimate makes the result
inconclusive. The `stimulus.warmup_seconds` and `measurement_seconds` fields
select the score window relative to the recorded playback start, not the first
received packet.

The report separates per-band temporal shape correlation and normalized profile
error from level ratio in dB. When a case references an audio fixture, it also
ranks simplified FFT-band fingerprints (including mapping, sample-rate, FFT-size,
pink-correction and scaling variants). This is a diagnostic approximation, not a
bit-accurate or hardware-validated MoonModules reference; tied or unmatched
fingerprints should not be treated as proof of a specific implementation bug.

Cases cover tone, sweep, clicks, multi-tone, amplitude modulation, stepped levels,
and seeded white/pink noise. Unknown or mismatched required settings,
unsuccessful playback, mixed sources, ambiguous latency, or insufficient overlap
result in `inconclusive`, never a pass. Thresholds still require calibration from
maintainer hardware captures for a pinned profile.