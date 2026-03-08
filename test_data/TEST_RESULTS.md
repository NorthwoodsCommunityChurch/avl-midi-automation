# Alignment Test Results

Every test run recorded here with full metrics.

---

## Batch Tests (13 songs)

All batch tests run on graphics-mac (10.10.11.77, 24GB RAM) with `--estimate-windows --auto-offset`.

### Batch #1 — Baseline (stable-ts small, conservative pace correction)
**Date:** ~2026-02-28
**Config:** small model, quality >= 0.3, pace correction (2+ samples, avg > 2.0s)
**Command:** `test_batch.py ... --estimate-windows --auto-offset --run --count 20`
**Songs:** 13 tested

| Metric | Value |
|--------|-------|
| <0.5s | 30% |
| <1.0s | 54% |
| <2.0s | 67% |
| <5.0s | 76% |
| Avg error | 4.1s |

### Batch #2 — Combined changes (quality 0.15 + template + word-weight + onset snap)
**Date:** ~2026-03-03
**Config:** quality >= 0.15, word-weighted proportional, template matching, onset snap 0.3s
**Command:** `test_batch.py ... --estimate-windows --auto-offset --run --count 20`
**Songs:** 13 tested

| Metric | Value |
|--------|-------|
| <0.5s | 30% |
| <1.0s | 51% |
| <2.0s | 67% |
| <5.0s | 77% |
| Avg error | 4.7s |

**Notes:** No improvement over baseline. <1s slightly worse (51% vs 54%).

### Batch #3 — Vocal-band xcorr offset correction
**Date:** 2026-03-05
**Config:** + xcorr with 300-3000Hz bandpass, SNR >= 5.0 threshold
**Command:** `test_batch.py ... --estimate-windows --auto-offset --run --count 20`
**Songs:** 13 tested

| Metric | Value |
|--------|-------|
| <0.5s | 30% |
| <1.0s | 50% |
| <2.0s | 67% |
| <5.0s | 77% |
| Avg error | 4.7s |

**Notes:** "(no xcorr corrections applied)" for most songs. SNR threshold too high.

---

## Single-Song Tests — Here In Your House (43 slides, 181.5 BPM)

### HIYH #1 — Baseline (small model)
**Date:** ~2026-02-28
**Config:** small model, estimated windows

| Metric | Value |
|--------|-------|
| <0.5s | 25% (11/43) |
| <1.0s | 56% (24/43) |
| <2.0s | 81% (35/43) |
| <5.0s | 94% (40/43) |
| Avg error | 1.6s |
| Global offset | +0.7s |

### HIYH #2 — Demucs + large-v3 model
**Date:** ~2026-03-04
**Config:** large model, Demucs vocals as PRIMARY audio

| Metric | Value |
|--------|-------|
| <0.5s | 11% |
| <1.0s | 14% |
| <2.0s | 25% |
| <5.0s | 47% |
| Avg error | 6.9s |
| Global offset | -9.5s |

**Notes:** Catastrophically bad. Demucs from Guide.wav too noisy.

### HIYH #3 — Medium model
**Date:** ~2026-03-04
**Config:** medium model, estimated windows

| Metric | Value |
|--------|-------|
| <0.5s | 22% |
| <1.0s | 39% |
| <2.0s | 69% |
| <5.0s | 92% |
| Avg error | 1.9s |
| Global offset | +5.8s |

**Notes:** Medium model systematically late. Worse than small.

### HIYH #4 — Medium model + ground-truth windows
**Date:** ~2026-03-04
**Config:** medium model, ground-truth section windows (oracle test)

| Metric | Value |
|--------|-------|
| <0.5s | 22% |

**Notes:** Same as estimated windows! Proved window estimation is NOT the bottleneck.

### HIYH #5 — Beat-snapping (librosa)
**Date:** ~2026-03-05
**Config:** small model + beat snap (max_shift=0.5s)

| Metric | Value |
|--------|-------|
| <0.5s | 25% |
| <1.0s | 56% |
| <2.0s | 81% |
| <5.0s | 94% |
| Avg error | 1.6s |

**Notes:** Identical to baseline. At 181.5 BPM, beats too dense (every 0.33s).

### HIYH #6 — Cross-correlation offset (full spectrum)
**Date:** 2026-03-05
**Config:** small model + xcorr (full spectrum RMS, SNR >= 3.0)

| Metric | Value |
|--------|-------|
| <0.5s | 28% |
| <1.0s | 47% |
| <2.0s | 75% |
| <5.0s | 94% |
| Avg error | 1.7s |

**Notes:** 2 sections corrected, one wrong (Chorus 1 shifted -3.6s). <1s regressed.

### HIYH #7 — Cross-correlation offset (vocal-band)
**Date:** 2026-03-05
**Config:** small model + xcorr (300-3000Hz bandpass, SNR >= 5.0)

| Metric | Value |
|--------|-------|
| <0.5s | 33% (14/43) |
| <1.0s | 60% (26/43) |
| <2.0s | 79% (34/43) |
| <5.0s | 86% (37/43) |
| Avg error | 2.1s |
| Median error | 0.7s |
| Per-section <0.5s | 51% |

**Notes:** Slightly better than baseline. Most improvement from per-section offset correction.

---

