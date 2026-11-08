# MoonModules AudioReactive DSP reference

This is a research guide for designing tests and sender implementations, not a
claim that every WLED version behaves identically. It describes
[`audio_reactive.h` at a4338f2](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h);
use that source as the authority for exact operations, constants and build
conditions. Pin the actual reference firmware and settings for every run. No
native reference or canonical profile is implemented or selected yet.

## Sampling and FFT

The [source](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L475-L512)
uses 512 samples at 22,050 Hz on most targets, or 18,000 Hz on ESP32-C3/C6.
The bin spacing is respectively 43.0664 and 35.15625 Hz. Sliding-window
builds can reuse half a window; C3/C6 skip every second FFT. These are not
universal constants across versions or builds.

Default `fftWindow=0` is Blackman-Harris; Hann, Nuttall, Hamming, Flat-top and
Blackman are selectable. Window-specific correction `wc` is applied to the
channels, with extra correction for reused samples. After the FFT and magnitude
calculation, bin 0 is cleared and magnitudes are divided by 16 before channel
mapping. `fftAddAvg` takes an inclusive range and uses RMS averaging on
FPU-capable multicore targets, arithmetic mean otherwise; one-bin ranges return
that bin. See [windowing and mapping](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L902-L1065)
and [averaging](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L540-L555).

## GEQ mapping

Ranges below are **inclusive FFT-bin indices**, not exact frequency boundaries.
All channels are multiplied by `wc`; extra multipliers appear in the table.
Frequencies change with the sample rate and window leakage affects adjacent
channels. The source warns that comments describing `freqDist=1` frequencies
are incorrect: derive frequencies from bin spacing and validate the response.

| Channel | `freqDist=0`, filter != 1 | `freqDist=1`, filter != 1 |
| ---: | :--- | :--- |
| 0 | 1 | 1 |
| 1 | 2 | 2 |
| 2 | 3–4 | 3 |
| 3 | 5–6 | 4 |
| 4 | 7–9 | 5–6 |
| 5 | 10–12 | 7–8 |
| 6 | 13–18 | 9–10 |
| 7 | 19–25 | 11–13 |
| 8 | 26–32 | 14–18 |
| 9 | 33–43 | 19–25 |
| 10 | 44–55 | 26–36 |
| 11 | 56–69 | 37–45 |
| 12 | 70–85 | 46–66 |
| 13 | 86–103 | 67–97 |
| 14 | 104–164 × 0.88 | 98–164 × 0.88 |
| 15 | 165–215 × 0.70 | 165–215 × 0.70 |

When `useInputFilter == 1`, channels 4–14 are unchanged, but these entries
replace channels 0–3 and 15:

| Channel | `freqDist=0` | `freqDist=1` |
| ---: | :--- | :--- |
| 0 | 3 × 0.8 | 1 × 0.8 |
| 1 | 4 × 0.9 | 2 × 0.9 |
| 2 | 5 | 3 |
| 3 | 6 | 4 |
| 15 | 165–205 × 0.75 | 165–205 × 0.75 |

In particular, the filtered right-shift branch still uses bins 1 and 2 even
though a nearby source comment says frequencies below 100 Hz are skipped.
When the FFT gate is closed, previous channel calculations decay instead of
instantly becoming zero.

## Profiles and output scaling

[`fftResultPink`](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L415-L435)
is an exact 11-by-16 table of per-channel multipliers. `pinkIndex` selects
0 default, 1 line-in, 2–4 INMP441 variants (spelled IMNP441 in the source),
5–6 ICS-43434 variants, 7 SPM1423, 8–9 user-defined, or 10 almost-flat/no
pink adjustment. Index 10 is **not** a row of ones; read the pinned table for
the exact factors. Default index 0 can be overridden at build time.
Do not conflate these multipliers with generating pink-noise test audio or
the optional separate weighting for dominant-frequency selection.

[Post-processing](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L1155-L1250)
applies profile factors, `FFT_DOWNSCALE=0.40` only when scaling is enabled,
and AGC or manual gain. Limiter-dependent smoothing precedes output scaling.
`FFTScalingMode` is 0 none, 1 logarithmic, 2 linear, 3 square root (default);
the modes apply channel-dependent factors and thresholds, followed by rounding
and clamping to 0–255. AGC also applies an input-level-dependent post-gain to
GEQ. These details matter: equal raw FFT bins do not imply equal packets.

## Gain, gating and dynamics

With AGC off, the manual multiplier is
`sampleGain / 40 * inputLevel / 128 + 1 / 16`; the sample path selects filtered
or unfiltered input according to `micQuality` and clips the adjusted sample.
The same multiplier is used in FFT post-processing. The
[AGC](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L1547-L1629)
has normal, vivid and lazy presets with distinct targets, follow rates and
PI gains. It uses a decaying peak estimate, two targets, normal and emergency
zones, anti-windup and a gain clamp of 1/64–32. Timing and state matter; a
single fixed gain or generic envelope follower is not equivalent.

Squelch gates the pre-gain, DC-adjusted envelope; AGC additionally gates very
small `sampleReal`. FFT gating and transmitted magnitude have their own
thresholds. Reset and settling behavior therefore matter as well as the
numeric squelch setting. See [sampling](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L1638-L1710).

The optional dynamics limiter defaults to attack/decay 24/250 ms with sliding
FFT, otherwise 50/300 ms, and can be configured or disabled. FFT channels
have their own smoothing. The volume limiter affects effect-facing
`volumeSmth`; transmitted raw and smoothed samples are taken before that
limiter. See [defaults](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L299-L311),
[limiter](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L1795-L1822)
and [transmission](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L1886-L1916).

## Dominant frequency and peak flag

After FFT peak calculation, a major peak below one bin becomes 1 Hz with zero
magnitude; a peak above 0.42 × sample rate restores the previous peak and
magnitude. The frequency is then clamped to 1–11025 Hz. When the FFT gate is
closed, the FFT path sets frequency to 1 and magnitude to 0.001. Optional
human-ear weighting and subsequent transmission processing also matter.
See [FFT peak and gate](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L940-L990).

The [FFT peak detector](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L1251-L1280)
requires `sampleAvg > 1`, `maxVol > 0`, `binNum > 4`, the selected FFT bin
above `maxVol`, and more than 100 ms since the previous peak. It can retrigger
on sustained energy, so it is not a musical beat detector. There is also a
[volume-peak path](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L1711-L1725):
a rising peak with `sampleReal > 0.5`, `sampleAvg > 1`, `binNum < 12` or
`maxVol < 1`, and more than 80 ms since the last peak. Depending on settings,
both paths can operate. Both set a UDP latch normally cleared on transmission;
local peak reset is separate. A simple volume-spike detector is not equivalent.

The MoonModules v2 packet has an 8-bit rolling `frameCounter` at offset 17,
incremented after connected transmission attempts and wrapping at 256. Other
v2 senders may leave this byte reserved. Zero is also used for legacy behavior;
do not infer loss from a nonzero byte alone. See
[packet definition](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L1352-L1363)
and [transmitter](https://github.com/MoonModules/WLED-AudioReactive-Usermod/blob/a4338f2ae26e303653c8fffbbb9af975401cefc5/audio_reactive.h#L1886-L1933).
