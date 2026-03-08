# Lyrics-to-Audio Alignment Research — Full Summary

**Project:** MIDI Automation (Northwoods Community Church)
**Machine:** graphics-mac (10.10.11.77, Apple M-series, 24GB RAM, macOS)
**Period:** Feb–Mar 2026
**Approaches tested:** 57
**Status:** Hard ceiling reached on macOS — Linux testing phase beginning

---

## What We're Solving

The MIDI Automation app generates MIDI files that fire ProPresenter slide triggers in sync with live worship music. Every song already has an Ableton project with stems and a MIDI file that was placed by hand.

**The goal:** Automatically reproduce those hand-placed MIDI triggers by aligning the known lyrics (from ProPresenter) to the pre-recorded backing vocal audio. Each trigger must fire close enough to the right moment that the slide is visible before the congregation sings it.

**Why it's hard:**
- The audio is **polyphonic backing vocals** — 4 singers simultaneously, no isolated lead vocal
- The lead vocal is only in `Guide.wav` mixed with a click track and spoken cues — not cleanly separable
- Songs have repeated sections (Chorus twice, Bridge four times) — standard whole-song alignment fails
- Ground truth MIDI triggers fire 1.5–5s before the actual lyric onset — timing varies per section
- All available alignment tools are trained on speech or clean separated vocals

---

## Test Infrastructure

### Primary test machine
- `graphics-mac` (10.10.11.77) — macOS, 24GB RAM, Apple Silicon
- Python 3.12, venv at `test_data/.venv/`
- All 57 approaches run here

### Primary benchmark song
**"Here In Your House"** (F, 181.5 BPM, 255.9 seconds)
- 15 arrangement sections, 37 slides with ground truth timestamps
- `BGVS.wav` — 4-part backing vocals, the primary alignment audio
- Ground truth extracted from hand-placed MIDI `.als` file

### Batch test set
13 songs across a range of musical styles and difficulty levels.

---

## Accuracy Metrics

Every test reports four thresholds plus per-section oracle:

| Metric | Meaning |
|--------|---------|
| **<0.5s** | Slide fires within half a second of GT — considered "accurate" |
| **<1.0s** | Within one second |
| **<2.0s** | Within two seconds |
| **<5.0s** | Within five seconds |
| **Avg delta** | Mean absolute error across slides with valid timestamps |
| **Per-section oracle** | Best accuracy achievable if each section had a perfect constant-offset correction applied — indicates upper bound of what post-processing could achieve |

The **per-section oracle** is the key insight metric. If oracle is 50% but raw is 8%, the model is placing words correctly *within* sections but each section is offset by a systematic amount. If oracle is also low, the alignment is fundamentally wrong.

---

## The Baseline

**stable-ts forced alignment (`small` Whisper model, section-by-section)**

Forced alignment means: we supply the known lyrics text and tell the model "find WHEN these words occur in the audio." This is categorically better than transcription (asking the model what words it hears).

Section-by-section: each arrangement section is aligned independently against a cropped audio window. This handles repeated lyrics — the same Chorus text can appear multiple times in the song.

| Metric | Single song (HIYH) | 13-song batch |
|--------|-------------------|---------------|
| <0.5s | 25% | **30%** |
| <1.0s | 56% | 54% |
| <2.0s | 81% | 67% |
| <5.0s | 94% | 76% |
| Avg error | 1.6s | 4.1s |
| Oracle | 50% | — |

This is the number every approach below is measured against.

---

## All Approaches Tested

### Category 1 — Model Selection

| # | Approach | HIYH <0.5s | Batch <0.5s | Notes |
|---|----------|-----------|------------|-------|
| 1 | WhisperKit transcription + SlideAligner | ~5% | — | Abandoned early. Transcription fails on sung polyphonic audio. |
| 2 | stable-ts base model | Worse | — | 74MB too small to hear vocals through instruments |
| **3** | **stable-ts small (BASELINE)** | **25%** | **30%** | **Current best** |
| 4 | stable-ts large model | 17% | Mixed | Helps some songs (Good Plans 81%), destroys others (HIYH 17%). Not a default. |
| 5 | stable-ts medium model | 22% | — | Systematically +5.8s late. Worse than small. |

### Category 2 — Whisper Parameter Tuning