## Per-Song Results (large model, no demucs)

From earlier testing sessions:

| Song | <0.5s | Notes |
|------|-------|-------|
| Good Plans | 81% | Best performer |
| God So Loved | 61% | Good |
| Be Glad | 48% | Decent |
| As For Me And My House | 47% | Decent |
| Way Maker | 40% | Decent |
| Holy Forever | 35% | Below target |
| Here In Your House | 17% | Poor (large model) |
| Because He Lives | 14% | Poor |
| King Of Kings | 13% | Poor |
| At The Cross | 12% | Poor |
| Christ Be Magnified | 8% | Poor |

---

## HIYH #8 — Demucs-mix (Demucs on all-stems mix, dual-audio fallback)
**Date:** 2026-03-05
**Config:** small model, estimated windows, Demucs htdemucs_ft on all-stems mix

| Metric | Value |
|--------|-------|
| <0.5s | 25% (9/36) |
| <1.0s | 56% (20/36) |
| <2.0s | 81% (29/36) |
| <5.0s | 94% (34/36) |
| Avg error | 1.6s |
| Global offset | +5.5s |
| Per-section <0.5s | 50% (18/36) |

**Notes:** Identical to baseline. Dual-audio always chose main audio (quality > 0.75). Per-section offset correction would double accuracy (50% vs 25%).

---

## CTC vs Whisper — Here In Your House (side-by-side)

**Date:** 2026-03-05
**Config:** Whisper small vs CTC ONNX (MMS_FA), estimated windows, all-stems mix

| Section | Slides | Whisper first-slide | CTC first-slide | Delta |
|---------|--------|-------------------|----------------|-------|
| Verse 1 | 7 | 11.18s | 7.22s | -3.96s |
| Chorus 1 | 4 | 47.41s | 32.54s | -14.87s |
| Verse 2 | 4 | 84.12s | 64.94s | -19.18s |
| Chorus 1 (rep) | 4 | 92.62s | 85.59s | -7.03s |
| Bridge | 4 | 142.87s | 116.79s | -26.08s |

**Verdict:** CTC is catastrophically worse — places words 7-26s too early. Also 3x slower. Not viable.

---

## Demucs Tests (5 songs, large model)

| Song | Without Demucs | With Demucs | Change |
|------|---------------|-------------|--------|
| Christ Be Magnified | 8% | 50% | +42% !! |
| At The Cross | 12% | 40% | +28% !! |
| Good Plans | 81% | 61% | -20% |
| Be Glad | 48% | 36% | -12% |
| Because He Lives | 14% | 5% | -9% |

---

## Onset Anchoring Tests — Here In Your House

### HIYH #9 — Onset Anchoring v1 (bandpass absolute threshold)
**Date:** 2026-03-05
**Config:** small model, estimated windows, onset anchoring with 300-3000Hz bandpass, 25% peak threshold, 0.5s sustain

| Metric | Value |
|--------|-------|
| <0.5s | 3/36 (8%) |
| <1.0s | 3/36 (8%) |
| <2.0s | 6/36 (17%) |
| <5.0s | 26/36 (72%) |
| Avg error | 4.2s |
| Per-section <0.5s | 18/36 (50%) |

**Notes:** Catastrophic regression. All 8 sections shifted, all wrong. Onset fires on instrument energy at window start. Baseline was 25% <0.5s.

### HIYH #10 — Onset Anchoring v2 (bandpass step-up detection)
**Date:** 2026-03-05
**Config:** + step-up requirement (1.5x recent baseline, 30% absolute, 1s sustain, 3s lookback)

| Metric | Value |
|--------|-------|
| <0.5s | 6/36 (17%) |
| <1.0s | 17/36 (47%) |
| <2.0s | 20/36 (56%) |
| <5.0s | 26/36 (72%) |
| Avg error | 3.0s |
| Per-section <0.5s | 18/36 (50%) |

**Notes:** More conservative (2 corrections vs 8), but Verse 1 still shifted wrong (silence→instrument transition triggers step-up). Still worse than baseline.

### HIYH #11 — Onset Anchoring v3 (Demucs-separated onset detection)
**Date:** 2026-03-05
**Config:** + Demucs htdemucs_ft vocals for onset detection, 20% threshold, 0.5s sustain

| Metric | Value |
|--------|-------|
| <0.5s | 7/36 (19%) |
| <1.0s | 8/36 (22%) |
| <2.0s | 10/36 (28%) |
| <5.0s | 17/36 (47%) |
| Avg error | 5.0s |
| Per-section <0.5s | 18/36 (50%) |

**Notes:** Demucs detects backing vocal onsets, not lead vocal. Bridge sections improved (7/8 within 0.5s) but Verse 1 shifted +5.6s wrong. Overall still worse than baseline.

### HIYH #12 — Demucs-primary alignment (--demucs-primary)
**Date:** 2026-03-05
**Config:** small model, align on Demucs htdemucs_ft separated vocals (primary, not fallback)

| Metric | Value |
|--------|-------|
| <0.5s | 6/36 (17%) |
| <1.0s | 11/36 (31%) |
| <2.0s | 22/36 (61%) |
| <5.0s | 31/36 (86%) |
| Avg error | 2.7s |
| Per-section <0.5s | 13/36 (36%) |

