# Alignment Approaches — What We've Tried

Tracks every approach attempted to improve lyrics-to-audio alignment accuracy.
Updated every time we try something new.

---

## Current Best: Section-by-Section Forced Alignment (stable-ts small)

**Batch accuracy (13 songs):** 30% <0.5s, 54% <1s, 67% <2s, 76% <5s, avg 4.1s

---

## Approaches Tried

### 1. WhisperKit Transcription + SlideAligner
**Status:** ABANDONED
**Result:** Bad — slides clustered at wrong positions
**Why:** Whisper transcribes poorly with instruments. SlideAligner had 4 bugs (filtered "I"/"a", narrow search window, strict thresholds, aggressive cursor advance). Fixed bugs but transcription accuracy was the bottleneck.

### 2. stable-ts Forced Alignment (base model)
**Status:** ABANDONED
**Result:** Better than WhisperKit but bridges missing
**Why:** base model (74MB) too small to hear vocals through instruments.

### 3. stable-ts Forced Alignment (small model) + Sequential Mapping
**Status:** CURRENT BASELINE
**Result:** 30% <0.5s across 13 songs
**Why it's better:** Forced alignment = "here are the words, find WHEN they occur" vs transcription. Sequential word counting avoids fuzzy matching.

### 4. Whisper large model
**Status:** TESTED — mixed results, not default
**Result:** Some songs much better (Good Plans 81% vs 64%), some worse (At The Cross 60%→12%)
**Why mixed:** Large model is better at hearing vocals through instruments but sometimes over-fits to wrong audio sections.

### 5. Whisper medium model
**Status:** TESTED — worse than small
**Result:** 22% <0.5s on Here In Your House (was 25% with small)
**Why:** Medium model systematically places words 5-6s later. Global offset +5.8s vs +0.7s with small.

### 6. Demucs Vocal Separation (htdemucs)
**Status:** TESTED — inconsistent, not default
**Result:** Helped 2/5 songs dramatically (Christ Be Magnified 8%→50%, At The Cross 12%→40%), hurt 3/5 (Good Plans 81%→61%, Be Glad 48%→36%, Because He Lives 14%→5%)
**Why inconsistent:** Guide.wav has click track + spoken cues that bleed through separation. BG vocal stems lack lead vocal for verses.

### 7. Demucs as PRIMARY audio (not fallback)
**Status:** TESTED — catastrophically bad
**Result:** 11% <0.5s on Here In Your House (was 25%), auto-offset -9.5s
**Why:** Demucs vocal separation from Guide.wav too noisy. Click track artifacts dominate.

### 8. Demucs htdemucs_ft (fine-tuned model)
**Status:** CHANGED — upgraded from htdemucs to htdemucs_ft
**Result:** Not batch-tested yet in dual-audio fallback mode
**Why:** Fine-tuned model should give better separation quality.

### 9. model.refine() (stable-ts post-alignment)
**Status:** TESTED AND REMOVED
**Result:** Hurt accuracy, added 20-60s per section
**Why:** refine() probes audio by muting sections — designed for speech, not singing. Made things worse for music.

### 10. Quality threshold lowered (0.3 → 0.15)
**Status:** IN CODE — no measurable batch improvement
**Result:** 30% <0.5s (same as baseline)
**Why:** More sections pass quality check, but those sections weren't bad enough to benefit from re-alignment.

### 11. Word-weighted proportional timing
**Status:** IN CODE — no measurable batch improvement
**Result:** 30% <0.5s (same as baseline)
**Concept:** Distribute slide times proportionally by word count within each section instead of evenly.

### 12. Template matching for repeated sections
**Status:** IN CODE — no measurable batch improvement
**Result:** 30% <0.5s (same as baseline)
**Concept:** If same section appears multiple times (e.g., Chorus), use the best-quality alignment as a template for others with time offset.

### 13. Onset snapping (0.3s)
**Status:** IN CODE — no measurable batch improvement
**Result:** Part of combined changes, 30% <0.5s
**Concept:** Snap aligned word times to nearest audio onset within 0.3s.

### 14. Beat-snapping (librosa)
**Status:** TESTED AND REMOVED
**Result:** 25% <0.5s on Here In Your House (same as baseline)
**Why:** At 181.5 BPM, beats every 0.33s, half-beats every 0.165s — too dense to constrain. Every aligned time is already within 0.08s of a beat. Also librosa detected half-tempo (123 BPM).

### 15. Ground-truth windows (oracle test)
**Status:** TESTED — proved windows aren't the bottleneck
**Result:** 22% <0.5s (same as estimated windows!)
**Key finding:** Window estimation is NOT the bottleneck. Alignment quality within windows is the limiting factor.

### 16. Cross-correlation offset correction (full spectrum)
**Status:** TESTED — mixed, sometimes wrong
**Result:** 28% <0.5s on Here In Your House (was 25%), but 47% <1s (was 56%)
**Why:** Full-spectrum RMS dominated by drums/bass. Chorus 1 got shifted -3.6s incorrectly.

### 17. Cross-correlation offset correction (vocal-band 300-3000Hz)
**Status:** IN CODE — too conservative to help
**Result:** 30% <0.5s batch (same as baseline). SNR >= 5.0 filters out almost all corrections.
**Why:** With high SNR threshold, "(no xcorr corrections applied)" for most songs. Threshold must be high to avoid false corrections.

### 18. Running-average pace correction
**Status:** IN CODE — marginal help
**Concept:** Track drift from well-aligned sections, apply correction when avg > 2.0s with 2+ samples.
**Result:** Part of baseline 30% <0.5s.

---

## Approaches NOT YET Tried

### 19. CTC Forced Aligner (wav2vec2/MMS ONNX)
**Status:** TESTED — much worse than Whisper
**Date:** 2026-03-05
**Tool:** `ctc-forced-aligner` pip package (ONNX backend)
**Result:** CTC places words 7-26s too early across most sections. Also 3x slower (6-7s vs 2s per section).
**Why:** MMS model trained on speech, completely wrong for polyphonic music. Different model = different failure mode, but this particular failure mode is worse than Whisper's.

