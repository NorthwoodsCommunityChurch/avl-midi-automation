#!/usr/bin/env python3
"""Analyze per-section offsets across all tested songs to find patterns."""

import json
import os
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

for f in sorted(glob.glob(os.path.join(SCRIPT_DIR, '*_results.json'))):
    if 'batch_results' in f:
        continue
    with open(f) as fp:
        data = json.load(fp)

    sections = data.get('per_section', [])
    if not sections:
        continue

    name = data.get('song_name', os.path.basename(f))
    global_offset = data.get('global_offset', 0)
    print(f"=== {name} (global: {global_offset:+.1f}s) ===")
    for sec in sections:
        matched = sec.get('matched', 0)
        if matched > 0:
            sec_off = sec.get('section_offset', 0)
            std = sec.get('std_dev', 0)
            sec_name = sec.get('section_name', '?')
            total = sec.get('total', '?')
            w05 = sec.get('within_0.5s', '?')
            print(f"  {sec_name:20s}  off={sec_off:+6.1f}s  std={std:4.1f}s  "
                  f"matched={matched}/{total}  w/off={w05}")
    print()