**Notes:** Worse than baseline AND per-section oracle dropped from 50% to 36%. Verse 1 std dev 6.42s (was 1.66s). Demucs vocals from all-stems mix mostly backing vocals — verses have near-silence, Whisper aligns to noise.

---

## HIYH #13 — Vocal-Boosted Mix (3x vocal stem boost)
**Date:** 2026-03-05
**Config:** small model, estimated windows, vocal stems (Tenor, BGVS, Alto) boosted 3x in alignment mix

| Metric | Value |
|--------|-------|
| <0.5s | 7/36 (19%) |
| <1.0s | 18/36 (50%) |
| <2.0s | 29/36 (81%) |
| <5.0s | 34/36 (94%) |
| Avg error | 1.6s |
| Per-section <0.5s | 19/36 (53%) |

**Notes:** Close to baseline (25% <0.5s). Per-section oracle slightly improved (53% vs 50%), suggesting vocal boosting helps within-section placement marginally. Does NOT solve per-section offset problem.

---

## stable-ts Parameter Tests — Here In Your House

### HIYH #14 — only_voice_freq + fast_mode
**Date:** 2026-03-05
**Config:** small model, estimated windows, only_voice_freq (200-5000Hz), fast_mode=True

| Metric | Value |
|--------|-------|
| <0.5s | 6/36 (17%) |
| <1.0s | 18/36 (50%) |
| <2.0s | 28/36 (78%) |
| <5.0s | 34/36 (94%) |
| Per-section <0.5s | 15/36 (42%) |

**Notes:** WORSE than baseline. fast_mode causes "Failed to align the last N words" warnings — too aggressive for singing. Oracle dropped from 50% to 42%.

### HIYH #15 — only_voice_freq only
**Date:** 2026-03-05
**Config:** small model, estimated windows, only_voice_freq (200-5000Hz)

| Metric | Value |
|--------|-------|
| <0.5s | 8/36 (22%) |
| <1.0s | 16/36 (44%) |
| <2.0s | 29/36 (81%) |
| <5.0s | 34/36 (94%) |
| Per-section <0.5s | 18/36 (50%) |

**Notes:** Slightly worse than baseline. Removing bass/drums actually loses useful timing cues. Oracle unchanged.

### HIYH #16 — dynamic_heads
**Date:** 2026-03-05
**Config:** small model, estimated windows, dynamic_heads=True

| Metric | Value |
|--------|-------|
| <0.5s | 12/36 (33%) |
| <1.0s | 19/36 (53%) |
| <2.0s | 26/36 (72%) |
| <5.0s | 33/36 (92%) |
| Avg error | 1.7s |
| Per-section <0.5s | 17/36 (47%) |

**Notes:** Best overall improvement (+8% over baseline). dynamic_heads finds optimal cross-attention heads at runtime. Oracle slightly lower (47% vs 50%), but actual accuracy improved more.

### HIYH #17 — Continuity chaining (BROKEN — removed)
**Date:** 2026-03-05
**Config:** small model, estimated windows, inter-section continuity chaining

| Metric | Value |
|--------|-------|
| <0.5s | 12/36 (33%) |
| <1.0s | 14/36 (39%) |
| <2.0s | 20/36 (56%) |
| <5.0s | 30/36 (83%) |
| Per-section <0.5s | 18/36 (50%) |

**Notes:** CATASTROPHIC at wider tolerances — <1.0s dropped from 56% to 39%. Continuity chaining compounds offset errors across sections. Bridge sections shifted +4s too far. Approach removed from code.

### HIYH #18 — word_dur_factor=4.0 + dynamic_heads
**Date:** 2026-03-05
**Config:** small model, estimated windows, dynamic_heads=True, word_dur_factor=4.0

| Metric | Value |
|--------|-------|
| <0.5s | 11/36 (31%) |
| <1.0s | 22/36 (61%) |
| <2.0s | 28/36 (78%) |
| <5.0s | 35/36 (97%) |
| Avg error | 1.4s |
| Per-section <0.5s | 17/36 (47%) |

**Notes:** Slight trade-off: <0.5s down from 33% to 31%, but <1.0s up from 53% to 61%. word_dur_factor=4.0 allows longer held notes, improving coarse accuracy at the expense of tight precision. Best avg error yet (1.4s).

### HIYH #19 — Two-phase + dynamic_heads
**Date:** 2026-03-05
**Config:** small model, estimated windows, dynamic_heads=True, two_phase=True

| Metric | Value |
|--------|-------|
| <0.5s | 6/36 (17%) |
| <1.0s | 24/36 (67%) |
| <2.0s | 27/36 (75%) |
| <5.0s | 34/36 (94%) |
| Avg error | 1.6s |
| Per-section <0.5s | 18/36 (50%) |

**Notes:** Phase 2 fired with avg shift 13.5s. <0.5s dropped to 17% (WORSE) but <1.0s improved to 67% (BEST). Window refinement helps coarse positioning but degrades precise timing. Oracle back to 50%.

### HIYH #20 — Slide-level refinement + dynamic_heads
**Date:** 2026-03-05
**Config:** small model, estimated windows, dynamic_heads=True, slide-level refinement (±5s windows per slide)

