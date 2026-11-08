# AudioReactive test harness: implementation plan

## Status and objective

This plan turns the proposed design in [REQUIREMENTS.md](REQUIREMENTS.md) and
[TECHNICAL_SPEC.md](TECHNICAL_SPEC.md) into an implementation. The first
release compares Audio Sync v2 output from one pinned reference configuration
and one candidate without requiring live hardware for every run. Hardware
validation remains required before results can claim conformance to WLED.

## Current implementation status

Implemented:

- deterministic, versioned PCM fixture generation and a committed stimulus set;
- strict v2 capture artifacts, live UDP capture and source identity checks;
- saved-capture comparison with settings preflight, coverage checks, field and
  window metrics, strong-signal frequency eligibility, peak-event matching,
  transport quality, diagnostics, JSON reports and text summaries;
- end-to-end execution that starts capture before an external player, splits
  traffic by declared reference/candidate endpoints and preserves its artifacts;
- tone, sweep and click case manifests plus unit and loopback integration
  coverage.

Not implemented or validated:

- an executable native MoonModules DSP adapter for the pinned C++ source;
- automatic offset estimation; cases currently use an explicit offset;
- hardware routing, calibration, threshold-baseline and real-device variability
  measurements.

Those limitations are intentional validity boundaries. The current suite can
measure and report a configured comparison, but cannot claim WLED conformance
until the selected firmware build and hardware workflow are validated.

The initial deliverable is an offline, deterministic comparison workflow that:

- accepts saved reference and candidate UDP captures;
- validates and decodes Audio Sync v2 datagrams;
- aligns streams, evaluates coverage and calculates field-level metrics;
- emits a versioned JSON report, a readable summary and distinct exit statuses;
- rejects invalid evidence instead of reporting a successful comparison.

Live capture, playback adapters, a native MoonModules DSP reference and
hardware calibration follow as separate milestones. Their feasibility and
interfaces must not block saved-capture analysis.

## Delivery principles

- Keep capture, decoding, analysis, reporting and adapters independently
  runnable so saved artifacts can be reanalysed.
- Make every result reproducible from immutable input artifacts, a versioned
  case manifest and complete provenance.
- Keep reference settings explicit. Unknown, unsupported or incompatible
  settings yield an inconclusive result for affected comparisons.
- Treat packet transport quality and DSP-field agreement as separate evidence.
- Derive thresholds from measurements for each pinned configuration and case;
  never present them as universal WLED tolerances.

## Proposed architecture

The repository will contain a small command-line harness with these boundaries:

| Component | Responsibility | Primary output |
| --- | --- | --- |
| Case manifest | Defines stimulus, settings contract, thresholds and timing windows | Versioned case configuration |
| Capture adapter | Receives UDP and persists datagrams with monotonic timestamps and source identity | Raw capture artifact |
| Decoder | Validates and decodes only supported Audio Sync v2 packets | Decoded packet stream and quality counters |
| Alignment | Applies a declared offset or estimates one from calibration data | Fixed run/case offset and confidence |
| Comparator | Produces time-windowed field, FFT-band, frequency and event metrics | Metrics, coverage and diagnostic evidence |
| Verdict engine | Enforces validity requirements and evaluates declared thresholds | Pass, threshold failure or inconclusive |
| Reporter | Writes machine-readable results and a concise terminal summary | JSON report and human summary |
| Input/reference adapters | Generate stimuli, launch/control compatible senders or use a native reference | Run artifacts and adapter metadata |

The implementation language, package tooling and report schema format remain
an initial setup decision. The selected stack must provide reliable UDP
multicast support, monotonic timestamps, numeric validation, JSON schema
validation and cross-platform command-line execution.

## Artifact contracts

Define stable, versioned schemas before implementing comparison logic:

1. **Run manifest:** identities, reference and candidate settings, source
   selection, transport settings, case IDs, timing, audio routing and tool
   versions.
2. **Capture artifact:** run ID, monotonic arrival timestamp, source endpoint,
   datagram bytes (encoded safely), parser status and capture metadata. Raw
   datagrams must remain recoverable.
3. **Decoded packet:** capture reference, Audio Sync version, volume/raw and
   smoothed values, peak flag, 16 FFT values, magnitude, dominant frequency
   and preserved extension bytes.
4. **Comparison report:** provenance, validation findings, fixed alignment,
   overlap and coverage, per-field metrics, thresholds, diagnostics, verdict
   and exit-status reason.
5. **Case manifest:** waveform/stimulus parameters, warm-up/measurement/trailing
   windows, expected suppression policy and configuration-specific thresholds.

The initial packet decoder contract is exact: accept only 44-byte v2 datagrams
with the `00002\0` header and finite decoded floating-point values. Malformed
or unsupported datagrams are retained in quality accounting and cannot be
silently treated as data.

## Milestones

### M0: foundation and contracts

1. Select the implementation stack and create the project layout, formatter,
   test runner and CLI entry point.
2. Define JSON schemas and fixtures for run manifests, captures, decoded
   packets, cases and reports.
3. Specify CLI commands and exit statuses: `0` pass, a nonzero threshold-fail
   status, and a distinct nonzero invalid/inconclusive status.
4. Pin the first MoonModules source revision and record the first reference
   profile without claiming that a native reference exists.

**Exit criteria:** schema validation and CLI smoke tests run in a clean
environment; fixtures cover valid, invalid and unknown-setting inputs.

### M1: offline capture analysis

1. Implement capture-artifact read/write and source identity validation.
2. Implement the strict Audio Sync v2 decoder and quality counters for
   malformed, unsupported, duplicate, reordered and absent traffic.
3. Implement declared-offset alignment, time-window selection, overlap and
   coverage validation.