| # | Approach | HIYH <0.5s | Oracle | Notes |
|---|----------|-----------|--------|-------|
| 26 | `dynamic_heads=True` | **33%** | 47% | Best single-parameter improvement (+8%). Runtime head selection adapts to audio. |
| 24 | `only_voice_freq=True` | 22% | 50% | Slightly worse. Removing bass/drums loses timing cues. |
| 25 | `fast_mode=True` | 17% | 42% | "Failed to align last N words" errors. Too aggressive for singing. |
| 29 | `word_dur_factor=4.0` | 31% | 47% | <0.5s slightly lower, <1.0s improves to 61%. Trade-off. |
| 28 | Repeated-section equalization | No change | — | HIYH has no exact repeated section names. Would need batch test. |
| 33 | `dynamic_heads` on batch | — | 23% batch | WORSE on batch. Helps HIYH, hurts others. Per-song optimization only. |

### Category 3 — Post-Processing

| # | Approach | HIYH <0.5s | Notes |
|---|----------|-----------|-------|
| 9 | `model.refine()` | Worse | Speech refinement hurts music. Added 20-60s per section. Removed. |
| 14 | Beat-snapping (librosa) | 25% (same) | Beats every 0.33s at 181.5 BPM — too dense. Alignment errors (4-9s) >> beat interval (0.33s). |
| 27 | Inter-section continuity chaining | 33%, <1.0s **39%** | Catastrophic at wider tolerances. Errors compound across sections. Removed. |
| 30 | Two-phase alignment | 17%, <1.0s **67%** | <0.5s worse, <1.0s best ever. Window refinement helps coarse, hurts precise. |
| 31 | Slide-level refinement (±5s) | 28% | WORSE. Loses section context and ordering constraints. Removed. |
| 13 | Onset snapping (0.3s) | 25% (same) | No improvement as standalone. |
| 11 | Word-weighted proportional | 30% (batch same) | No measurable improvement. |
| 12 | Template matching (repeated sections) | 30% (batch same) | No improvement. |
| 18 | Running-average pace correction | Part of baseline | Marginal help. Stays in code. |

### Category 4 — Window Estimation

| # | Approach | HIYH <0.5s | Notes |
|---|----------|-----------|-------|
| 15 | Ground-truth windows (oracle) | **22% (same)** | **KEY FINDING: Window estimation is NOT the bottleneck.** Giving the model perfect windows produced the same result as estimated windows. |
| 35 | Guide.wav spoken cues for windows | 25% (same) | 17 cues found, 8/15 matched. Cue matching errors on closely-spaced section announcements. |

### Category 5 — Audio Source Selection

| # | Approach | HIYH <0.5s | Oracle | Notes |
|---|----------|-----------|--------|-------|
| 34 | Guide.wav direct alignment | — | 28% | Click track confuses Whisper more than full band. Much worse. |
| 46a | Full instrument mix (all stems) | 3% | 42% | More instruments = more interference. |
| 46b | Instruments only | 3% | 24% | Whisper hallucinates words in instrument sounds. |
| 46c | Drums + Vocals | 11% | 45% | Marginally better (+3%). Not significant. |
| 46d | Vocal-heavy (vocals 3x boost) | 8% | 42% | No change. Backing vocal boost doesn't help. |
| 23 | Vocal-boosted mix (--vocal-boost 3) | 19% | 53% | Close to baseline. Lead vocal not in stems. |
| 45 | Click track subtraction | 0% | 3% | Phase artifacts from imperfect subtraction. Catastrophic. |
| 36 | Transcription mode | — | — | Only 8/36 slides matched. Global offset +157s. Catastrophic. |

### Category 6 — Vocal Separation (Demucs)

Demucs separates vocals from a mix. The critical finding: our vocal stems are **backing vocals only** — the lead vocal is only in `Guide.wav` mixed with a click track.

| # | Approach | HIYH <0.5s | Notes |
|---|----------|-----------|-------|
| 7 | Demucs as primary audio (Guide.wav) | 11% | Catastrophic. Click track artifacts dominate. |
| 20 | Demucs mix (all-stems, dual-audio fallback) | 25% (same) | Dual-audio always chose original (quality > 0.75 threshold). |
| 22 | Demucs-primary (all-stems) | 17% | Worse. Demucs sees mostly BG vocals; verses have near-silence (lead not in stems). |
| 40 | Guide.wav → Demucs → stable-ts | 6% | 21% oracle. Click contamination survives separation. Much worse. |
| 51 | Guide.wav → BS-RoFormer (SOTA SDR 12.97) → stable-ts | 0% | 12% oracle. Even 2024 SOTA separation can't clean up Guide.wav (non-musical elements). |
| 6 | Demucs on 5 songs (large model) | Mixed | Helped 2/5 (+28-42%), hurt 3/5 (-9 to -20%). Not a reliable default. |

### Category 7 — Audio Onset Detection

