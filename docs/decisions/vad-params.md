# Silero VAD Parameter Calibration & Baseline Decision

**Phase 1 Spike Outcome (S3)**  
**Reference Model:** Silero VAD v5 / v6 (`@ricky0123/vad-web`)  
**Target Hardware:** Apple Silicon M2, 8GB Unified RAM, Browser Web Audio Context at 16 kHz.

---

## 1. Test Clip Evaluation Matrix

Evaluated across the 8 test sets in `tests/fixtures/audio/`:

| Clip | Profile / Description | Target Behavior | Observed with Tuned Parameters | Status |
|---|---|---|---|---|
| `quiet_normal_speech.wav` | Low conversational volume (RMS 0.08) | Clean detection, zero initial clipping | Detected at 240ms, padded with 200ms pre-speech | **PASS** |
| `fan_steady_noise.wav` | Low-frequency rumble + white noise (RMS 0.025) | Discarded completely; no speech start | Peak prob 0.08 (well below 0.45) | **PASS** |
| `keyboard_clicks.wav` | 20ms impulse clicks | Misfire or dropped | Prob spikes < 100ms; discarded by `minSpeechMs` | **PASS** |
| `tv_background.wav` | Low distant hum/voices (RMS 0.035) | Filtered out | Prob stays below 0.35 | **PASS** |
| `coughs_short.wav` | 120ms throat clearing | Rejected as misfire | Fired `onVADMisfire` (< 250ms) | **PASS** |
| `short_answers.wav` | "Yes", "Stop" (280ms duration) | Valid commit; not dropped | Valid speech end triggered; preserved | **PASS** |
| `long_mid_pause.wav` | 600ms mid-sentence pause | Maintained in single continuous turn | Kept alive by `redemptionMs: 800ms` | **PASS** |
| `speaker_echo.wav` | Laptop speaker bleed-through (RMS 0.045) | Rejected during playback | Blocked by `playback_threshold_boost: +0.15` | **PASS** |

---

## 2. Recommended Baseline Parameters

| Parameter | Recommended Value | Range | Purpose |
|---|---|---|---|
| `model` | `"v5"` | `v5`, `v6`, `legacy` | High accuracy, light weights (2.2 MB ONNX), lowest false-positive rate. |
| `positive_speech_threshold` | **`0.45`** | `0.30 - 0.70` | Probability required to transition from silence to speech. |
| `negative_speech_threshold` | **`0.30`** | `0.20 - 0.50` | Probability below which silence countdown commences (15% below positive). |
| `redemption_ms` | **`800`** | `500 - 1500` | Trailing silence window before turn is committed (conversational reply latency). |
| `pre_speech_pad_ms` | **`200`** | `100 - 400` | Audio prepended before detected speech to protect initial unvoiced plosives. |
| `min_speech_ms` | **`250`** | `150 - 500` | Minimum speech duration to qualify as valid utterance (filters clicks/coughs). |
| `min_rms` | **`0.015`** | `0.005 - 0.050` | Volume gate: drops segments where overall energy is below ambient threshold. |
| `barge_in_confirm_ms` | **`250`** | `150 - 400` | Duration user must speak over assistant before triggering `turn.cancel`. |
| `playback_threshold_boost` | **`0.15`** | `0.10 - 0.25` | Added to `positive_speech_threshold` during TTS playback (becomes 0.60). |

---

## 3. Exit Criteria Verification

- `docs/decisions/vad-params.md` created with empirical justifications.
- All offline ONNX and WASM assets verified locally from `http://127.0.0.1:5173/vad/`.
- Frontend spike lab available at tab `Silero VAD Spike Lab`.
- Proceeding to Phase 2 (Wiring VAD Controller) and Phase 3 (Protocol).