4. Implement volume, magnitude, per-band FFT and dominant-frequency metrics;
   calculate dominant-frequency metrics only in configured strong-signal
   intervals.
5. Emit JSON and terminal reports with pass, threshold failure and
   inconclusive verdicts.

**Exit criteria:** saved fixture captures reproduce byte-for-byte report
metrics and deliberate band/volume errors fail their corresponding thresholds.
No traffic, ambiguous identity and inadequate overlap are inconclusive.

### M2: diagnostics and reproducible stimuli

1. Implement deterministic WAV generation for silence, tones, multi-tone,
   sweep, clicks, amplitude modulation and seeded white/pink noise.
2. Version each case manifest with waveform, sample rate, level, duration and
   seed; include warm-up, measurement and trailing intervals.
3. Add per-window aggregation and comparison charts or chart-ready data.
4. Implement peak-event extraction and tolerance-based matching, including
   repeated flags and packet-loss handling.
5. Add evidence-based diagnostic hints for band mapping, profile/scaling,
   gain/gating, AGC/limiter and peak-timing discrepancies.

**Exit criteria:** synthetic fixtures demonstrate each diagnostic category,
and hints cite the measured signals and relevant configuration rather than
asserting an internal cause.

### M3: live UDP capture and execution adapters

1. Implement configurable unicast, broadcast and multicast capture with
   explicit multicast membership, bounded no-traffic timeout and monotonic
   timestamps.
2. Add source selection using separate listeners or explicit endpoints; reject
   ambiguous streams.
3. Add a manual workflow that records playback timing uncertainty and marks
   failed or unverified playback invalid.
4. Add an adapter interface for compatible automated senders without assuming
   that candidates accept WAV input or can be launched by the harness.

**Exit criteria:** integration tests cover UDP capture, malformed input,
source filtering, no-traffic timeout and playback failure. Saved live captures
can be analysed identically to offline fixtures.

### M4: reference path and hardware validation

1. Investigate building the pinned MoonModules DSP natively with explicit
   platform shims and pinned FFT dependencies.
2. If feasible, implement an offline reference adapter that publishes logical
   v2 fields and records compiler, target, numerical and build settings.
3. Compare the native reference and saved hardware captures across the full
   stimulus matrix, measuring input-level, timing and state discrepancies.
4. Establish calibrated hardware workflow documentation, baseline variability
   and configuration-specific thresholds.

**Exit criteria:** the selected reference's limitations are documented with
measurements. Hardware remains the ground truth; native-reference divergence
does not automatically fail a candidate.

## Critical implementation rules

### Validity before scoring

The verdict engine evaluates evidence in this order:

1. Validate manifests, required settings, source identity and capture quality.
2. Validate playback/capture completion and usable aligned overlap.
3. Validate signal-specific eligibility, including strong-signal frequency
   intervals and verified silence-suppression intervals.
4. Score only eligible samples/windows against declared thresholds.
5. Produce `inconclusive` when an earlier validity condition fails, even if
   the eligible values would otherwise match.

For long-silence tests, require observed valid reference traffic before the
continuous silence period, a bounded or instrumented `volumeSmth < 1` interval,
the configured suppression timeout, and verified traffic resumption. Never
generate synthetic zero packets during suppression.

### Alignment and timing

Use the common monotonic arrival-time axis. A case may provide an explicit
candidate offset or request estimation from a non-silent calibration signal.
Store the sign convention, method, search range, confidence and overlap.
Estimate one offset per run or case and apply it to every metric; do not
optimize independently per field. Different packet rates are compared through
time-based aggregation rather than packet pairing.

### Settings compatibility

The comparison preflight checks sample rate, FFT size/window, averaging,
fast-path/sliding-window behavior, frequency distribution, mic filter and
quality, pink profile, FFT scaling, gain/input level, AGC, squelch, peak
configuration, dynamics limiter and silence-stop policy where applicable.
Each adapter must report values as known, unknown, unsupported or mapped
equivalent. A mapped equivalent needs documented calibration evidence.

## Verification plan

| Level | Coverage |
| --- | --- |
| Unit | Packet decoding, malformed input, finite-number checks, schema validation, stimulus determinism, offset logic, coverage, metrics, peak matching and verdict rules |
| Integration | UDP listener modes, source selection, capture persistence, timeout, playback failure and end-to-end report generation |
| Fixture regression | Saved captures reproduce metrics and verdicts from pinned manifests |
| Hardware validation | Real-device repeatability, routing/level calibration, native-reference comparison and threshold baselines |

Maintain fixtures for known offset with differing packet rates; deliberate
per-band, volume and peak errors; malformed packets; missing traffic; ambiguous
sources; insufficient overlap; silence suppression/resumption; and incompatible
settings. Each fixture identifies whether its expected outcome is pass,
threshold failure or inconclusive.

## Open decisions and decision gates

| Decision | Needed by | Evidence required |
| --- | --- | --- |
| Implementation stack | M0 | Cross-platform CLI, UDP/multicast, schema and test support |
| First reference firmware, board and canonical profile | M0 | Pinned revision and complete settings manifest |
| Automated playback platforms | M3 | Candidate/adaptor inventory and CI constraints |
| Native DSP feasibility | M4 | Reproducible build experiment against pinned source |
| Initial thresholds | M2/M4 | Repeated saved-capture and hardware measurements by case/profile |
| CI hardware access | M4 | Available runners, routing controls and artifact retention policy |

## First implementation slice

Start with M0 and the smallest vertical path from two saved capture artifacts
to one validated JSON report. Use a single static fixture pair with an explicit
offset, one measurement window and a declared FFT-band threshold. Once the
report distinguishes pass, threshold failure and inconclusive, expand to the
full metric and stimulus matrix. This sequence establishes the core evidence
model before introducing hardware, playback or native-DSP variability.