| # | Approach | HIYH <0.5s | Oracle | Notes |
|---|----------|-----------|--------|-------|
| 21a | Onset anchoring v1 (bandpass threshold) | 3% | 18% | Instruments have energy in vocal band. All 8 sections shifted wrong. |
| 21b | Onset anchoring v2 (step-up detection) | 17% | 50% | 2 corrections vs 8. Still wrong — silence→instrument triggers false onset. |
| 21c | Onset anchoring v3 (Demucs onset) | 19% | 50% | Detects BG vocal entry. BG vocals start later than lead for verses. |
| 41 | Music structure analysis (SSM, novelty) | 5% | 12% | Structure boundaries 11-35s off. Works on full mix, not single vocal stem. |
| 42 | Energy phrase segmentation | 3% | 33% | Verse 1 86% oracle alone. Falls apart after section boundaries. |
| 43 | Hybrid energy + stable-ts | 5% | 24% | All variants worse than stable-ts alone. |
| 39 | Beat-informed snapping | 0% | — | Beat tracking wrong on BGVS (no percussion). Errors >> beat interval. |

### Category 8 — Multi-Model Ensemble

| # | Approach | HIYH <0.5s | Oracle | Notes |
|---|----------|-----------|--------|-------|
| 44 | Small + Large ensemble (best-per-section) | 8% | 45% | No improvement. Small model dominant (27/37 slides closer). 4.5x slower. |
| 44 | Median (small + large) | 8% | 42% | No change. Not worth it. |

### Category 9 — Alternative Alignment Models (Speech-Trained)

| # | Approach | HIYH <0.5s | Oracle | Notes |
|---|----------|-----------|--------|-------|
| 19 | CTC forced aligner (ONNX, wav2vec2) | — | — | Words 7-26s too early. 3x slower. Catastrophic. |
| 32 | `initial_prompt` parameter | FAILED | — | Only works with `transcribe()`, not `align()`. Incompatible. |
| 37 | Qwen3-ForcedAligner-0.6B | 0% | 39% | Speech-trained, can't locate words in polyphonic singing. |
| 38 | Qwen3-ASR transcription + matching | 0% | — | Word matching works but timestamps 5-10s late. |
| 48 | torchaudio MMS_FA (Meta) | 0% | 22% | CTC on speech model places words systematically early. |
| 49 | WhisperX (wav2vec2 alignment) | 0% | 31% | wav2vec2 worse than Whisper cross-attention for singing. |

### Category 10 — Singing-Trained Models

These are models trained on singing data specifically, not speech.

| # | Approach | HIYH <0.5s | Oracle | Notes |
|---|----------|-----------|--------|-------|
| 47 | lyrics-aligner (DTW, MUSDB18-trained) | 8% | 42% | Same as baseline! Diverges after first chorus. Trained on separated vocals. |
| 52 | LyricsAlignment-MTL (DALI, CTC+BDR) | 3% | 21% | ICASSP 2022. 5000+ songs training. Still fails on raw polyphonic BG vocals. |
| 53 | CREPE pitch-onset correction | 8% (no change) | 42% | 0% voicing detected in 8/9 sections. Polyphonic audio defeats monophonic pitch tracking. |
| 55 | tikick contrastive (NeurIPS 2024) | 5% | 48% | Dual encoder + DP alignment. DALI-trained. Better oracle but worse raw than baseline. Bridge sections fail entirely. |

### Category 11 — Learned Corrections

| # | Approach | Result | Notes |
|---|----------|--------|-------|
| 16 | Cross-correlation (full spectrum) | 28% HIYH | Full spectrum dominated by drums. Chorus shifted -3.6s wrong. |
| 17 | Cross-correlation (vocal band 300-3000Hz) | 30% batch (same) | SNR threshold too high — most corrections filtered out. |
| 50 | Per-section-type offset learning | WORSE (6%→3%) | Std dev >10s per section type. Chorus offset varies from -37s to +1s. Not learnable. |

### Category 12 — Not Viable / Not Testable on macOS

| Tool | Reason not testable |
|------|---------------------|
| SOFA | Mandarin-only, no English model |
| lyrics-sync | CUDA 11.7 locked, no Apple Silicon support |
| lyrics-aligner (old) | Requires Python 3.6, PyTorch 1.5, CUDA 9.2 |
| AudioShake LyricSync | Commercial cloud API (~$1/min), no local install |
| CrisperWhisper | Requires custom HuggingFace fork, incompatible with stable-ts |
| STARS (ACL 2025) | Chinese-only |
| Montreal Forced Aligner | Confirmed poor on singing in literature |
| NeMo | Limited macOS/Apple Silicon support, no singing models |
| AutoLyrixAlign | Requires Singularity container (Linux only) |
| ASA_ICASSP2021 | Requires Kaldi + Docker + 35GB |
| E2E-LyricsAlignment | No pretrained checkpoints published |
| ALT_SpeechBrain | Transcription system, outputs text not timestamps |