| Metric | Value |
|--------|-------|
| <0.5s | 10/36 (28%) |
| <1.0s | 17/36 (47%) |
| <2.0s | 22/36 (61%) |
| <5.0s | 31/36 (86%) |
| Avg error | 2.1s |
| Per-section <0.5s | 13/36 (36%) |

**Notes:** WORSE than baseline. 35/36 slides "refined" but most positions degraded. Individual slide alignment loses section context and ordering constraints. Oracle dropped from 47% to 36%.

### HIYH #21 — Qwen3-ForcedAligner-0.6B (section-by-section)
**Date:** 2026-03-05
**Config:** Qwen3-ForcedAligner-0.6B on MPS, section-by-section with estimated windows, known lyrics text

| Metric | Value |
|--------|-------|
| <0.5s | 0/36 (0%) |
| <1.0s | 0/36 (0%) |
| <2.0s | 4/36 (11%) |
| <5.0s | 14/36 (39%) |
| Per-section <0.5s | 14/36 (39%) |

**Notes:** Catastrophically worse than stable-ts. Qwen3 is speech-trained, cannot locate words in polyphonic singing. Timestamps 4-9s off even in correctly windowed sections. 13s total time (fast but inaccurate).

### HIYH #22 — Qwen3-ASR Transcription + Matching
**Date:** 2026-03-05
**Config:** Qwen3-ASR-0.6B transcription with ForcedAligner timestamps, section-by-section, SequenceMatcher word matching

| Metric | Value |
|--------|-------|
| <0.5s | 0/36 (0%) |
| <1.0s | 1/36 (3%) |
| <2.0s | 1/36 (3%) |
| <5.0s | 6/36 (17%) |
| Avg error | 8.3s |

**Notes:** Word matching partially works (42/53 for Verse 1) but timestamps consistently 5-10s late. Two slides missed entirely (-1.00s). 67s total time. Worse than forced alignment approach.

### HIYH #23 — Beat-Informed Snapping (librosa)
**Date:** 2026-03-05
**Config:** stable-ts small baseline + librosa beat tracking, snap to nearest beat/downbeat

| Metric | Value |
|--------|-------|
| <0.5s | 0/36 (0%) |

**Notes:** Beat tracking detected 117.5 BPM (wrong — actual 181.5) on BGVS audio. Even with correct beats, alignment errors 4-9s >> beat interval 0.33s. GT slides are 56% on a beat (within 100ms). Not useful until alignment is already good.

### HIYH #24 — Guide.wav Demucs Vocal Extraction + stable-ts
**Date:** 2026-03-05
**Config:** Demucs htdemucs_ft on Guide.wav, then stable-ts small on extracted vocals

| Metric | Value |
|--------|-------|
| <0.5s | 2/36 (6%) |
| <1.0s | 3/36 (8%) |
| <2.0s | 5/36 (14%) |
| <5.0s | 11/36 (31%) |
| Per-section <0.5s | 8/36 (21%) |

**Notes:** Demucs took 307s. Click track artifacts contaminate extracted vocals. Most timestamps 10-30s wrong. Sparse lead vocal gives stable-ts less signal. Much worse than full-mix baseline.

---

## Batch Tests (continued)

### Batch #4 — dynamic_heads (small model)
**Date:** 2026-03-05
**Config:** small model, estimated windows, auto-offset, dynamic_heads=True
**Songs:** 10 tested (3 skipped), 1 duplicate (God So Loved)

| Metric | Value |
|--------|-------|
| <0.5s | 23% |
| <1.0s | 35% |
| <2.0s | 45% |
| <5.0s | 60% |
| Total time | 5 min |

| Song | <0.5s | <1s | Avg | Notes |
|------|-------|-----|-----|-------|
| Good Plans | 50% | 67% | 2.7s | Best |
| God So Loved | 45% | 64% | 2.4s | Good |
| As For Me And My House | 37% | 53% | 3.2s | OK |
| Here In Your House | 33% | 53% | 1.7s | OK |
| At The Cross | 12% | 16% | 8.9s | Poor |
| Christ Be Magnified | 12% | 19% | 9.1s | Poor |
| Because He Lives | 10% | 29% | 8.0s | Poor |
| Firm Foundation | 10% | 18% | 12.7s | Poor |
| Be Glad | 5% | 9% | 10.4s | Terrible |

**Notes:** WORSE than Batch #1 baseline (23% vs 30% <0.5s, 35% vs 54% <1.0s). dynamic_heads helps individual songs (HIYH +8%) but hurts the batch overall. Not a good default.

---

## Audio Analysis Tests — Here In Your House

### HIYH #25 — Music Structure Analysis (librosa SSM + novelty)
**Date:** 2026-03-05
**Config:** 5 novelty signals (spectral flux, chroma distance, RMS derivative, MFCC distance, SSM), peak detection for N-1 boundaries

| Metric | Value |
|--------|-------|
| <0.5s | 2/37 (5%) |
| Per-section oracle | 12% |

**Notes:** SSM novelty max=0.000 on single vocal stem. All individual signals averaged 21-42s boundary error. Structure detection doesn't work without drums/bass. Much worse than word-count estimated windows (25%/50%).

### HIYH #26 — Energy-Based Phrase Segmentation
**Date:** 2026-03-05
**Config:** RMS energy threshold=-21dB, min_gap=0.3s, min_phrase_dur=1.0s → exactly 36 phrases for 36 slides

