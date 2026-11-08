# AudioReactive test harness: requirements

## Purpose

Provide a reusable, implementation-independent test harness that measures how
closely an audio-sync sender matches a specified WLED AudioReactive reference.
The results should help developers and AI agents identify discrepancies and
iterate without requiring repeated manual interpretation of packet captures.
This document describes a proposed harness; it is not an implemented test suite.

## Background and research

- [MoonModules AudioReactive](https://github.com/MoonModules/WLED-AudioReactive-Usermod)
  processes microphone or line-in audio. Firmware, input hardware, gain, AGC,
  frequency correction and microphone profile can all affect its output.
- [WLED-sync](https://github.com/netmindz/WLED-sync/blob/f7d9c154f59e87560ab376fb9946cec6cb10a8fe/WLED-sync.h)
  describes the Audio Sync packet data. Version 2 contains volume, a peak flag,
  16 FFT bands, magnitude and dominant frequency. Some bytes have
  extension-specific meanings and are not meaningful for every sender.
- The [comparison-tuning tools](https://github.com/netmindz/SR-WLED-audio-server-win/tree/8c02afe6db6c2df9eaf21748cbdd9cce8a5c9fd7/tools)
  capture and plot streams and provide a signal matrix. Their runner assumes
  Linux playback and does not calculate quantitative agreement.
- The [Windows audio server](https://github.com/Victoare/SR-WLED-audio-server-win)
  captures live audio, and its Linux CLI captures an output monitor rather than
  accepting a WAV file. [WLED Audio Sender](https://github.com/netmindz/WLED-Audio-Sender)
  captures mobile microphone or Android internal audio. The
  [Music Assistant WLED audio sync branch](https://github.com/netmindz/music-assistant-server/tree/feature/wled-audio-sync)
  converts extracted features into packets.
- [WLED-App PR #318](https://github.com/Moustachauve/WLED-App/pull/318) is an
  open draft that adds AGC modes and squelch control to Sound Sync. At the
  [reviewed PR revision](https://github.com/Moustachauve/WLED-App/blob/d9efec714e0b28debcb4977d381c099742661091/shared/src/commonMain/kotlin/ca/cgagnier/wlednativeandroid/service/soundsync/dsp/SoundSyncProcessor.kt),
  shared processing applies filtering, gain, FFT and peak detection to PCM.
  Its [Android](https://github.com/Moustachauve/WLED-App/blob/d9efec714e0b28debcb4977d381c099742661091/app/src/main/java/ca/cgagnier/wlednativeandroid/service/soundsync/SoundSyncManager.kt)
  and [iOS](https://github.com/Moustachauve/WLED-App/blob/d9efec714e0b28debcb4977d381c099742661091/iosApp/wled/Service/SoundSync/SoundSyncManager.swift)
  managers capture microphone audio and send Sound Sync UDP packets. This is
  a relevant candidate for comparison, not evidence that the draft has merged
  or that its processing matches WLED.

These projects should not be assumed to share an audio-file input or automation
interface.

## Scope

The initial target is Audio Sync v2 output from one reference and one candidate.
Real WLED hardware is the final ground truth. A deterministic offline software
reference and saved reference captures should enable fast iteration and CI
without hardware; their equivalence to the selected hardware must be validated.
The harness assesses observable outputs, not proof that the candidate internally
implements the same algorithm. See [REFERENCE_DSP.md](REFERENCE_DSP.md) for
the version-specific reference behavior and its configuration dependencies.

## Functional requirements

1. **Controlled input:** Feed both implementations the same audio, including a
   real WLED device through line-in. Support live capture and comparison with
   saved captures, with documented limitations for each setup.
2. **Repeatable stimuli:** Supply silence, single tones across the spectrum,
   multi-tone, sweep, clicks, amplitude modulation, white noise and pink noise.
   Record durations, sample rates, amplitudes, frequencies and random seeds.
3. **Capture and identity:** Identify reference and candidate explicitly, retain
   packet timestamps and raw data, and report malformed packets and traffic
   quality. Do not silently mix streams from multiple senders.
4. **Latency tolerance:** Compare over configurable time intervals and windows,
   allowing a declared or estimated relative offset. Different packet rates
   must not require packet-by-packet correspondence.
5. **Diagnostics:** Report differences for all 16 FFT bands, raw and smoothed
   volume, magnitude, dominant frequency and peak/beat behavior. Include
   packet counts, timing, missing data and alignment information.
6. **Closed-loop use:** Produce human-readable and machine-readable results
   suitable for automated agents and CI. Provide configurable thresholds and
   distinct success, threshold-failure and invalid-run outcomes.
7. **Reproducibility:** Record versions, configurations, signal parameters,
   audio routing and levels, timing, and capture quality. Preserve raw captures
   for subsequent analysis and comparison.
8. **Integration:** Do not require candidates to use a particular language or
   implement file input. Support manual and automated workflows appropriate to
   desktop, mobile and feature-based senders.
9. **Validity:** Unexpected missing packets, failed playback, ambiguous source
   identity, insufficient overlap or unreliable alignment must not be reported
   as a successful comparison. Handle configured reference silence suppression
   explicitly; absence of packets alone is not proof of silence or agreement.
10. **Domain diagnostics:** Use targeted tests to distinguish likely FFT
    frequency mapping, pink correction, microphone profile, scaling, AGC,
    squelch, limiter and peak-detection mistakes. Report evidence and actionable,
    qualified hints, not just a score.
11. **Comparable configurations:** Require the same relevant reference and
    candidate settings or an explicitly documented equivalent. Incompatible
    or unknown required settings make the affected comparison inconclusive;
    no silent substitution is allowed.
12. **Software reference and agent loop:** Provide a reproducible offline
    reference path, subject to hardware validation, and a one-command test
    workflow with structured output and instructions for an agent to diagnose,
    change and rerun a candidate.

## Acceptance criteria

- A controlled fixture with a known offset and differing packet rates passes
  appropriate declared thresholds when its outputs agree.
- Deliberate FFT-band, volume and peak-behavior changes are reported in the
  corresponding metrics and fail appropriate thresholds.
- Missing traffic and insufficient usable overlap produce an invalid or
  inconclusive result, not a pass.
- A long silent run with reference silence-stop enabled distinguishes expected
  suppression from unexpected packet loss; it scores packet fields only while
  both streams have adequate coverage, and verifies resumed traffic after sound
  returns. A silent-start capture without evidence of prior traffic is not a pass.
- Saved captures and the same comparison configuration reproduce the same
  metrics and verdict.
- A documented hardware workflow records sufficient settings and calibration
  details to interpret results against a particular reference firmware.
- Fixtures with deliberately wrong band mapping, correction, gain/gating or
  peak timing yield relevant diagnostic hints with the measured evidence.
- Unknown or mismatched required settings do not yield a conformance pass.
- Repeating an offline case with identical inputs and pinned reference settings
  produces reproducible results; hardware comparisons quantify any deviation.

## Open decisions

- Which WLED firmware revision, hardware and settings define the first reference?
- Which MoonModules settings define the canonical reference profile, and how
  should candidates express equivalent settings they do not expose directly?
- Is a faithful native build of the selected reference feasible, and what
  numerical tolerance is required across platforms?
- Which platforms must support automated playback initially?
- Which CI environments can access hardware, and which must use saved captures?
- What baseline measurements justify initial thresholds for each configuration?
