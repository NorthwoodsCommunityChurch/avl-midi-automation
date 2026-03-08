#!/usr/bin/env python3
"""
Fetch song lyrics and arrangements from ProPresenter 7 REST API.

Searches the Pro7 library by song name, fetches groups + slides + arrangements,
and caches the result as JSON for offline testing.

Usage:
  python3 fetch_lyrics.py "Here In Your House"
  python3 fetch_lyrics.py --uuid 73675E8A-27DF-47F6-A050-98200567F640
  python3 fetch_lyrics.py --list  # list all songs in the library
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error

# Pro7 connection defaults
PRO7_HOST = "10.10.11.76"
PRO7_PORT = 57131
LIBRARY_UUID = "93666554-FE8D-4C9C-AD51-506C2CE8BBFB"  # "Lyrics" library

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lyrics_cache")


def pro7_get(path):
    """Make a GET request to the Pro7 REST API."""
    url = f"http://{PRO7_HOST}:{PRO7_PORT}/v1/{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"Error connecting to Pro7 at {PRO7_HOST}:{PRO7_PORT}: {e}", file=sys.stderr)
        sys.exit(1)


def list_library():
    """List all songs in the Lyrics library."""
    data = pro7_get(f"library/{LIBRARY_UUID}")
    items = data if isinstance(data, list) else data.get("items", data.get("presentations", []))
    for item in sorted(items, key=lambda x: x.get("name", "").lower()):
        print(f"  {item.get('name', '?'):50s}  uuid={item.get('uuid', '?')}")
    print(f"\n{len(items)} songs total")
    return items


def search_library(song_name):
    """Search the library for a song by name. Returns (uuid, name) or None."""
    data = pro7_get(f"library/{LIBRARY_UUID}")
    items = data if isinstance(data, list) else data.get("items", data.get("presentations", []))

    # Normalize for matching
    query = re.sub(r'[^\w\s]', '', song_name.lower()).strip()

    # Try exact match first
    for item in items:
        name = item.get("name", "")
        normalized = re.sub(r'[^\w\s]', '', name.lower()).strip()
        if normalized == query:
            return item["uuid"], name

    # Try substring match
    matches = []
    for item in items:
        name = item.get("name", "")
        normalized = re.sub(r'[^\w\s]', '', name.lower()).strip()
        if query in normalized or normalized in query:
            matches.append((item["uuid"], name))

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        print(f"Multiple matches for '{song_name}':", file=sys.stderr)
        for uuid, name in matches:
            print(f"  {name}  (uuid={uuid})", file=sys.stderr)
        print("Use --uuid to specify exactly.", file=sys.stderr)
        return None
    else:
        print(f"No match found for '{song_name}' in Pro7 library.", file=sys.stderr)
        return None


def fetch_presentation(uuid):
    """Fetch full presentation data (groups, slides, arrangements)."""
    data = pro7_get(f"presentation/{uuid}")
    pres = data.get("presentation", data)

    song_name = pres.get("id", {}).get("name", "Unknown")

    # Parse groups with UUIDs
    groups = []
    for g in pres.get("groups", []):
        group_uuid = g.get("uuid", "")
        group_name = g.get("name", "")
        slides = []
        for s in g.get("slides", []):
            slides.append({
                "text": s.get("text", ""),
                "label": s.get("label", ""),
                "enabled": s.get("enabled", True),
            })
        groups.append({
            "uuid": group_uuid,
            "name": group_name,
            "slides": slides,
        })

    # Parse arrangements
    arrangements = []
    for arr in pres.get("arrangements", []):
        arr_id = arr.get("id", {})
        arrangements.append({
            "name": arr_id.get("name", ""),
            "uuid": arr_id.get("uuid", ""),
            "group_uuids": arr.get("groups", []),
        })

    current_arrangement = pres.get("current_arrangement", "")

    return {
        "song_name": song_name,
        "pro7_uuid": uuid,
        "groups": groups,
        "arrangements": arrangements,
        "current_arrangement": current_arrangement,
    }


def expand_arrangement(cached_data, arrangement_name="MIDI"):
    """
    Expand a named arrangement into a flat list of slides.

    Returns list of dicts: [{text, group_name, is_blank}, ...]
    """
    # Find the arrangement
    arrangement = None
    for arr in cached_data.get("arrangements", []):
        if arr["name"].lower() == arrangement_name.lower():
            arrangement = arr
            break

    if arrangement is None:
        available = [a["name"] for a in cached_data.get("arrangements", [])]
        print(f"Arrangement '{arrangement_name}' not found. Available: {available}", file=sys.stderr)
        return None

    # Build group UUID -> group data map
    group_map = {}
    for g in cached_data["groups"]:
        group_map[g["uuid"]] = g

    # Expand
    slides = []
    for group_uuid in arrangement["group_uuids"]:
        group = group_map.get(group_uuid)
        if group is None:
            print(f"Warning: group UUID {group_uuid} not found in groups", file=sys.stderr)
            continue
        for s in group["slides"]:
            if not s.get("enabled", True):
                continue
            text = s["text"].strip()
            slides.append({
                "text": text,
                "group_name": group["name"],
                "is_blank": len(text) == 0,
            })

    return slides


def cache_path(song_name):
    """Get cache file path for a song name."""
    safe_name = re.sub(r'[^\w\s-]', '', song_name).strip().lower().replace(' ', '_')
    return os.path.join(CACHE_DIR, f"{safe_name}.json")


def save_cache(data):
    """Save presentation data to cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = cache_path(data["song_name"])
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Cached to: {path}")
    return path


