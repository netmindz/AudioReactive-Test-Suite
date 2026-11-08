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

## Reference and settings contract

Make the fast path a native, offline reference adapter that feeds a WAV through
the selected, pinned MoonModules DSP and emits the same logical fields as the
hardware reference. Investigate isolating and building the actual C++ DSP with
its pinned FFT dependency and explicit platform shims; do not assume that it
already builds natively. Pin source revision, compiler, FFT library, target
options and numerical settings. Establish reproducibility within a pinned
environment and specify tolerances for cross-platform floating point. Validate
the native reference against hardware across the test matrix, including
input scaling, timing and state; treat discrepancies as reference limitations,
not automatic candidate failures. Saved hardware captures remain useful when
native porting is infeasible and real hardware remains the final ground truth.

The per-sender manifest must state sample rate, FFT size and window, averaging
mode, frequency distribution, mic filter and quality, pink profile, FFT
scaling, gain/input level, AGC mode, squelch, peak bin/threshold and dynamics
limiter, as applicable to the selected revision. Reference and candidate
adapters report actual settings and map equivalent names and units.
Compare settings before scoring: required unknown or mismatched settings make
the affected cases inconclusive. An explicit, documented equivalence or
calibration can make differing physical input paths comparable; it must not
silently mask a processing discrepancy. Some senders may lack settings; record
that limitation rather than asserting equivalence.

Record `audioSyncSilenceStop` independently for each sender, together with the
reference revision's suppression timeout and the observed start of continuous
silence. This transport policy may differ from DSP settings: either configure
it consistently for a transport comparison or report the difference explicitly.
Do not treat an unsupported or unverified setting as known.

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

For packet-value silence tests, either disable reference silence-stop and verify
continued traffic, or keep the entire continuous silent period (including
run-in, settling and measurement) safely below the configured timeout.
Budget from the earliest possible start of continuous silence, including
pre-capture/run-in silence, rather than just the nominal start of a WAV; allow
scheduling and timestamp uncertainty. The suppression timer uses internal
`volumeSmth`, which need not equal the transmitted smoothed sample. If the
start cannot be bounded, the time budget cannot be established.

For a long-silence transport test, first observe valid reference packets, then
establish the sustained internal `volumeSmth < 1` interval using reference
instrumentation or a validated bound, and check suppression after the configured
timeout and resumption once `volumeSmth >= 1`, allowing specified timing/network
tolerances. An absence that could also be caused by
lost connectivity, capture failure or unknown prior silence is inconclusive,
not evidence of suppression. Distinguish transport-policy results from DSP
field results; never synthesize zero-valued reference packets in suppressed
intervals. Missing packets outside a verified suppression period, failure to
resume, or inadequate field coverage must not result in a conformance pass.

### Targeted test catalogue

Use the pinned [DSP reference](REFERENCE_DSP.md) and each configuration to
derive expected outcomes. Keep reference measurements, thresholds and input
levels with every case; the hints below are hypotheses supported by measured
differences, not diagnoses of hidden implementation details.

| Case | Check and example hint |
| --- | --- |
| Tone at each band center and around edges | Compare the band-response pattern, including overlapping ranges and window leakage. Unexpected energy in adjacent or distant bands suggests bin mapping, FFT rate/size or frequency distribution. |
| Frequency sweep and two-tone | Compare channel transitions and dominant frequency; misplaced transitions suggest sample-rate, bin-boundary or FFT errors. |
| Seeded pink and white noise under selected profiles | Compare per-band shapes against the same reference profile; a frequency-dependent bias suggests pink correction, microphone profile or scaling. Do not assume pink input necessarily yields a flat output. |
| Silence and below/above-squelch levels | Compare volume, FFT and major-peak behavior after settling. Residual energy suggests gating, gain or noise-floor differences; verify the selected reference's silence behavior rather than assuming an unconditional frequency value. Include a separate long-silence suppression and sound-resumption case. |
| Level steps and amplitude modulation | Compare attack/release curves and AGC settling for each preset. Excessive overshoot or slow recovery suggests gain, AGC or dynamics-limiter differences. |
| Click trains at multiple intervals, including around hold-off | Compare peak events with a tolerance. Extra or missed events suggest bin/threshold, hold-off or packet peak handling; a regular musical beat is not by itself the oracle. |

Each failed case should include case ID, measured reference/candidate values,
threshold, relevant settings and ranked possible causes with the evidence that
triggered each hint. Avoid hard-coded claims such as a fixed band for 1 kHz
independent of FFT settings.

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
implementations. MoonModules defines a frame counter at offset 17, but other
v2 senders may reserve or not advance it. Reject or separately account for
unsupported versions and malformed packets. Legacy v1 support requires a
separately specified target ABI and is outside the initial scope.

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
  from audio metrics. Estimate sequence loss only when counter semantics are
  known and observed to advance; handle wraparound, duplicates, reordering and
  resets. A nonzero byte alone is insufficient evidence of a working counter.

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

## Agent workflow and existing tools

Provide a single documented command, once implemented, that selects a candidate
adapter and profile, runs the offline matrix and writes a JSON report. An agent
reads the requirements and pinned DSP reference, runs that command, inspects
invalid cases before failed cases, follows evidence-backed hints to change the
candidate, and reruns the same cases and full matrix. It must not relax
thresholds or silently change the reference settings to obtain a pass. Include
the command, exit-code contract, artifacts and iteration instructions in the
future harness documentation; no command is specified as available today.

Use the existing [comparison parser and plots](https://github.com/netmindz/SR-WLED-audio-server-win/blob/8c02afe6db6c2df9eaf21748cbdd9cce8a5c9fd7/tools/compare_wled.py)
and [signal matrix](https://github.com/netmindz/SR-WLED-audio-server-win/blob/8c02afe6db6c2df9eaf21748cbdd9cce8a5c9fd7/tools/run_test_matrix.py)
as a starting point, subject to their license and dependency requirements.
Before reuse, correct the loose header check, explicit multicast joining,
no-traffic timeout and playback failure handling; add quantitative comparisons
and adapter boundaries rather than assuming the existing Linux runner is
portable. Do not copy code without complying with its license.

## Verification

Unit-test packet decoding, malformed input, signal generation, offset handling,
window coverage, metrics, event matching and threshold verdicts with synthetic
fixtures. Integration-test UDP capture, source identification, playback failure
and end-to-end report generation using controlled senders. Verify repeatability
from saved captures. Perform separate real-device runs to measure hardware
variability, establish baselines and choose justified tolerances; synthetic
fixtures alone cannot establish equivalence to WLED.