### 20. Demucs on all-stems mix (--demucs-mix)
**Status:** TESTED — no improvement
**Date:** 2026-03-05
**Concept:** Run Demucs htdemucs_ft on the all-stems mix (no click track) instead of Guide.wav. Should give cleaner vocal separation.
**Result:** 25% <0.5s on Here In Your House — identical to baseline. Dual-audio logic always chose main audio because main quality was consistently > 0.75 threshold.
**Why:** The all-stems mix already has decent vocals. Demucs separation doesn't provide enough additional clarity to beat the original. The dual-audio quality threshold (0.75) prevents switching to Demucs vocals when the main audio is "good enough".

### 21. Vocal Onset Anchoring (3 variants)
**Status:** TESTED AND REMOVED — all variants made accuracy worse
**Date:** 2026-03-05
**Concept:** Detect first vocal onset in each section's audio window, shift entire section to anchor on it. Targets the per-section offset (biggest error source).
**Variants tested:**
- **v1 (bandpass absolute threshold):** 8% <0.5s on HIYH (was 25%). Vocal-band (300-3000Hz) energy above 25% of peak. Fires on instruments at window start — guitars/keyboards have significant energy in this band.
- **v2 (bandpass step-up detection):** 17% <0.5s. Looks for energy INCREASE above recent baseline (1.5x ratio + 1s sustain). More conservative (only 2 corrections) but still wrong — silence→instrument transition triggers false onset.
- **v3 (Demucs-separated onset):** 19% <0.5s. Uses Demucs htdemucs_ft vocals for onset detection. Cleaner signal but Demucs separates BACKING vocals (not lead) from all-stems mix. Onset detected at backing vocal entry, which is later than lead vocal for verses.
**Why all failed:** In polyphonic worship music, (a) instruments have significant energy in vocal band, (b) Demucs separates backing not lead vocals, (c) the mapping from audio onset to ideal MIDI trigger time is inconsistent across sections.

### 22. Demucs-primary alignment (--demucs-primary)
**Status:** TESTED — worse than baseline
**Date:** 2026-03-05
**Concept:** Run Demucs on all-stems mix, use separated vocals as PRIMARY alignment audio (not fallback). No instrument interference for Whisper.
**Result:** 17% <0.5s on HIYH (was 25%). Per-section oracle dropped from 50% to 36%. Verse 1 std dev jumped from 1.66s to 6.42s.
**Why:** Demucs vocals from all-stems mix are mostly backing vocals. Verses have almost no separated vocal content (lead not present in stems). Whisper aligns to noise/artifacts. Overall worse than aligning on the full instrument mix.

### 23. Vocal-boosted mix (--vocal-boost 3)
**Status:** TESTED — marginal improvement only
**Date:** 2026-03-05
**Concept:** Boost vocal stems (Tenor, BGVS, Alto) 3x in alignment mix to increase vocal-to-instrument ratio without removing instruments.
**Result:** 19% <0.5s on HIYH (was 25%), per-section oracle 53% (was 50%). Close to baseline overall.
**Why marginal:** Vocal stems are backing vocals, not lead. Boosting backing vocals 3x increases their relative volume but doesn't fundamentally change what Whisper "hears" — the lead vocal (sung live) isn't in the stems at all.

### 24. stable-ts only_voice_freq parameter
**Status:** TESTED — slightly worse
**Date:** 2026-03-05
**Concept:** Filter audio to 200-5000Hz vocal band before alignment. Removes bass/drums interference.
**Result:** 22% <0.5s on HIYH (was 25%). Oracle unchanged at 50%.
**Why worse:** Removing bass/drums actually loses useful timing cues that Whisper uses for word placement.

### 25. stable-ts fast_mode parameter
**Status:** TESTED AND REMOVED — hurts accuracy
**Date:** 2026-03-05
**Concept:** Optimized timestamp mode when text is known (forced alignment). Should be faster and more accurate.
**Result:** 17% <0.5s on HIYH (combined with only_voice_freq). Oracle dropped to 42%.
**Why bad:** Causes "Failed to align the last N words" errors. Too aggressive for singing — re-alignment with max word duration fails when vocals are masked by instruments.

### 26. stable-ts dynamic_heads parameter
**Status:** TESTED — best single parameter improvement
**Date:** 2026-03-05
**Concept:** Find optimal cross-attention heads at runtime instead of using pre-selected heads.
**Result:** 33% <0.5s on HIYH (was 25%). Oracle slightly lower at 47% (was 50%).
**Why it helps:** Runtime head selection adapts to the specific audio characteristics. Best per-slide improvement (+8%), though per-section oracle dropped slightly.

### 27. Inter-section continuity chaining
**Status:** TESTED AND REMOVED — compounds errors
**Date:** 2026-03-05
**Concept:** Track drift between consecutive sections. If section N ends at time T but section N+1 starts too late, shift N+1's alignment backward. Should fix systematic drift.
**Result:** 33% <0.5s (same) but <1.0s dropped from 56% to 39%. CATASTROPHIC regression at wider tolerances.
**Why it fails:** Gap error calculation doesn't account for systematic per-section offsets. Bridge sections got shifted +4s too far as errors compounded through the chain.

### 28. Repeated-section equalization
**Status:** IN CODE — conservative, no measured change
**Date:** 2026-03-05
**Concept:** When same section appears multiple times, transfer timing pattern from best instance (lowest std dev) to worst instances. Conservative: only applies when std dev ratio > 3x AND absolute std dev >= 1.5s.
**Result:** No change on HIYH (no repeated sections with matching names).
**Why no change:** HIYH doesn't have exact repeated section names. Would need batch testing on songs with repeated Chorus/Verse labels.

### 29. word_dur_factor=4.0 (singing hold duration)
**Status:** TESTED — trade-off, not default
**Date:** 2026-03-05
**Concept:** Raise word_dur_factor from default 2.0 to 4.0, allowing held notes up to 4x the median word duration.
**Result:** 31% <0.5s (vs 33% dynamic_heads alone), but 61% <1.0s (vs 53%). Best avg error 1.4s.
**Why trade-off:** Longer word durations help held notes but slightly overshoot precise timing. Improves consistency at wider tolerances.

### 30. Two-phase alignment
**Status:** TESTED — worse at tight tolerance
**Date:** 2026-03-05
**Concept:** Phase 1 gets rough alignment, refine windows from results, Phase 2 re-aligns with corrected windows.
**Result:** 17% <0.5s (WORSE, was 33%), but 67% <1.0s (BEST). Phase 2 shifted windows by avg 13.5s.
**Why worse at <0.5s:** Window refinement changes section boundaries, causing re-alignment to place words in slightly different positions. Helps coarse accuracy but introduces new jitter at the tightest tolerance.