| Metric | Value |
|--------|-------|
| <0.5s | 1/37 (3%) |
| Per-section oracle | 33% |
| Verse 1 oracle | 86% (with +1.0s offset) |

**Notes:** Phrase detection within Verse 1 nearly perfect. But mapping breaks down after blank slides — BGVS phrase boundaries don't match arrangement section boundaries. Sub-phrase detection within coarse regions was also worse (3%).

### HIYH #27 — Hybrid Energy + stable-ts
**Date:** 2026-03-05
**Config:** Energy phrase detection within stable-ts section windows; also GT windows; also median ensemble

| Method | <0.5s | Oracle |
|--------|-------|--------|
| Energy (Est Windows) | 0% | 25% |
| Energy (GT Windows) | 0% | 35% |
| Stable-ts (Est Windows) | 8% | 42% |
| Median (Energy + Stable-ts) | 5% | 24% |

**Notes:** Energy detection worse than stable-ts across all metrics. No auto-offset applied in comparison (oracle is the fair metric). Median ensemble also degraded.

### HIYH #28 — Multi-Model Ensemble (small + large Whisper)
**Date:** 2026-03-05
**Config:** stable-ts with both small and large models, best-per-section and median ensemble

| Method | <0.5s | Oracle | Time |
|--------|-------|--------|------|
| Small model | 8% (3/37) | 42% | 15.8s |
| Large model | 8% (3/37) | 36% | 69.1s |
| Best-per-section | 8% (3/37) | 45% | — |
| Median (small+large) | 8% (3/37) | 42% | — |

**Notes:** Small model won 27/37 slides vs large 6/37 on HIYH. Best-per-section chose small for 14/15 sections. Ensemble gives marginal oracle improvement (+3%) but no actual accuracy gain. No auto-offset applied. 4.5x slower with large model.

### HIYH #29 — Click Track Removal from Guide.wav
**Date:** 2026-03-05
**Config:** numpy subtraction of Click Track.wav from Guide.wav, also mixed with BG vocals

| Method | <0.5s | Oracle |
|--------|-------|--------|
| Click-removed Guide only | 0% (0/37) | 3% |
| Guide+BG vocal mix | 11% (4/37) | 45% |
| BG vocal mix (BGVS+Alto+Tenor) | 8% (3/37) | 48% |

**Notes:** Click subtraction creates phase artifacts worse than original click (avg delta 21.1s). Guide+BG mix is slightly better than either alone. BG vocal mix has best oracle (48%) but similar raw accuracy.

### HIYH #30 — Full Instrument Mix Alignment
**Date:** 2026-03-05
**Config:** All stems mixed (no Guide/Click), various combinations

| Method | <0.5s | Oracle |
|--------|-------|--------|
| BGVS only (baseline) | 8% (3/37) | 42% |
| Full mix (all 15 stems) | 3% (1/37) | 42% |
| Instruments only | 3% (1/37) | 24% |
| Vocal-heavy (vocals 3x + inst 1x) | 8% (3/37) | 42% |
| Drums + Vocals | 11% (4/37) | 45% |
| Median (5 sources) | 8% (3/37) | 42% |

**Notes:** More instruments = worse. Drums+vocals marginally best (+3% over BGVS). Full mix and instruments-only both terrible. Median ensemble doesn't help.

### HIYH #31 — lyrics-aligner (DTW-attention, MUSDB18-trained)
**Date:** 2026-03-05
**Config:** schufo/lyrics-aligner InformedOpenUnmix3, proper DTW path extraction

| Method | <0.5s | Oracle | Time |
|--------|-------|--------|------|
| BGVS, whole song, proper DTW | 8% (3/37) | 42% | 17.9s |
| Full mix, whole song, proper DTW | 8% (3/37) | 42% | 17.3s |
| BGVS, whole song, argmax (wrong) | 5% (2/37) | 24% | 14.5s |
| BGVS, section-by-section, argmax | 3% (1/37) | 16% | — |

**Notes:** Model trained on polyphonic music (MUSDB18) but no better than speech-trained Whisper. First 11 slides decent (within 2s), then DTW path diverges after Chorus 1 and never recovers. Whole-song DTW is fundamentally brittle for long songs with repeating sections.

### HIYH #32 — torchaudio MMS_FA Forced Alignment (approach #48)
**Date:** 2026-03-05
**Config:** torchaudio.functional.forced_align with MMS_FA pipeline, CTC token alignment

| Method | <0.5s | Oracle |
|--------|-------|--------|
| MMS_FA section-by-section | 0% (0/37) | 22% |
| MMS_FA whole song | 0% (0/37) | 14% |

**Notes:** CTC-based forced alignment with Meta's MMS_FA model places words systematically early in polyphonic audio. Zero slides within 0.5s. WAV2VEC2_ASR test failed (no get_dict method). CTC emission alignment fundamentally worse than Whisper cross-attention for singing.

### HIYH #33 — WhisperX Alignment with Known Lyrics (approach #49)
**Date:** 2026-03-05
**Config:** WhisperX 3.8.1, wav2vec2 CTC alignment, custom transcript segments with known lyrics

