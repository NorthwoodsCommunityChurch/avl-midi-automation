# PRD: Lyrics-to-Audio Alignment Testing on Ubuntu Server

**For:** Claude working on the LLM server / Ubuntu machine project
**From:** MIDI Automation project (macOS)
**Date:** 2026-03-06
**Priority:** High — this is the last untested path toward near-100% accuracy

---

## Background

The MIDI Automation app automatically places MIDI triggers in Ableton Live to advance ProPresenter slide shows during live worship. The core challenge: align lyrics (from ProPresenter) to audio (from Ableton multitrack stems) to find when each lyric slide should appear.

We have tested **57 approaches** on macOS using Python/PyTorch. The current best is `stable-ts` (Whisper forced alignment), achieving **30% of slides within 0.5s** of ground truth across a 13-song batch. Every open-source alignment paradigm available on macOS has been exhausted.

Three tools we believe are significantly more capable **require Linux** and have not been tested. This PRD defines the work to test them on the Ubuntu server.

---

## The Goal

Test three lyrics-to-audio alignment tools on Ubuntu, evaluate against ground truth timing data from hand-placed MIDI files, and report accuracy metrics. We need to know if any of these tools can beat the current 30% <0.5s ceiling.

**Target accuracy:** 70%+ of slides within 0.5s of ground truth (would be transformative). Even 50% would be a significant improvement worth shipping.

---

## The Test Song

**"Here In Your House"** — this is our primary benchmark song with full ground truth data.

- Audio: `BGVS.wav` — polyphonic 4-part backing vocals (no lead vocal, no instruments other than click embedded in a separate file)
- Duration: 255.9 seconds
- Slides: 37 total (15 sections: Verse 1, Verse 2, Chorus ×3, Bridge ×3, Tag, etc. with blank slides between)
- Ground truth: 37 word-level timestamps extracted from hand-placed MIDI triggers in Ableton `.als` files

The critical challenge: the audio is **polyphonic backing vocals** (4 singers simultaneously). There is no isolated lead vocal stem. Every approach that requires a single clean voice has failed.

---

## Infrastructure

### Ubuntu Server
- **OS:** Ubuntu Server
- **CPU:** 16-core Xeon
- **RAM:** 96GB
- **GPU:** AMD RX 580 (8GB VRAM) — NOTE: Kaldi/AutoLyrixAlign is CPU-only, GPU not used
- **Disk:** Need ~50GB free working space

### Data Source (macOS machines)
Files needed from the macOS side will be provided via SCP or shared network path. Coordinate with the MIDI Automation project owner (Aaron) for access. Key files to transfer to the Ubuntu server:

| File | Location on graphics-mac (10.10.11.77) | Purpose |
|------|----------------------------------------|---------|
| `BGVS.wav` | `/Volumes/Creative Arts/Music/Ableton/Songs/H/Here In Your House/Here In Your House F 181.5 Project/MultiTracks/BGVS.wav` | Test audio |
| `here_in_your_house_results.json` | `/Users/mediaadmin/test_data/here_in_your_house_results.json` | Ground truth timestamps |
| `here_in_your_house.json` | `/Users/mediaadmin/test_data/lyrics_cache/here_in_your_house.json` | Lyrics + arrangement from ProPresenter |
| `autolyrixalign.zip` | `/Users/mediaadmin/test_data/AutoLyrixAlign/autolyrixalign.zip` | AutoLyrixAlign Singularity image + models (3.93GB) |
| `ASA_ICASSP2021/` | `/Users/mediaadmin/test_data/ASA_ICASSP2021/` | ASA repo (already cloned) |
| `E2E-LyricsAlignment/` | Clone from GitHub | jhuang448/E2E-LyricsAlignment-Implementation |

---

## Tool 1: AutoLyrixAlign (TOP PRIORITY)