### 31. Slide-level refinement
**Status:** TESTED AND REMOVED — worse accuracy
**Date:** 2026-03-05
**Concept:** After section-level alignment, re-align each slide's text individually in a narrow ±5s window around its rough position. Gives Whisper a much smaller audio context (10s vs 30-40s), targeting within-section timing jitter.
**Result:** 28% <0.5s (was 33%), oracle dropped from 47% to 36%. 35/36 slides "refined" but most got WORSE.
**Why it fails:** Individual slide alignment in narrow windows loses three critical things: (1) section context — neighboring words constrain each other's positions, (2) ordering constraints — section-level alignment enforces word order, (3) audio context — 10s of polyphonic music is too noisy for reliable 5-word alignment.

### 32. initial_prompt parameter
**Status:** TESTED — NOT COMPATIBLE with align()
**Date:** 2026-03-05
**Concept:** Prime Whisper with context about hearing singing: "Worship song lyrics being sung with band instruments."
**Result:** All sections failed — `initial_prompt` is a transcribe() parameter only, not supported by align().
**Why:** stable-ts's align() method does not accept initial_prompt. This is documented but not obvious. The parameter only affects Whisper's decoder during free-form transcription, not forced alignment.

### 33. dynamic_heads batch test
**Status:** TESTED — WORSE on batch
**Date:** 2026-03-05
**Concept:** dynamic_heads=True across 10-song batch (was best single-song improvement on HIYH).
**Result:** Batch 23% <0.5s (was 30% baseline). Helps HIYH (+8%) but hurts other songs.
**Why worse on batch:** dynamic_heads optimizes cross-attention heads at runtime — this finds different heads for different audio characteristics. What works for HIYH's audio doesn't work for other songs. Per-song optimization, not a universal improvement.

### C. SOFA (Singing-Oriented Forced Aligner)
**Status:** NOT VIABLE — Mandarin only
**Tool:** GitHub clone, requires conda environment
**Concept:** Neural network trained specifically on singing data. Phoneme-level alignment.
**Risk:** Primarily Chinese-focused. No production-ready English model. Dictionary is `opencpop-extension.txt` (Mandarin). Not viable for English worship songs.

### D. lyrics-sync (mikezzb)
**Status:** NOT VIABLE — CUDA only, no CLI
**Tool:** GitHub clone, conda + CUDA 11.7
**Concept:** Demucs vocal separation + fine-tuned wav2vec2 + CTC alignment. Does accept known lyrics.
**Risk:** CUDA 11.7 locked — will NOT work on Apple Silicon (no NVIDIA GPU). No CLI (notebook only). No benchmarks published. Research prototype from 2023, abandoned.

### E. lyrics-aligner (schufo)
**Status:** NOT VIABLE — Python 3.6, PyTorch 1.5
**Tool:** GitHub clone, conda + Python 3.6 + CUDA 9.2
**Concept:** DNN trained on MUSDB18 (English polyphonic music). Joint alignment + singing voice separation.
**Why not viable:** Requires Python 3.6.10, PyTorch 1.5.0, CUDA 9.2. All completely incompatible with modern macOS/Apple Silicon. Would require rewriting the entire dependency chain.

### F. AudioShake LyricSync (Commercial)
**Status:** NOT VIABLE — paid API only
**Concept:** Commercial API used by Disney Music Group. Claims >95% word-level accuracy.
**Why not viable:** No local installation. Paid API. Not suitable for offline worship production tool.

### G. WhisperX (Whisper + wav2vec2)
**Tool:** `pip install whisperx`
**Concept:** Whisper transcribes, wav2vec2 refines word timestamps.
**Limitation:** Can't provide known lyrics — must use Whisper's transcription. For singing, Whisper mis-transcribes.

### 34. Guide.wav direct alignment
**Status:** TESTED — much worse
**Date:** 2026-03-05
**Concept:** Align lyrics directly against Guide.wav (which contains guide vocal = lead vocal) instead of all-stems mix.
**Result:** Oracle 28% (was 47%). Per-section StdDevs 4-7s. Section offsets range -16.8 to +9.3s.
**Why worse:** Guide.wav's click track (metronome) confuses Whisper more than full band instrumentation in the stems mix. Regular click pulses are interpreted as speech-like patterns.

### 35. Guide.wav spoken cues for section windows
**Status:** TESTED — same as baseline
**Date:** 2026-03-05
**Concept:** Transcribe Guide.wav to detect spoken section cues ("Verse", "Chorus", etc.) and use their timestamps for precise section window boundaries.
**Result:** 25% <0.5s (same as baseline). Oracle 50% (same). 17 cues found, 8/15 sections matched.
**Problem:** Cue matching errors — sequential matching can assign wrong cues when section names are ambiguous. Some cues are only 1.7s apart (Verse 2 and Chorus 1) due to rapid announcements on Guide track.

### 36. Transcription + matching mode
**Status:** TESTED — catastrophic
**Date:** 2026-03-05
**Concept:** Use Whisper transcription (not forced alignment) to find words, then fuzzy-match transcribed words back to known lyrics.
**Result:** Only 8/36 slides matched. Global offset +157s. Catastrophic failure.
**Why:** Whisper cannot transcribe sung words in polyphonic music. It mostly hears instruments. Forced alignment is far better because it KNOWS what words to look for.

### 37. Qwen3-ForcedAligner-0.6B (forced alignment with known text)
**Status:** TESTED — much worse than stable-ts
**Date:** 2026-03-05
**Concept:** Qwen3-ForcedAligner (Jan 2026, Alibaba) — purpose-built forced aligner, 0.6B params, supports MPS/Apple Silicon, claims superior timestamp accuracy on speech. Section-by-section alignment with known lyrics.
**Result (HIYH):** 0% <0.5s raw, 39% per-section oracle (vs stable-ts 25% raw, 50% oracle). 13s total alignment time (very fast).
**Why it fails:** Designed for speech, not singing. Word timestamps are 4-9s off even within correctly windowed sections. Chorus sections get first two slides collapsed to window start. The model can't locate words in sung polyphonic audio.