---

## Key Findings Summary

### What we know for certain

1. **Section-by-section is essential** — whole-song alignment fails completely on songs with repeated sections (same Chorus text aligned correctly requires knowing which time range to search in).

2. **Forced alignment beats transcription by a huge margin** — transcription on polyphonic music gets ~8/36 slides matched (22%). Forced alignment (known lyrics as input) gets 25-33% <0.5s on the same audio. Always use forced alignment.

3. **Window estimation is not the bottleneck** — giving the model perfect section windows produced identical results to estimated windows. The alignment quality within windows is the constraint.

4. **Cross-attention (Whisper) outperforms CTC for singing** — every CTC-based approach (wav2vec2, MMS, WhisperX) performed worse than Whisper cross-attention. Cross-attention finds words in context; CTC emits probabilities at each frame and fails when the acoustic model doesn't recognize sung phonemes.

5. **No vocal separation approach consistently helps** — Demucs helped 2/5 songs and hurt 3/5. BS-RoFormer (2024 SOTA) was worse than Demucs. The problem: lead vocal is in `Guide.wav` which has a click track that survives separation; backing vocal stems have no lead vocal at all.

6. **Singing-trained models still fail on polyphonic BG vocals** — LyricsAlignment-MTL and tikick were both trained on the DALI singing dataset (5000+ songs). Both got worse accuracy than speech-trained Whisper. Reason: DALI training used separated vocal stems, not polyphonic audio. Our BG vocals are unseparated multi-voice audio.

7. **Audio onset detection cannot find vocal onsets in polyphonic music** — every onset detection approach (bandpass energy, step-up detection, Demucs-separated onset, CREPE pitch tracking) failed. Instruments share the vocal frequency band; polyphonic singing has no dominant single pitch for CREPE to track.

8. **Per-section errors are not learnable** — section offsets vary from -37s to +1s within the same section type (Chorus) across songs. Standard deviation >10s within section types. Leave-one-out offset learning made accuracy WORSE.

9. **No lead vocal stem exists in the corpus** — searched all 227 projects on the Creative Arts volume. Zero have isolated lead vocal. MultiTrack providers deliberately exclude lead vocals to prevent re-recording. Guide.wav (lead + click) is the only source, and it's too noisy to separate cleanly.

10. **30% <0.5s is the hard ceiling on macOS with available tools** — held across 3 independent batch runs with different parameters and approaches.

### The oracle gap is the key insight

- Raw accuracy: 8% <0.5s (on HIYH with current best single-song config)
- Per-section oracle: 42-50% <0.5s

This 5-6x gap means the model is finding words correctly *within* sections, but each section's position is off by a systematic amount. If we could correct each section's offset, accuracy quintuples. But that offset varies unpredictably per song and per section-instance.

---

## Batch Results — All Runs

| Run | Config | <0.5s | <1.0s | <2.0s | <5.0s | Avg |
|-----|--------|-------|-------|-------|-------|-----|
| Batch #1 | small, conservative pace correction | **30%** | **54%** | **67%** | **76%** | **4.1s** |
| Batch #2 | + quality 0.15 + template + word-weight + onset snap | 30% | 51% | 67% | 77% | 4.7s |
| Batch #3 | + vocal-band xcorr offset correction | 30% | 50% | 67% | 77% | 4.7s |
| Batch #4 | dynamic_heads | 23% | 35% | 45% | 60% | — |

No configuration has beaten the original baseline of 30% <0.5s on the batch. `dynamic_heads` is the only parameter that helps an individual song (HIYH +8%) but it hurts the batch overall.

---

## Single-Song Results — Here In Your House

Full chronological record of every HIYH test:

| Test | Config | <0.5s | <1.0s | <2.0s | Oracle | Time |
|------|--------|-------|-------|-------|--------|------|
| #1 | small baseline | 25% | 56% | 81% | 50% | — |
| #2 | large + Demucs Guide | 11% | 14% | 25% | — | — |
| #3 | medium model | 22% | 39% | 69% | — | — |
| #4 | medium + GT windows | 22% | — | — | — | — |
| #5 | beat-snapping | 25% | 56% | 81% | — | — |
| #6 | xcorr full spectrum | 28% | 47% | 75% | — | — |
| #7 | xcorr vocal-band | 33% | 60% | 79% | 51% | — |
| #8 | Demucs-mix fallback | 25% | 56% | 81% | 50% | — |
| #9 | onset v1 (bandpass) | 8% | 8% | 17% | 50% | — |
| #10 | onset v2 (step-up) | 17% | 47% | 56% | 50% | — |
| #11 | onset v3 (Demucs) | 19% | 22% | 28% | 50% | — |
| #12 | Demucs primary | 17% | 31% | 61% | 36% | — |
| #13 | vocal-boost 3x | 19% | 50% | 81% | 53% | — |
| #14 | only_voice_freq + fast_mode | 17% | 50% | 78% | 42% | — |
| #15 | only_voice_freq | 22% | 44% | 81% | 50% | — |
| **#16** | **dynamic_heads** | **33%** | **53%** | **72%** | **47%** | — |
| #17 | continuity chaining | 33% | 39% | 56% | 50% | — |
| #18 | word_dur_factor=4.0 + dyn_heads | 31% | 61% | 78% | 47% | 1.4s |
| #19 | two-phase + dyn_heads | 17% | **67%** | 75% | 50% | — |
| #20 | slide-level refinement | 28% | 47% | 61% | 36% | 2.1s |
| #21 | Qwen3-ForcedAligner | 0% | 0% | 11% | 39% | — |
| #22 | Qwen3-ASR + matching | 0% | 3% | 3% | — | 8.3s |
| #23 | beat-snapping (librosa) | 0% | — | — | — | — |
| #24 | Guide Demucs + stable-ts | 6% | 8% | 14% | 21% | — |
| #25 | Music structure SSM | 5% | — | — | 12% | — |
| #26 | Energy phrase seg | 3% | — | — | 33% | — |
| #27 | Hybrid energy + stable-ts | 5% | — | — | 24% | — |
| #28 | CTC forced-aligner (ONNX) | — | — | — | — | 3x slow |
| #29 | Guide.wav direct alignment | — | — | — | 28% | — |
| #30 | Transcription mode | — | — | — | — | 8/36 matched |
| #31 | Multi-model ensemble | 8% | — | — | 45% | — |
| #32 | torchaudio MMS_FA | 0% | — | — | 22% | — |
| #33 | WhisperX wav2vec2 | 0% | — | — | 31% | — |
| #34 | BS-RoFormer separation | 0% | — | — | 12% | — |
| #35 | LyricsAlignment-MTL | 3% | — | — | 21% | 12.4s |
| #36 | CREPE pitch-onset snap | 8% (no change) | — | — | 42% | — |
| #37 | tikick contrastive | 5% | 11% | 27% | 48% | 35s |

---

## What's Left to Try

### On Ubuntu (pending)
These three tools couldn't be tested on macOS. The Ubuntu server (16-core Xeon, 96GB RAM) enables them. See `PRD_ubuntu_alignment_testing.md` for the full spec.

| Tool | Why promising | Blocker on macOS |
|------|--------------|-----------------|
| **AutoLyrixAlign** | MIREX 2019+2020 winner, <200ms AAE on polyphonic, purpose-built for this problem | Singularity container (Linux only) |
| **ASA_ICASSP2021** | Kaldi + recursive anchoring for long recordings, ICASSP 2021 | Requires Kaldi compilation + Docker |
| **E2E-LyricsAlignment** | Wave-U-Net on polyphonic audio, no separation needed | No pretrained checkpoints published; PyTorch 1.4 |

### AudioShake LyricSync (commercial)
Cloud API, ~$1/min. Claims >95% word-level accuracy. Purpose-built by a music tech company used by Disney, major labels. This is the commercial alternative if open-source can't reach the target.

---

## Code and Files

| File | Purpose |
|------|---------|
| `test_data/align_sections.py` | Core alignment library — section-by-section forced alignment |
| `test_data/test_song.py` | End-to-end test for one song |
| `test_data/test_batch.py` | Multi-song batch test runner |
| `test_data/compare_alignment.py` | Compare alignment output vs ground truth with offset |
| `test_data/extract_ground_truth.py` | Parse Ableton `.als` MIDI files for GT timestamps |
| `test_data/fetch_lyrics.py` | Pro7 REST API client + lyrics cache |
| `test_data/lyrics_cache/` | 16 songs cached (Pro7 lyrics + MIDI arrangement) |
| `test_data/ALIGNMENT_APPROACHES.md` | Every approach tried with full detail |
| `test_data/TEST_RESULTS.md` | Every test run with full metrics |