def load_cache(song_name):
    """Load cached presentation data, or None if not cached.

    First tries exact match, then fuzzy match (cache filename contains
    the normalized song name). This handles cases like:
    - .als name "Build My Life" → cache "build_my_life_1_line.json"
    - .als name "At The Cross" → cache "at_the_cross_love_ran_red.json"
    """
    # Exact match
    path = cache_path(song_name)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)

    # Fuzzy match: look for cache files whose song_name contains our query
    if os.path.isdir(CACHE_DIR):
        query = re.sub(r'[^\w\s]', '', song_name).strip().lower()
        for fname in os.listdir(CACHE_DIR):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(CACHE_DIR, fname)
            try:
                with open(fpath) as f:
                    data = json.load(f)
                cached_name = re.sub(r'[^\w\s]', '', data.get('song_name', '')).strip().lower()
                if query in cached_name or cached_name in query:
                    return data
            except (json.JSONDecodeError, KeyError):
                continue

    return None


def print_summary(data):
    """Print a human-readable summary of the fetched data."""
    print(f"\nSong: {data['song_name']}")
    print(f"UUID: {data['pro7_uuid']}")
    print(f"\nGroups ({len(data['groups'])}):")
    total = 0
    for g in data["groups"]:
        enabled = [s for s in g["slides"] if s.get("enabled", True)]
        texts = [s["text"][:50] for s in enabled if s["text"].strip()]
        blanks = len(enabled) - len(texts)
        total += len(enabled)
        blank_note = f" + {blanks} blank" if blanks else ""
        print(f"  {g['name']}: {len(texts)} slides{blank_note}")

    print(f"\nArrangements ({len(data['arrangements'])}):")
    for arr in data["arrangements"]:
        expanded = expand_arrangement(data, arr["name"])
        if expanded:
            lyric_count = sum(1 for s in expanded if not s["is_blank"])
            blank_count = sum(1 for s in expanded if s["is_blank"])
            print(f"  {arr['name']}: {len(expanded)} total ({lyric_count} lyric, {blank_count} blank)")
        else:
            print(f"  {arr['name']}: {len(arr['group_uuids'])} groups")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 fetch_lyrics.py 'Song Name'")
        print("  python3 fetch_lyrics.py --uuid <presentation-uuid>")
        print("  python3 fetch_lyrics.py --list")
        sys.exit(1)

    if sys.argv[1] == "--list":
        list_library()
        return

    if sys.argv[1] == "--uuid":
        if len(sys.argv) < 3:
            print("Need UUID after --uuid", file=sys.stderr)
            sys.exit(1)
        uuid = sys.argv[2]
        data = fetch_presentation(uuid)
    else:
        song_name = " ".join(sys.argv[1:])
        result = search_library(song_name)
        if result is None:
            sys.exit(1)
        uuid, matched_name = result
        print(f"Found: {matched_name} (uuid={uuid})")
        data = fetch_presentation(uuid)

    print_summary(data)
    save_cache(data)

    # Also show expanded MIDI arrangement if it exists
    expanded = expand_arrangement(data, "MIDI")
    if expanded:
        print(f"\nMIDI Arrangement expanded ({len(expanded)} slides):")
        for i, s in enumerate(expanded):
            marker = "[BLANK]" if s["is_blank"] else s["text"][:60]
            print(f"  {i+1:3d}. [{s['group_name']}] {marker}")


if __name__ == "__main__":
    main()