### What it is
MIREX 2019 and 2020 winner for automatic lyrics-to-audio alignment. Developed by National University of Singapore HLT Lab. Kaldi HMM/DNN with singing-adapted acoustic models. Claims **<200ms average absolute error** on polyphonic audio.

- GitHub: https://github.com/chitralekha18/AutoLyrixAlign
- Paper: ICASSP 2020 — "Automatic Lyrics Alignment and Transcription in Polyphonic Music"

### How to run
The download (`autolyrixalign.zip`) contains:
- `NUSAutoLyrixAlign/kaldi.simg` — Singularity container with all Kaldi dependencies
- `NUSAutoLyrixAlign/RunAlignment.sh` — main entry point
- Example audio/lyrics files

```bash
# Install Singularity (if not present)
# Ubuntu: https://sylabs.io/guides/3.0/user-guide/installation.html
sudo apt-get install -y singularity-container
# Or newer: https://github.com/sylabs/singularity/releases

# Unzip
unzip autolyrixalign.zip
cd NUSAutoLyrixAlign

# Run alignment
singularity shell kaldi.simg -c "./RunAlignment.sh BGVS.wav lyrics.txt output.txt"
```

### Input format
- Audio: WAV file (any sample rate — the script handles conversion internally)
- Lyrics: plain text file, one word per line OR words separated by spaces/newlines

### Output format
The output file contains word-level timestamps:
```
word_start_seconds  word_end_seconds  word
0.54  0.82  There's
0.82  1.10  an
...
```

### What you need to build
A Python evaluation script that:
1. Prepares lyrics input file from `here_in_your_house.json` (extract all slide texts, clean to words)
2. Runs `RunAlignment.sh` on `BGVS.wav`
3. Parses the output timestamps
4. Maps word-level timestamps back to **slide start times** (first word of each slide = slide start time)
5. Compares slide start times against ground truth from `here_in_your_house_results.json`
6. Reports accuracy at 0.5s, 1.0s, 2.0s, 5.0s thresholds

### Lyrics preparation
The `here_in_your_house.json` file structure:
```json
{
  "groups": [{"uuid": "...", "name": "Verse 1", "slides": [{"text": "There's an echoing..."}]}],
  "arrangements": [{"name": "MIDI", "group_uuids": ["uuid1", "uuid2", ...]}]
}
```

Use the "MIDI" arrangement to get the full ordered slide list (including repeated sections). Skip blank slides (empty text). Build a word list and track which slide each word belongs to.

### Ground truth format
`here_in_your_house_results.json`:
```json
{
  "results": [
    {"slide_text": "There's an echoing...", "gt_time": 11.74, "status": "MATCHED"},
    ...
  ]
}
```
Only use entries where `gt_time` is not null and `status != "MISSED"`.

### Important notes
- RAM requirement: **20GB** — the server's 96GB is more than sufficient
- The Singularity image already includes Kaldi, models, and all dependencies — no compilation needed
- The script takes ~2 minutes per 4-minute song (expected ~2-3 min for BGVS.wav)

---

## Tool 2: ASA_ICASSP2021

### What it is
"Audio-to-Song Alignment" — Kaldi-based framework with recursive anchoring for long recordings. ICASSP 2021. Includes built-in Demucs vocal separation.

- GitHub: https://github.com/emirdemirel/ASA_ICASSP2021
- Already cloned to graphics-mac at `/Users/mediaadmin/test_data/ASA_ICASSP2021/`

### Installation options

**Option A — Docker (easier):**
```bash
cd ASA_ICASSP2021
docker build --tag asa:latest -f Dockerfile .  # ~1 hour first time
DATASET='/path/to/test/data'  # must contain wav/ and lyrics/ subdirs
docker run -v $DATASET:/a2l/dataset -it asa:latest
# Inside container:
source /root/miniconda3/etc/profile.d/conda.sh
conda activate ASA
```