### 38. Qwen3-ASR transcription + forced aligner timestamps
**Status:** TESTED — worse than stable-ts
**Date:** 2026-03-05
**Concept:** Use Qwen3-ASR transcription model (trained on singing, 14.6% WER on English songs) to transcribe each section, then match transcribed words to known lyrics using SequenceMatcher.
**Result (HIYH):** 0% <0.5s, 17% <5s. Word matching worked (42/53 words for Verse 1) but timestamps are consistently 5-10s late.
**Why it fails:** The transcription model finds SOME words but timestamps are inaccurate on polyphonic worship music. The forced aligner component (which generates word timestamps) is still speech-trained.

### 39. Beat-informed snapping (librosa beat tracking)
**Status:** TESTED — no improvement
**Date:** 2026-03-05
**Concept:** Ground truth MIDI triggers fire on specific beats. At 181.5 BPM, beats are 0.33s apart. After stable-ts alignment, snap each slide to the nearest beat/downbeat.
**Result (HIYH):** 0% <0.5s (same as raw). Beat tracking detected 117.5 BPM (wrong — actual is 181.5). But even with correct beats, alignment errors are 4-9s, far larger than beat interval (0.33s).
**Why it fails:** Beat snapping can only correct sub-beat errors. Our alignment errors are multi-second, making beat snapping useless. Also, beat tracking on BGVS audio (no percussion) gives wrong tempo.

### 40. Guide.wav Demucs vocal extraction + stable-ts
**Status:** TESTED — much worse
**Date:** 2026-03-05
**Concept:** Guide.wav has the LEAD VOCAL (the actual singer) mixed with click track. No other stem has the lead vocal. Use Demucs to extract vocals from Guide.wav, giving us an isolated lead vocal for alignment.
**Result (HIYH):** 6% <0.5s, 21% per-section oracle (vs 26% baseline, 50% baseline oracle). Most timestamps 10-30s wrong.
**Why it fails:** Demucs vocal separation of Guide.wav is noisy — click track remnants and spoken cues contaminate the extracted vocal. The sparse lead vocal (only present during singing) gives stable-ts less to work with than the full mix with background harmonies.