| Method | <0.5s | Oracle |
|--------|-------|--------|
| WhisperX section-by-section | 0% (0/37) | 31% |
| WhisperX whole song | 0% (0/37) | 3% |

**Notes:** WhisperX CAN accept known lyrics via transcript segments (contradicting previous note). But wav2vec2 CTC alignment worse than Whisper cross-attention. Whole-song alignment catastrophic (oracle 3%). Section-by-section oracle 31% suggests per-section offset is ~consistent but timestamps systematically wrong.

### HIYH #34 — BS-RoFormer Vocal Separation + stable-ts (approach #51)
**Date:** 2026-03-05
**Config:** BS-RoFormer (SDR 12.97, SOTA 2024) vocal separation from Guide.wav via audio-separator, then stable-ts alignment

| Method | <0.5s | Oracle |
|--------|-------|--------|
| BS-RoFormer Guide + small model | 0% (0/37) | 12% |
| BS-RoFormer Guide + large model | 0% (0/37) | 6% |
| BGVS baseline (small) | 8% (3/37) | 42% |

**Notes:** Even SOTA vocal separation (BS-RoFormer, SDR 12.97) cannot salvage Guide.wav — click track and spoken cues contaminate extracted vocals. Worse than Demucs htdemucs_ft attempt (#24). BGVS baseline remains far superior. Guide.wav is fundamentally unsuitable for vocal extraction.

---

## Corpus Offset Learning — 2 Songs (approach #50)
**Date:** 2026-03-05
**Config:** Leave-one-song-out cross-validation, per-section-type median offset correction, stable-ts small baseline
**Songs:** God So Loved (29 slides), Here In Your House (33 slides) — 62 total measurements

| Section Type | n | Median Offset | Std Dev | Useful? |
|-------------|---|---------------|---------|---------|
| Chorus | 26 | -20.80s | 10.74s | No (high variance) |
| Verse | 20 | -2.92s | 7.98s | No (high variance) |
| Bridge | 16 | -7.62s | 10.74s | No (high variance) |
| **Global** | **62** | **-9.86s** | **11.16s** | **No** |

| Correction Method | <0.5s |
|-------------------|-------|
| Before (no correction) | 4/62 (6%) |
| After (leave-one-out type offsets) | 2/62 (3%) — WORSE |
| Per-song oracle | 8/62 (13%) |
| Per-section-type oracle | 11/62 (18%) |

**Notes:** Offset correction made things WORSE. Variance is far too high (>10s std dev in every section type) for learned offsets to help. Even oracle (per-song per-type median applied to own data) only reaches 18%. The alignment errors are song-specific and section-instance-specific, not systematic by section type.

---

### HIYH #35 — LyricsAlignment-MTL (DALI singing-trained, approach #52)
**Date:** 2026-03-05
**Config:** ICASSP 2022 multi-task learning model (CTC + boundary detection), trained on DALI dataset (5000+ songs), CPU

| Method | <0.5s | Oracle | Time |
|--------|-------|--------|------|
| MTL+BDR section-by-section | 3% (1/37) | 21% | 12.3s |
| MTL+BDR whole song | 0% (0/37) | 30% | 29.6s |
| Baseline (no BDR) | 0% (0/37) | 9% | 7.6s |
| stable-ts baseline | 8% (3/37) | 42% | — |

**Notes:** Despite being trained on singing (DALI dataset), LyricsAlignment-MTL is much worse than speech-trained Whisper on our audio. The model was trained on separated vocals (from MUSDB18); our BGVS is raw polyphonic backing vocals. Boundary detection (BDR) helps slightly (21% vs 9% oracle) but can't overcome the audio quality gap.

### HIYH #36 — CREPE Pitch-Onset Correction (approach #53)
**Date:** 2026-03-05
**Config:** torchcrepe (tiny model) pitch tracking + snap stable-ts timestamps to pitch onsets

| Metric | Baseline | +CREPE 0.3s | +CREPE 0.5s | +CREPE 1.0s |
|--------|----------|-------------|-------------|-------------|
| <0.5s | 8% | 8% | 8% | 8% |
| Oracle | 42% | 42% | 42% | 42% |

CREPE pitch detection on BGVS:

| Section | Onsets Found | % Voiced |
|---------|-------------|----------|
| Verse 1 | 0 | 0% |
| Chorus 1 | 0 | 0% |
| Verse 2 | 4 | 3% |
| All others | 0 | 0% |
| **Total** | **4** | — |

**Notes:** CREPE is completely non-functional on polyphonic BG vocals. Detected 0% voicing in 8/9 sections. Only 4 pitch onsets in entire song vs 37 needed. GT median 60.8s from nearest onset. Monophonic pitch tracking requires isolated single voice — multiple singers + instruments produce no trackable pitch. CREPE snap had zero effect on any metric at any radius.

### HIYH #37 — tikick/LyricsAlignment Contrastive Learning (approach #55)
**Date:** 2026-03-06
**Config:** tikick contrastive alignment (NeurIPS 2024), dual audio+text encoder, 3 checkpoint variants, section-by-section

| Checkpoint | <0.5s | <1.0s | <2.0s | <5.0s | Avg | Oracle | Valid/Total |
|---|---|---|---|---|---|---|---|
| negBox_daliClean | 5% (2/37) | 11% | 27% | 27% | 7.46s | 48% | 21/37 |
| contrastive_daliClean | 0% | 16% | 27% | 32% | 6.64s | 48% | 21/37 |
| box_daliClean | 0% | 11% | 27% | 32% | 6.46s | 52% | 21/37 |

Per-slide detail (negBox_daliClean):
- Verse 1 (7 slides): 1 OK, 6 within ~1s (consistent +1s late bias)
- Chorus 1 first instance (4 slides): 1 OK (0.06s!), 2 within ~1s, 1 off by 5.8s
- Bridge sections (12 slides): ALL FAILED — phoneme alignment index error, 0/12 timestamps
- Later sections increasingly off (>5s deltas)

**Notes:** Model runs fast on CPU (~35s for all sections). Verse 1 alignment is surprisingly decent (~1s late). But Bridge sections fail entirely due to doubled lyrics ("Surely the Lord is Surely the Lord is") causing phoneme count mismatches in the DP alignment. Only 21/37 slides got timestamps. Worse than stable-ts baseline (5% vs 8% <0.5s). The contrastive approach doesn't overcome the fundamental polyphonic BG vocal challenge.

### Open-Source Alternatives Feasibility Assessment (2026-03-06)

| Tool | Status | Blocker |
|---|---|---|
| tikick/LyricsAlignment | TESTED | 5% <0.5s — worse than stable-ts |
| AutoLyrixAlign (MIREX winner) | NOT TESTABLE | Requires Singularity container (Linux only) |
| ASA_ICASSP2021 | NOT TESTABLE | Requires Kaldi + Docker + 35GB |
| ALT_SpeechBrain | DISCARDED | Transcription system, not alignment |
| E2E-LyricsAlignment | NOT TESTABLE | No pretrained checkpoints, requires PyTorch 1.4 |

**Conclusion:** Every testable open-source lyrics alignment tool has now been tested. The remaining untested ones (AutoLyrixAlign, ASA) require Linux container infrastructure that we don't have. 57 approaches exhausted, 30% batch ceiling unchanged.

---

## Ubuntu Server Tests (AutoLyrixAlign — Linux-only tool)

Tests run on Ubuntu Server 24.04.4 LTS at 10.10.11.157 (16-core Xeon, 96GB RAM, AMD RX 580 available but not used). Infrastructure set up 2026-03-06.

### Ubuntu #1 — AutoLyrixAlign (MIREX 2019+2020 Winner)

**Date:** 2026-03-06
**Tool:** NUSAutoLyrixAlign (Kaldi HMM/DNN, CNN-TDNN acoustic model trained on singing)
**Source:** autolyrixalign.zip (Singularity container kaldi.simg, 3.93GB)
**Audio:** BGVS.wav (4-part polyphonic backing vocals, 255.9s, no lead vocal)
**Lyrics:** 36 non-blank slides, 330 words (MIDI arrangement order)
**Runtime:** 100s (0.39x realtime — faster than real-time)
**Script:** `eval_autolyrixalign.py`

#### Raw Results (no offset correction)

| Metric | AutoLyrixAlign | Baseline (stable-ts small) |
|--------|---------------|---------------------------|
| <0.5s | 0/36 (0.0%) | 8% (3/37) |
| <1.0s | 1/36 (2.8%) | 32% |
| <2.0s | 1/36 (2.8%) | 43% |
| <5.0s | 9/36 (25.0%) | 57% |
| Avg delta | 6.98s | 4.8s |
| Median delta | 6.45s | — |

#### With Constant Offset Correction

The alignment is systematically late. Best constant offset found: **+5.00s → 13/36 (36.1%) <0.5s**.
This means AutoLyrixAlign finds actual vocal onsets ~5-10s AFTER the MIDI trigger fires.

Offset scan (1s steps):
```
+ 0s:  0/36 ( 0.0%)
+ 5s: 13/36 (36.1%)  ← best
+ 9s:  9/36 (25.0%)  ← secondary peak
```
The two peaks (+5s and +9s) correspond to different sections having different operator anticipation distances.

#### Per-Slide Output (truncated — see raw file)

```
  1  al=11.19  gt=10.413  d=0.78s   There's an echoing in the Spirit
  2  al=15.15  gt=11.736  d=3.41s   If you listen closely you'll hear it
...
 20  al=143.28 gt=135.661 d=7.62s   Surely the Lord is... (Bridge)
 21  al=147.15 gt=141.191 d=5.96s   Shout if you wanna...
...Bridge slides 20-31: delta range 4.70-6.05s (very consistent!)
...
 32  al=205.29 gt=192.002 d=13.29s  Here in Your house (Final Chorus)
```

#### Key Finding — Within-Section Precision

The **bridge section (slides 20-31)** has a delta range of only **4.70–6.05s (1.35s spread)**. AutoLyrixAlign is finding word positions with high RELATIVE accuracy within sections — the issue is the systematic per-section offset, not imprecise word detection.

Compare:
- Bridge section spread: 1.35s (12 slides)
- stable-ts oracle gap: ~5-6x between raw (8%) and oracle (42-50%) on HIYH

#### Root Cause Analysis

AutoLyrixAlign finds the actual vocal onset in BGVS.wav. The GT timestamps are when the **MIDI operator fires the trigger** — which is based on hearing the **lead vocal or instrument cue**, not the backing vocals. The BG vocals start 5-13s AFTER the operator triggers. This is not an alignment accuracy problem — it's a fundamental mismatch between the audio source (BG vocals) and the GT reference (lead vocal cue timing).

#### Verdict

AutoLyrixAlign **works correctly** on polyphonic BG vocal audio. It is finding the actual word onsets accurately. However:
1. Raw accuracy is **worse** than stable-ts baseline (0% vs 8% <0.5s)
2. The systematic offset (5-10s late) is caused by backing vocals starting after the musical cue the operator uses for MIDI trigger timing
3. With a known per-section offset, accuracy would be **much better** — but per-section offsets are not learnable (confirmed by 57-approach research)
4. The MIREX polyphonic audio claims are valid — the tool works on polyphonic BG vocals — but the reference audio timing problem makes it unusable for predicting MIDI triggers

**Recommendation:** AutoLyrixAlign does not solve our problem as-is. However, it could be useful as a **verification tool** (checking that produced MIDI triggers fire before the actual vocal onset) or if a different GT reference is available (e.g., stems with isolated lead vocal onset markers).

---

### Ubuntu #2 — AutoLyrixAlign Section-by-Section (HIYH full mix)

**Date:** 2026-03-06
**Tool:** NUSAutoLyrixAlign (same Kaldi container as Ubuntu #1)
**Audio:** HIYH_full_mix.wav (all 15 stems, 255.9s)
**Method:** 9 sections, each audio-cropped with ffmpeg, aligned independently, timestamps adjusted by section start offset
**Runtime:** 424s total across 9 sections (~47s per section — consistent regardless of section length)
**Script:** `eval_ala_sections.py`
**Hypothesis tested:** Repeated section confusion (Kaldi aligning Verse 1 words to Verse 2 audio) — isolating each section should prevent this

#### Section Windows

| Section | Start–End | Words | ALA Result (first slide) |
|---------|-----------|-------|--------------------------|
| Verse1 | 11.9–49.6s | 53 | al=11.90, gt=10.413, d=1.49s ← good start |
| Chorus1 | 43.6–77.4s | 38 | (section start ~43.6s) |
| Verse2 | 77.4–107.1s | 32 | — |
| Chorus2 | 107.1–134.9s | 38 | — |
| Bridge1–3 | 134.9–206.3s | 42 each | — |
| Chorus3 | 190.4–234.1s | 38 | al=190.44, gt=192.002, d=1.56s ← good start |
| Outro | 234.1–256.0s | 5 | al=234.05, gt=234.756, d=0.71s ← very good |

#### Raw Results (no offset correction)

| Metric | Section-by-Section ALA | Whole-Song ALA (BGVS) | Baseline (stable-ts) |
|--------|------------------------|----------------------|----------------------|
| <0.5s | 0/36 (0.0%) | 0/36 (0.0%) | 8% |
| <1.0s | 1/36 (2.8%) | 1/36 (2.8%) | 32% |
| <2.0s | 3/36 (8.3%) | 1/36 (2.8%) | 43% |
| <5.0s | 9/36 (25.0%) | 9/36 (25.0%) | 57% |
| Avg delta | 6.30s | 6.98s | 4.8s |
| Median delta | 5.99s | 6.45s | — |

#### With Constant Offset Correction

Best constant offset: **+5.00s → 13/36 (36.1%) <0.5s** — identical to whole-song result.

Offset scan shows two peaks:
- +5s: 13/36 (36.1%) — primary cluster
- +9s: 8/36 (22.2%) — secondary cluster (Bridge + later Chorus slides)

#### Key Finding — Section-Start Reset Behavior

Each new section's **first slide** is consistently good:
- Verse1 slide 1: 1.49s delta (section starts at 11.9s, ALA snaps to 0.00 local = 11.90s global)
- Chorus3 slide 1: 1.56s delta (section starts at 190.41s, ALA snaps near 0.00 local)
- Outro slide 1: 0.71s delta (section starts at 234.05s, ALA snaps near 0.00 local)

Then within each section, ALA drifts progressively later. By the last slide of Verse1, delta is 8.99s.

**This confirms the root cause is NOT repeated-section confusion.** The problem is within-section drift — ALA's Viterbi alignment stretches word spacing unevenly across the audio clip, accumulating error as it progresses through the section.

#### Why Section Isolation Didn't Help

The repeated-section theory (Kaldi drifts into Verse 2 when processing Verse 1) was incorrect. The actual drift pattern:
1. Section starts: ALA places first words at the very beginning of the clip (~0.00 local) regardless of any instrumental intro
2. Drift: ALA spreads subsequent words across the clip, but later words land too far into the clip relative to GT
3. Same total error accumulated whether run whole-song or section-by-section

The consistency of the +5s best offset and the 36.1% accuracy (with offset) being identical to whole-song confirms section isolation provides no benefit.

#### Verdict

Section-by-section AutoLyrixAlign produces **identical results** to whole-song alignment. Both achieve 0% raw / 36.1% with +5s offset. The within-section drift problem is fundamental to Kaldi's Viterbi alignment on polyphonic audio — not caused by section confusion. No further ALA variants worth testing.
