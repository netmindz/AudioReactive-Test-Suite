# AudioReactive test harness: technical specification

This is a proposed design for the requirements in [REQUIREMENTS.md](REQUIREMENTS.md).
It does not describe an existing implementation.

## System boundaries

Separate the system into stimulus, input/playback adapter, UDP capture, decoder,
alignment, comparison and reporting components. A test run combines a test
manifest, reference and candidate identities, configuration metadata, raw
captures and results. The capture and analysis stages must also run separately,
so captures can be reanalysed without hardware.

Adapters must not assume a candidate can accept a WAV file or be launched by
the harness. Support externally started senders and manual playback alongside
automated adapters. For the hardware workflow, route the same output signal to
WLED line-in and to the candidate's supported input. Feature-based senders may
need their own upstream playback path. Verify routing and levels rather than
assuming that identical source audio yields identical physical input.

## Stimuli and execution

Define versioned cases for silence, low/mid/high and boundary-frequency tones,
multi-tone, a sweep, clicks at known intervals, amplitude modulation, and
seeded white and pink noise. Specify waveform parameters, sample format,
duration, seed and output level in the manifest. Include warm-up, measurement
and trailing intervals so startup, AGC settling and latency are explicit.
Pink-noise test audio is distinct from the reference's pink-noise correction.

Start capture before playback and stop it after the trailing interval. Playback
or adapter failures must invalidate the case. Where playback is manual, record
the timing source and any uncertainty; do not silently assume success.

## UDP capture and protocol

Listen on a configured port and interface, including an explicit multicast
membership when needed; support broadcast and unicast deployments. Select
reference and candidate using unambiguous endpoints or separate listeners.
Timestamp arrivals using one monotonic clock and store the original datagrams,
source endpoints and run metadata. Apply a bounded capture timeout even if no
packets arrive.

For v2, validate the six-byte `00002\0` header, exact 44-byte length and
finite numeric values. Decode the little-endian volume floats at offsets 8 and
12, peak flag at 16, 16 FFT bytes at 18, and magnitude and major-peak floats at
36 and 40. Preserve the remaining bytes; pressure, frame counter and
zero-crossing extensions are not guaranteed to be meaningful for all
implementations. Reject or separately account for unsupported versions and
malformed packets. Legacy v1 support requires a separately specified target
ABI and is outside the initial scope.

The layout is based on [WLED-sync's packet definition](https://github.com/netmindz/WLED-sync/blob/f7d9c154f59e87560ab376fb9946cec6cb10a8fe/WLED-sync.h#L12-L29)
and the [existing comparison parser](https://github.com/netmindz/SR-WLED-audio-server-win/blob/8c02afe6db6c2df9eaf21748cbdd9cce8a5c9fd7/tools/compare_wled.py).

## Alignment and comparison

- Use the common arrival-time base and a declared candidate offset, or estimate
  one from a suitable non-silent calibration signal. Record the sign convention,
  method, confidence, search range and usable overlap. If the estimate is
  ambiguous, require an explicit offset rather than silently choosing one.
- Fix alignment once per run or case, not independently for every metric.
  Compare configurable windows over the overlapping measurement interval.
  Report window duration, coverage and packet counts; require enough coverage
  before scoring. Account for packet rate differences through time-based
  aggregation, not direct packet pairing.
- Report reference and candidate values and signed and absolute differences
  for each FFT band, volume and magnitude. Include windowed and aggregate
  errors so brief discrepancies are not hidden by whole-run averages.
- Compare dominant frequency only when the signal is sufficiently strong and
  report excluded intervals. Use known-frequency stimuli to expose incorrect
  band mapping as well as weighting and profile differences.
- Compare peak events with a configurable time tolerance and report matched,
  missed and extra events. Define event extraction and account for repeated
  peak flags and packet loss; a peak flag is not necessarily a musical beat.
- Report packet rate, arrival jitter, malformed and missing data separately
  from audio metrics. Estimate sequence loss only if a sender is known to
  implement the frame-counter extension.

## Results and verdicts

Produce a versioned machine-readable report containing per-case provenance,
alignment, coverage, metrics, thresholds, diagnostics and verdict, plus a
concise human-readable summary and optional plots. Thresholds should be
declared for a reference configuration and stimulus rather than claimed as
universal WLED tolerances. Distinguish pass, threshold failure and
invalid/inconclusive; automated callers must be able to distinguish them by
exit status as well as report contents.

Record firmware and candidate revisions, input hardware, sample rate, gain,
AGC mode, noise gate, microphone/pink-correction profile, audio routing,
output levels and timing. Document clipping and level calibration. Do not
silently normalize away gain differences or infer hardware settings from
packets. Saved reference captures are only valid for the recorded setup.

## Verification

Unit-test packet decoding, malformed input, signal generation, offset handling,
window coverage, metrics, event matching and threshold verdicts with synthetic
fixtures. Integration-test UDP capture, source identification, playback failure
and end-to-end report generation using controlled senders. Verify repeatability
from saved captures. Perform separate real-device runs to measure hardware
variability, establish baselines and choose justified tolerances; synthetic
fixtures alone cannot establish equivalence to WLED.