### 41. Music structure analysis (librosa SSM + novelty functions)
**Status:** TESTED — much worse
**Date:** 2026-03-05
**Concept:** Use audio features (spectral flux, chroma cosine distance, RMS energy derivative, MFCC distance, self-similarity matrix novelty) to detect section boundaries, then build section windows from detected boundaries instead of proportional estimation.
**Result (HIYH):** 5% <0.5s, 12% per-section oracle (vs baseline 25% / 50%). Structure boundary errors 11-35s per section. SSM novelty had max=0.000 (didn't work on single vocal stem).
**Why it fails:** Single vocal stem (BGVS) doesn't have clear structural boundaries. Musical structure analysis works best on full mixes with drums/bass providing structural cues. Individual novelty signals averaged 21-42s error.

### 42. Energy-based phrase segmentation
**Status:** TESTED — interesting finding, but not viable
**Date:** 2026-03-05
**Concept:** Detect vocal phrases using RMS energy thresholds and silence detection. Map detected phrases 1:1 to arrangement slides. Parameters: threshold=-21dB, min_gap=0.3s.
**Result (HIYH):** 3% <0.5s raw, 33% per-section oracle. BUT Verse 1 alone got 86% oracle with +1.0s offset — phrases within the first section map nearly perfectly.
**Why it fails:** After section boundaries (especially blank slides = instrumental breaks), BGVS phrase structure diverges from arrangement. Background vocals don't phrase exactly like the lead vocal arrangement. Mapping breaks down after Chorus 1.

### 43. Hybrid energy + stable-ts
**Status:** TESTED — all variants worse than stable-ts alone
**Date:** 2026-03-05
**Concept:** Detect energy-based phrases within stable-ts estimated section windows. Also tested with ground truth windows (oracle), and median ensemble of energy + stable-ts.
**Results (HIYH):**
- Energy (Estimated Windows): 0% <0.5s, 25% oracle
- Energy (GT Windows): 0% <0.5s, 35% oracle
- Stable-ts baseline: 8% <0.5s, 42% oracle
- Median (Energy + Stable-ts): 5% <0.5s, 24% oracle
**Why all worse:** Energy-based phrase detection is too coarse for slide-level timing. stable-ts word-level alignment, even imperfect, provides better timing than energy dip detection.

### 44. Multi-model ensemble (small + large Whisper)
**Status:** TESTED — no improvement
**Date:** 2026-03-05
**Concept:** Run stable-ts with both small and large Whisper models on same audio/windows. Ensemble strategies: (a) pick best model per section by quality metrics, (b) take median timestamp across models.
**Results (HIYH):**
- Small model: 8% <0.5s, 42% oracle (15.8s)
- Large model: 8% <0.5s, 36% oracle (69.1s)
- Best-per-section: 8% <0.5s, 45% oracle (chose small for 14/15 sections)
- Median (small+large): 8% <0.5s, 42% oracle
**Why it fails:** Small model dominated (27/37 slides closer to GT). Large model was worse on HIYH across nearly all slides. Ensemble gives marginal oracle improvement (+3%) but no actual accuracy gain. 4.5x slower with large model for no benefit.

### H. Lead vocal stem availability
**Status:** INVESTIGATED — NOT AVAILABLE
**Date:** 2026-03-05
**Finding:** Searched all 227 projects with MultiTracks on Creative Arts volume. ZERO have isolated lead vocal stems. MultiTrack providers (MultiTracks.com etc.) deliberately exclude lead vocals. Available vocal stems are BGVS, Alto, Tenor, Soprano, Choir — all background/harmony parts. 222/227 projects have Guide.wav (lead vocal + click).

### F. Per-section offset prediction
**Concept:** Use metadata (section type, position in song, tempo) to predict the systematic offset for each section type.
**Finding:** Per-section offset correction gives 50% <0.5s (from comparison tool). If we could predict offsets, accuracy doubles.
**Analysis (2026-03-05):** Per-section offsets on HIYH range from +3.8s (Verse 1) to +8.0s (last Chorus). Bridge sections are most consistent (±0.4s). Chorus sections are least consistent (2.2s range). Variation is partly due to ground truth's variable early-fire timing and partly due to alignment error. With only 15 songs, not enough data to build a reliable prediction model.

### G. Vocal stem mixing strategies
**Concept:** Instead of Guide.wav, mix specific vocal stems (BGVS, Alto, Tenor, Lead if available) for alignment.
**Finding:** 122 of 153 songs have vocal stems. Lead vocal not always available.

### 45. Click track removal from Guide.wav
**Status:** TESTED — catastrophic failure
**Date:** 2026-03-05
**Concept:** Guide.wav = lead vocal + click track + cues. Click Track.wav is a separate stem. Subtract click from Guide to get isolated lead vocal.
**Results (HIYH):**
- Click-removed Guide: 0% <0.5s, 3% oracle (21.1s avg error). Catastrophic.
- Click-removed Guide + BG vocals mix: 11% <0.5s, 45% oracle
- BG vocal mix (BGVS+Alto+Tenor): 8% <0.5s, 48% oracle
**Why it fails:** Phase alignment between Click Track.wav and the click embedded in Guide.wav is imperfect — subtraction creates artifacts that are worse than the original click. The cleaned signal has distortion and phase cancellation effects.

### 46. Full instrument mix as alignment audio
**Status:** TESTED — worse than BGVS alone
**Date:** 2026-03-05
**Concept:** Use ALL stems mixed (drums, bass, keys, guitars + vocals) instead of vocal-only. Hypothesis: full mix has rhythmic timing cues that could help anchor word placement.
**Results (HIYH):**
- BGVS only (baseline): 8% <0.5s, 42% oracle
- Full mix (all stems): 3% <0.5s, 42% oracle
- Instruments only: 3% <0.5s, 24% oracle
- Vocal-heavy mix (vocals 3x + inst 1x): 8% <0.5s, 42% oracle
- Drums + Vocals: 11% <0.5s, 45% oracle (marginal best)
- Median (5 sources): 8% <0.5s, 42% oracle
**Why it doesn't help:** More instruments = more interference for Whisper. Drums+vocals is marginally better (+3%) but not meaningful. Instruments alone give terrible results (Whisper hallucinates words in instrument sounds).

### 47. lyrics-aligner (DTW-attention, MUSDB18-trained)
**Status:** TESTED — worse than stable-ts
**Date:** 2026-03-05
**Tool:** schufo/lyrics-aligner (2021), InformedOpenUnmix3 model, DTW-attention alignment trained on polyphonic music
**Concept:** Unlike Whisper (speech-trained), lyrics-aligner was specifically trained on polyphonic music (MUSDB18 dataset) to jointly separate vocals and align lyrics. Takes known lyrics as phoneme sequence.
**Results (HIYH):**
- BGVS, whole song, proper DTW: 8% <0.5s, 42% oracle (17.9s). First 11 slides decent, then diverges.
- Full mix, whole song, proper DTW: 8% <0.5s, 42% oracle (17.3s). Similar pattern.
- BGVS, argmax (incorrect extraction): 5% <0.5s, 24% oracle
- BGVS, section-by-section, argmax: 3% <0.5s, 16% oracle
**Why it fails:** The model's DTW path starts diverging after the first chorus (~slide 12). Whole-song DTW is fundamentally brittle for 4+ minute songs with repeating sections. The model runs through phonemes faster than the actual singing and never recovers. Trained on MUSDB18 (150 pop songs) — may not generalize to worship music with long bridge repetitions.

### I. CrisperWhisper (improved Whisper timestamps)
**Status:** NOT VIABLE — requires custom transformers fork
**Date:** 2026-03-05
**Concept:** Whisper variant from nyrahealth with improved word-level timestamps via adjusted tokenizer + custom attention loss during training.
**Why not viable:** Requires custom HuggingFace transformers fork (`nyrahealth/transformers@crisper_whisper`), not compatible with stable-ts's align() which uses OpenAI Whisper API. Would require rewriting the alignment pipeline.

### J. AutoLyrixAlign (MIREX 2019 winner)
**Status:** NOT VIABLE — code on Google Drive, old dependencies
**Concept:** HMM-based acoustic models trained specifically for polyphonic music alignment. Won MIREX 2019 with <200ms mean word alignment error.
**Why not viable:** Code available only via Google Drive download (not git), undocumented dependencies, likely requires old Python/package versions. No maintenance since 2020.

### K. STARS (ACL 2025 — singing alignment framework)
**Status:** NOT VIABLE — Chinese-only, GPU required
**Tool:** gwx314/STARS, unified singing transcription + alignment
**Why not viable:** Chinese and Chinese+English bilingual only. No English-only model. Requires CUDA GPU.

### 48. torchaudio MMS_FA forced alignment
**Status:** TESTED — much worse than stable-ts
**Date:** 2026-03-05
**Tool:** torchaudio.functional.forced_align with MMS_FA pipeline (Meta's dedicated forced alignment model)
**Concept:** Different from ctc-forced-aligner (#19, ONNX). Uses native PyTorch forced_align with Meta's MMS model specifically trained for alignment. Section-by-section and whole-song modes.
**Results (HIYH):**
- Section-by-section: 0% <0.5s, 22% oracle, avg 9.95s (28.5s runtime)
- Whole song: 0% <0.5s, 14% oracle, avg 20.61s (42.7s runtime)
**Why it fails:** CTC-based model trained on speech cannot locate words in polyphonic singing. Places words systematically early. Whole-song mode diverges worse.

### 49. WhisperX (wav2vec2 alignment with known lyrics)
**Status:** TESTED — worse than stable-ts
**Date:** 2026-03-05
**Tool:** whisperx.align() with custom transcript (not transcription mode)
**Concept:** Previous note said "can't provide known lyrics" — WRONG. WhisperX accepts transcript segments with text+start+end. Uses wav2vec2 (not Whisper) for actual word-level CTC alignment. Fundamentally different from stable-ts cross-attention.
**Results (HIYH):**
- Section-by-section: 0% <0.5s, 31% oracle (14.0s)
- Whole song: 0% <0.5s, 3% oracle (18.3s)
**Why it fails:** wav2vec2 (LibriSpeech-trained) worse than Whisper cross-attention for singing. Verse 1 had consistent ~1.2s delta but chorus/bridge sections badly wrong. Section-by-section places first slide at window start.

### L. Montreal Forced Aligner (MFA)
**Status:** NOT TESTED — conda required, speech-trained
**Tool:** Kaldi GMM-HMM based, conda install only
**Concept:** Completely different architecture from neural approaches. Research shows it "struggles with singing, especially melisma" (Liu 2024). Would need singing-specific training data.
**Why skipped:** Conda not available on test machine, and research literature confirms poor performance on singing without custom training.

### M. NeMo Forced Aligner (NVIDIA)
**Status:** NOT VIABLE — limited macOS support
**Tool:** FastConformer CTC, pip install 'nemo_toolkit[asr]'
**Concept:** NVIDIA's aligner, claims best-in-class on speech benchmarks.
**Why not viable:** Limited macOS/Apple Silicon support. Designed for NVIDIA GPUs on Linux. No singing models.

### N. AutoLyrixAlign (NUS MIREX winner)
**Status:** NOT TESTABLE — web demo dead, Kaldi dependency
**Tool:** Kaldi GMM-HMM based, won MIREX 2019+2020 for lyrics alignment
**Concept:** Purpose-built for polyphonic music lyrics alignment. Uses singing-adapted acoustic models with duration-explicit HMM. Claims sub-200ms mean error.
**Why not tested:** Web demo at autolyrixalign.hltnus.org is dead (redirects to spam). GitHub repo from 2019, unmaintained. Requires Kaldi (massive C++ dependency, painful on macOS). No pip install.

### 52. LyricsAlignment-MTL (DALI-trained, pitch+CTC)
**Status:** TESTED — much worse than stable-ts
**Date:** 2026-03-05
**Tool:** ICASSP 2022 multi-task learning model (CTC alignment + boundary detection), trained on DALI singing dataset
**Concept:** First singing-trained model we could actually run. Uses joint pitch detection + phoneme alignment. Trained on 5000+ songs from DALI dataset. PyTorch-based, runs on CPU.
**Results (HIYH):**
- MTL+BDR section-by-section: 3% <0.5s (1/37), 21% oracle, avg 12.36s
- MTL+BDR whole song: 0% <0.5s, 30% oracle, avg 10.67s
- Baseline (no BDR) section-by-section: 0% <0.5s, 9% oracle, avg 16.28s
**Why it fails:** Despite being trained on singing, the model was trained on clean vocal stems (separated from MUSDB18). Our BGVS audio is raw polyphonic backing vocals — multiple singers, harmonies, no separation. The model's phoneme recognition is overwhelmed by the multi-voice signal. BDR (boundary detection) helps slightly but can't overcome the audio quality gap.

### 53. CREPE pitch-onset correction (hybrid with stable-ts)
**Status:** TESTED — completely non-functional
**Date:** 2026-03-05
**Tool:** torchcrepe (CREPE pitch tracker) + stable-ts
**Concept:** Singing has pitch changes at word boundaries. Detect note onsets via CREPE pitch tracking, snap stable-ts word timestamps to nearest pitch onset. Novel hybrid approach combining speech alignment + musical signal.
**Results (HIYH):**
- CREPE detected 0% voiced frames in 8/9 sections, 3% in one section
- Only 4 pitch onsets found in entire song (vs 37 expected)
- Snapping had zero effect (identical to baseline in all metrics)
- GT analysis: median 60.8s from nearest pitch onset (CREPE can't find vocals at all)
**Why it fails:** CREPE is a monophonic pitch tracker — designed for single-voice audio. BGVS has multiple singers at different pitches + instrumental harmonies. Periodicity detection (voicing confidence) returns near-zero everywhere because there is no dominant single pitch. Pitch-based approaches require isolated vocal stems.

### 50. Learned per-section-type offset correction
**Status:** TESTED — not viable (too much variance)
**Date:** 2026-03-05
**Tool:** Custom script, leave-one-song-out cross-validation on GT corpus
**Concept:** Accept imperfect alignment and learn to correct it. Analyze per-section-type offsets (Verse, Chorus, Bridge) across the corpus, learn median offsets, apply to held-out songs.
**Results (2 songs — God So Loved, Here In Your House, 62 measurements):**
- All section types had HIGH variance (>10s std dev) — not useful for correction
- Leave-one-out HURT accuracy: 6% → 3% <0.5s
- Per-song oracle: 13% <0.5s
- Per-section-type oracle: 18% <0.5s
**Why it fails:** The alignment errors are not consistent per-section-type across songs. Chorus offset varies from -37s to +1s (std 10.7s). The errors are song-specific and section-instance-specific, not systematic by section type. Even within the same song, a Verse 1 and Verse 2 have different offsets. Learning from corpus is NOT a viable path.

### 51. BS-RoFormer vocal separation + stable-ts
**Status:** TESTED — much worse than BGVS baseline
**Date:** 2026-03-05
**Tool:** audio-separator with model_bs_roformer_ep_317_sdr_12.9755.ckpt (2024 SOTA, SDR 12.97)
**Concept:** Guide.wav is the ONLY audio with lead vocal. BS-RoFormer is SOTA for vocal separation (much better than Demucs). Extract lead vocal, then align. Previous Demucs attempt (#40) got 6%/21% oracle.
**Results (HIYH):**
- BS-RoFormer Guide + small: 0% <0.5s, 12% oracle, avg 18.27s (5min separation + 22s alignment)
- BS-RoFormer Guide + large: 0% <0.5s, 6% oracle, avg 24.11s (5min separation + 277s alignment)
- BGVS baseline: 8% <0.5s, 42% oracle (for comparison)
**Why it fails:** Even SOTA vocal separation can't cleanly extract lead vocal from Guide.wav (which has click track + spoken cues mixed in). Separated vocals still have artifacts. Worse than Demucs (#40) likely because BS-RoFormer optimizes for music signals, while Guide.wav has non-musical elements (click, spoken cues).

### 55. tikick/LyricsAlignment — Contrastive Learning
**Status:** TESTED — worse than stable-ts baseline
**Date:** 2026-03-06
**Tool:** tikick/LyricsAlignment (NeurIPS 2024 workshop), dual audio+text encoder with contrastive loss
**Concept:** Completely different paradigm — encode audio as log-STFT spectrogram (11025 Hz) and lyrics as IPA phonemes, compute similarity matrix via dual encoders, then monotonic DP alignment. Trained on DALI v2.0 (5000+ pop songs). Reports AAE 0.20s on JamendoLyrics++. Works on polyphonic audio.
**Results (HIYH, section-by-section):**
- negBox_daliClean: 5% <0.5s (2/37), 11% <1s, 27% <2s, 48% oracle
- contrastive_daliClean: 0% <0.5s, 16% <1s, 27% <2s, 48% oracle
- box_daliClean: 0% <0.5s, 11% <1s, 27% <2s, 52% oracle
- Bridge sections failed entirely (phoneme alignment index error) — 12/37 slides got no timestamp
- Time: ~35s per checkpoint
**Why it fails:** Trained on commercial pop with isolated vocals in DALI. Doesn't generalize to worship music with polyphonic BG vocals and no lead vocal. Also, Bridge sections with doubled lyrics ("Surely the Lord is Surely the Lord is") cause phoneme tokenization mismatches.

### N2. AutoLyrixAlign — Kaldi HMM/DNN (MIREX 2019 winner)
**Status:** NOT TESTABLE — requires Singularity container (Linux only)
**Date:** 2026-03-06
**Concept:** Kaldi-based HMM/DNN singing acoustic models. MIREX 2019+2020 winner with <200ms AAE.
**Why not tested:** 3.9GB download containing Kaldi models inside a Singularity container image (`kaldi.simg`). Requires `singularity shell` to run. Singularity does not run natively on macOS. Would need a Linux VM or server.

### N3. ASA_ICASSP2021 — Kaldi + Recursive Anchoring
**Status:** NOT TESTABLE — requires Kaldi + Docker + 35GB disk
**Date:** 2026-03-06
**Concept:** Kaldi-based framework with recursive anchoring for long recordings. Includes built-in Demucs separation.
**Why not tested:** Requires Kaldi compilation (hours), Docker, conda environment, and 35GB disk space. Too heavy for our macOS test setup.

### N4. ALT_SpeechBrain — wav2vec2 fine-tuned on singing
**Status:** NOT APPLICABLE — transcription system, not alignment
**Date:** 2026-03-06
**Why discarded:** Outputs text (no timestamps). It's an automatic lyric transcription (ALT) system. We already have the lyrics; we need timing, not transcription.

### N5. E2E-LyricsAlignment — Wave-U-Net polyphonic
**Status:** NOT TESTABLE — no pretrained checkpoints, requires PyTorch 1.4
**Date:** 2026-03-06
**Why not tested:** No pretrained models provided. Requires DALI dataset + GPU training from scratch. Pins PyTorch 1.4 which is incompatible with Apple Silicon.

---

## Key Findings

1. **Window estimation is NOT the bottleneck** — ground-truth windows gave same accuracy as estimated
2. **Per-section offset is the biggest error source** — correction gives 50% <0.5s vs 30% without
3. **Audio quality is the fundamental limit** — BG vocal stems lack lead vocal for verse sections
4. **All post-processing (beat-snap, xcorr, template, refine, onset anchoring) failed to move the batch needle**
5. **Model choice matters but no model is universally better** — small best on average
6. **Demucs helps some songs dramatically but hurts others** — depends on audio quality
7. **Audio-based onset detection CANNOT reliably find vocal onsets in polyphonic music** — instruments mask vocal signal. Tested bandpass filtering, step-up detection, and Demucs separation. None work.
8. **Demucs vocal separation from all-stems mix gives backing vocals, not lead** — lead vocal is recorded live during service and NOT in pre-recorded stems. Alignment on Demucs vocals is WORSE than full mix.
9. **stable-ts parameter tuning has limited upside** — dynamic_heads is the only parameter that helped (+8%). only_voice_freq, fast_mode, and token_step all failed to improve or actively hurt. The parameter space is largely exhausted.
10. **Inter-section chaining compounds errors** — propagating corrections between sections makes things worse, not better. Each section's offset is somewhat independent.
11. **No parameter improves the batch** — every parameter tested (dynamic_heads, only_voice_freq, fast_mode, word_dur_factor, two-phase, slide-refine) either doesn't help or makes batch accuracy worse. Some help individual songs but hurt others.
12. **No viable singing-trained aligner exists for English on macOS** — lyrics-sync (CUDA-only), SOFA (Mandarin), lyrics-aligner (untested, possibly old). AudioShake is commercial API only. The open-source landscape lacks a production-ready English singing aligner.
13. **Ground truth has variable early-fire timing** — MIDI triggers fire 3-8s before vocal onset depending on section type. No single offset can account for this variation. This inherently limits automated accuracy against hand-placed ground truth.
14. **Transcription mode is catastrophically worse than forced alignment** — Whisper cannot transcribe sung words in polyphonic music. Only 8/36 slides matched vs 36/36 with forced alignment.
15. **Guide.wav click track is more disruptive than band instruments** — direct alignment on Guide.wav gives 28% oracle vs 47% on stems mix. The regular click pattern confuses Whisper more than irregular instrument patterns.
16. **30% <0.5s is the practical ceiling** for fully automatic alignment on polyphonic worship music using open-source speech-trained models. No parameter, post-processing, or windowing change improves this across a batch of songs.
17. **No viable singing-trained aligner exists for English on macOS** — all candidates are either CUDA-only (lyrics-sync), Mandarin-only (SOFA), too old (lyrics-aligner Python 3.6), or commercial API (AudioShake). The open-source landscape lacks a production-ready English singing aligner as of March 2026.
18. **Music structure analysis fails on isolated vocal stems** — SSM, spectral flux, chroma, MFCC, and energy novelty all fail to detect section boundaries without drums/bass structural cues. 21-42s average boundary error.
19. **Energy phrase detection works within sections but not across** — phrase segmentation matches slides well within Verse 1 (86% oracle) but mapping breaks at section boundaries, especially at blank slides (instrumental breaks).
20. **Multi-model ensemble gives no meaningful improvement** — small model dominates (27/37 slides closer). Best-per-section oracle improves +3% but no actual accuracy gain. Not worth the 4.5x time increase.
21. **Click track subtraction creates worse artifacts than the original click** — phase alignment is imperfect; resulting signal has distortion and cancellation artifacts.
22. **Full instrument mix is WORSE than vocal-only for alignment** — more instruments = more interference for Whisper. Drums+vocals is marginally better than BGVS alone (+3%) but not significant.
23. **lyrics-aligner (polyphonic-trained) is no better than speech-trained Whisper** — despite being trained on polyphonic music (MUSDB18), the DTW-attention model diverges on long songs with repeating sections. 42% oracle (same as stable-ts baseline).
24. **No singing-trained aligner outperforms stable-ts on our audio** — tested Qwen3, lyrics-aligner, ctc-forced-aligner, torchaudio MMS_FA, WhisperX/wav2vec2. All performed worse. Stable-ts + Whisper small remains the best available tool.
25. **51 approaches tested, 30% batch ceiling confirmed** — the limiting factors are fundamental: (a) no isolated lead vocal stems, (b) speech-trained models on polyphonic audio, (c) variable ground truth early-fire timing. No algorithmic improvement can overcome these constraints.
26. **CTC/wav2vec2 alignment is strictly worse than Whisper cross-attention for singing** — torchaudio MMS_FA (0%, 22% oracle) and WhisperX wav2vec2 (0%, 31% oracle) both worse than stable-ts (8%, 42% oracle). Cross-attention captures word timing better than CTC emission-based alignment on polyphonic audio.
27. **SOTA vocal separation (BS-RoFormer SDR 12.97) cannot salvage Guide.wav** — even better than Demucs, but Guide.wav's click track + spoken cues create artifacts that defeat any vocal separation model. BS-RoFormer result (0%, 12% oracle) is worse than using BGVS directly (8%, 42% oracle).
28. **Every alignment paradigm has been exhausted** — cross-attention (Whisper/stable-ts), CTC (wav2vec2, MMS), DTW-attention (lyrics-aligner), HMM-GMM (MFA not tested but literature shows poor on singing), sequence-to-sequence (Qwen3). All hit the same fundamental constraint: no clean lead vocal signal.
29. **Learned offset correction is not viable** — alignment errors vary too much per-song and per-section-instance (std dev >10s within section types). There's no learnable pattern across the corpus. Leave-one-out correction made things WORSE (6%→3%). The errors are fundamentally unpredictable from section type alone.
30. **Singing-trained models (LyricsAlignment-MTL) still fail on raw polyphonic audio** — despite being trained on 5000+ songs (DALI dataset), the CTC+BDR model gets 3% <0.5s vs stable-ts 8%. Training on separated vocals doesn't transfer to unseparated polyphonic backing vocals.
31. **Pitch tracking (CREPE) cannot detect voice in polyphonic audio** — CREPE detects 0% voicing in 8/9 sections. Monophonic pitch trackers require a dominant single voice; multiple singers + instruments produce no trackable pitch contour.
32. **54 approaches, 30% batch ceiling confirmed** — every open-source paradigm exhausted: cross-attention (Whisper), CTC (wav2vec2, MMS), DTW-attention, HMM-GMM, sequence-to-sequence (Qwen3), singing-trained CTC+BDR, pitch tracking (CREPE), vocal separation (Demucs, BS-RoFormer), offset learning. AudioShake (commercial API) is the only untested option specifically designed for this task.
33. **Contrastive learning alignment (tikick/LyricsAlignment) also fails on polyphonic BG vocals** — NeurIPS 2024 approach using dual audio+text encoders with similarity matrix + DP alignment. Reports AAE 0.20s on Jamendo benchmark. On HIYH: negBox_daliClean gets 5% <0.5s (worse than stable-ts 8%), 48% oracle. Bridge sections fail entirely (index errors from phoneme alignment). Trained on DALI pop songs, doesn't generalize to worship BG vocals.
34. **AutoLyrixAlign (MIREX winner) requires Singularity/Linux** — 3.9GB download contains Kaldi models + Singularity container image. Requires `singularity shell kaldi.simg` to run. Linux-only, cannot run on macOS. Not testable on our infrastructure.
35. **ASA_ICASSP2021 requires Kaldi + Docker + 35GB** — Kaldi-based framework with Demucs separation built in. Requires either Docker or full Kaldi compilation + conda environment. Too heavy for our test setup.
36. **ALT_SpeechBrain is transcription, not alignment** — outputs text (no timestamps). Useless for our timing use case.
37. **E2E-LyricsAlignment has no pretrained checkpoints** — Wave-U-Net architecture that works on polyphonic audio, but requires PyTorch 1.4 (incompatible with Apple Silicon) and DALI dataset training from scratch. Dead end.
38. **57 approaches tested, 30% batch ceiling reconfirmed** — now exhausted contrastive learning (tikick) in addition to all previous paradigms. Every testable open-source alignment tool has been tried. The remaining untested options (AutoLyrixAlign, ASA) require Linux container infrastructure.
39. **AutoLyrixAlign (MIREX 2019+2020 winner) tested on Ubuntu server — works on polyphonic audio but wrong reference frame** — Run on Ubuntu Server 10.10.11.157 via Singularity container (kaldi.simg, 3.93GB). Audio: BGVS.wav (polyphonic BG vocals). Result: 0% <0.5s raw, 36.1% with +5.00s constant offset. Root cause: ALA finds ACTUAL vocal onset; GT timestamps are when the MIDI operator fires the trigger (based on lead vocal/instrument cues, ~5-10s before BG vocals speak the word). Within-section consistency is EXCELLENT (bridge section: 12 slides all within 1.35s of each other), confirming ALA correctly locates word positions. The tool works — the problem is that backing vocals come in later than the musical cue used for trigger timing. Runtime: 100s for 256s audio (0.39x realtime).
40. **ALA section-by-section (Ubuntu #2) — identical to whole-song, section isolation provides zero benefit** — Cropped audio to 9 section windows (11.9–256s), ran ALA independently on each, adjusted timestamps back to global time. Audio: HIYH full mix. Result: 0% <0.5s raw, 36.1% with +5s offset — IDENTICAL to whole-song result. The hypothesis (repeated section confusion causing drift) was wrong. Each section's FIRST slide starts close (1.49s, 1.56s, 0.71s for Verse1/Chorus3/Outro — ALA snaps words to clip start), but within-section drift accumulates the same way regardless of isolation. Root cause confirmed: within-section Viterbi drift on polyphonic audio, not cross-section repeated-lyric confusion. Runtime: 424s for 9 sections (~47s each). Script: eval_ala_sections.py.