**Option B — Local Kaldi (harder but faster after setup):**
```bash
# 1. Install Kaldi
git clone https://github.com/kaldi-asr/kaldi
cd kaldi/tools && make -j16  # uses all 16 cores
cd ../src && ./configure && make -j16

# 2. Set up ASA
cd ASA_ICASSP2021/a2l
git clone https://github.com/facebookresearch/demucs
cp local/demucs/separate.py demucs/demucs/separate.py
conda env update -f environment.yml
# Set KALDI_ROOT in a2l/path.sh
```

### Input format
Requires a dataset directory with:
```
dataset/
  wav/song_name.wav
  lyrics/song_name.txt  (one line per sentence/phrase)
```

### Running
```bash
# Inside container or activated conda env:
cd a2l
bash run_lyrics_alignment_long.sh
```

### What you need to build
Same evaluation script structure as AutoLyrixAlign — map word timestamps to slide starts, compare against ground truth.

### Notes
- Requires ~35GB disk for Docker image + Kaldi models
- Built-in Demucs separation is a concern — it may hurt accuracy on polyphonic BG vocals (we've confirmed Demucs hurts 3/5 songs in our tests). Check if Demucs can be disabled or bypassed.
- The "recursive anchoring" is the key innovation — it segments long songs into shorter pieces before aligning, which could help with our repeated sections problem.

---

## Tool 3: E2E-LyricsAlignment

### What it is
Wave-U-Net architecture trained end-to-end on polyphonic audio for character-level CTC alignment. ICASSP 2020. Key claim: works directly on polyphonic mix without vocal separation.

- GitHub: https://github.com/jhuang448/E2E-LyricsAlignment-Implementation
- Same first author (jhuang448) as LyricsAlignment-MTL which we tested on macOS (got 3% <0.5s)

### CRITICAL BLOCKER: No pretrained checkpoints
The repository does **not include pretrained model weights**. The checkpoint path in the code points to the author's institutional server (`/import/c4dm-datasets/...`) which is not publicly accessible.

**Before spending time on this tool**, check:
1. Email the author (Jiawen Huang, Queen Mary University of London) to request the model weights
2. Check if any forks of the repo have added checkpoints
3. Check if the weights were published elsewhere (Zenodo, Hugging Face, etc.)

If weights are not available, **skip this tool** — training from scratch requires the DALI dataset (licensed, requires registration) and would take days even on this server.

### If weights are found
```bash
git clone https://github.com/jhuang448/E2E-LyricsAlignment-Implementation
cd E2E-LyricsAlignment-Implementation

# Requirements need porting — original pins torch==1.4.0 (2020, incompatible with modern Python)
# Install modern PyTorch instead:
pip install torch torchaudio librosa soundfile h5py tqdm sortedcontainers

# The model architecture (Wave-U-Net) is standard — modern PyTorch should work
# Main compatibility concern: torch.nn.DataParallel wrapping of old checkpoints
```

### Audio format
- Input: 22,050 Hz mono WAV
- Processes in 5-second chunks with 2.5s hop
- Output: word start/end times in seconds

---

## Evaluation Methodology

Use this exact methodology to compare all tools consistently:

### Accuracy metrics
For each slide, compare `alignment_time` vs `gt_time`:
- **<0.5s**: within half a second (the main target — what we report as "accurate")
- **<1.0s**: within one second
- **<2.0s**: within two seconds
- **<5.0s**: within five seconds
- **Avg delta**: mean absolute error on slides that got a timestamp
- **Per-section oracle**: best accuracy achievable with a single per-section offset — indicates upper bound if timing drift is systematic

### Ground truth offset
The ground truth MIDI triggers fire **before** the lyric appears (the human operator triggers it early so the slide is ready). The offset varies per song and section. When comparing, also test with a fixed offset scan:
```python
for offset in [x * 0.25 for x in range(-20, 21)]:  # -5s to +5s in 0.25s steps
    accuracy = count(abs(alignment_time - (gt_time + offset)) <= 0.5)
```
Report both raw accuracy and best-offset accuracy.

### Slide mapping logic
When a tool returns word-level timestamps, map to slides like this:
```python
word_cursor = 0
for slide_text in all_slides:
    if not slide_text.strip():  # blank slide — skip, assign start_time = -1
        continue
    slide_words = re.findall(r"[a-zA-Z']+", slide_text.lower())
    if slide_words and word_cursor < len(word_timestamps):
        slide_start = word_timestamps[word_cursor]  # first word of slide
        word_cursor += len(slide_words)
```

### Per-slide reporting
Print a table like this for easy reading:
```
   1 t=  10.41 gt=  11.74 d=  1.33 [~] There's an echoing in the Spirit
   2 t=  14.02 gt=  14.61 d=  0.59 [~] If you listen closely you'll hear it
   3 t=  17.85 gt=  17.85 d=  0.00 [OK] Oh what a sound as broken shackles fall
```
Where `[OK]` = <0.5s, `[~]` = <2.0s, `[X]` = >2.0s or no timestamp.

---

## Expected Output

After testing, report back to the MIDI Automation project with:

1. **Accuracy table** comparing all tools at each threshold
2. **Per-slide detail** for each tool (the full list like above)
3. **Timing** — how long does each tool take to process a 4-minute song?
4. **Any errors or failures** and what caused them
5. **Best checkpoint / configuration** if there are options
6. **Recommendation** — which tool (if any) should be integrated into the macOS app pipeline

### Baseline to beat
Current best on HIYH with stable-ts (macOS):
```
<0.5s:  8% (3/37)
<1.0s: 32% (12/37)
<2.0s: 43% (16/37)
<5.0s: 57% (21/37)
Avg delta: 4.8s
Oracle: 42%
```

Across 13-song batch: **30% <0.5s, 54% <1s**

---

## File Organization on Ubuntu Server

Suggested layout:
```
~/alignment_test/
  audio/
    BGVS.wav                        # Here In Your House backing vocals
  lyrics/
    here_in_your_house.txt          # Prepared word list for alignment tools
  ground_truth/
    here_in_your_house_results.json # GT timestamps
    here_in_your_house.json         # Lyrics cache from ProPresenter
  tools/
    NUSAutoLyrixAlign/              # AutoLyrixAlign (unzipped from autolyrixalign.zip)
    ASA_ICASSP2021/                 # ASA (cloned from GitHub)
    E2E-LyricsAlignment/            # E2E (if checkpoints found)
  results/
    autolyrixalign_output.txt       # Raw tool output
    asa_output.txt
    eval_autolyrixalign.txt         # Accuracy report
    eval_asa.txt
```

---

## Notes for the Implementing Claude

- The MIDI Automation project has tested 57 approaches — **do not** re-test anything involving Python/PyTorch stable-ts, wav2vec2, MMS, WhisperX, LyricsAlignment-MTL, or CREPE. Those are already done on macOS.
- The key question is whether Kaldi HMM acoustic models trained specifically on singing can do what speech-trained Whisper cannot.
- AutoLyrixAlign is the highest priority — it's the MIREX winner and easiest to run (Singularity).
- If AutoLyrixAlign works well, the MIDI Automation project will need to figure out how to call it from a macOS app (likely via SSH to this Ubuntu server).
- Report results back to Aaron by updating `test_data/TEST_RESULTS.md` in the MIDI Automation project, or by directly messaging with the findings.

---

## Contact / Coordination

- **MIDI Automation project location:** `/Users/aaronlarson/Library/CloudStorage/OneDrive-NorthwoodsCommunityChurch/VS Code/MIDI Automation/`
- **Data on graphics-mac:** `mediaadmin@10.10.11.77`
- **Tracking documents:**
  - `test_data/ALIGNMENT_APPROACHES.md` — log every tool tried here
  - `test_data/TEST_RESULTS.md` — log every test result here with metrics
