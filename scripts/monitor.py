import calendar
import hashlib
import json
import os
import random
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from config import PODCASTS

load_dotenv()
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_OWNER = os.environ["GITHUB_OWNER"]
GITHUB_REPO = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GIT_AUTHOR_NAME = os.environ.get("GIT_AUTHOR_NAME", "podcast-monitor-bot")
GIT_AUTHOR_EMAIL = os.environ.get("GIT_AUTHOR_EMAIL", "podcast-monitor-bot@users.noreply.github.com")

REPO_DIR = os.environ.get("REPO_DIR", "/tmp/podcast_monitor_repo")
DATA_DIR = os.path.join(REPO_DIR, "data")

LOCAL_TZ = ZoneInfo("Australia/Melbourne")

DIGEST_NAME = "digest"
DIGEST_DISPLAY_NAME = "Run Digest"

INTERVIEWEES_FOLDER = "interviewees"
THEMES_FOLDER = "themes"
ACTORS_FOLDER = "actors"

GEMINI_MODEL = "gemini-3.5-flash-lite"

client = genai.Client(api_key=GEMINI_API_KEY)

BATCH_ANALYSIS_PROMPT = """You are a media analyst for a large organisation. You monitor
morning radio and podcast news programs and brief senior staff on what is being
covered, who is being given airtime, and how stories are being framed.

You have been given the audio of several podcast episodes published since the last
check. They are processed together as one batch. Each audio file comes right after
a label giving its podcast name and episode title or date. Listen to every
episode in full before answering.

Referring to episodes
- Whenever you reference an episode anywhere in your output (interviewees, themes,
  actors or the episode breakdown), copy its podcast name and title exactly as
  given in its label, so it can be matched back to that episode. Don't shorten,
  reword or abbreviate it.

Identifying people
- Give every person a full name and role: first name, surname and current title
  or affiliation (e.g. "Jim Chalmers, Treasurer"; "Sussan Ley, Opposition Leader";
  "Dr Jane Smith, epidemiologist, University of Sydney").
- Work out names and roles from how people are introduced, addressed, referred to
  or signed off. These clues are often spread across an episode: a guest may be
  introduced as "the Treasurer" and only named later, or the reverse. Combine them.
- If the audio gives only part of an identity (a surname, a first name, or a
  role with no name), you may complete it from general knowledge only when the
  match is unambiguous for the time and context. Mark it "(inferred)". If it is
  not unambiguous, keep only what was said and mark it "(unconfirmed)", e.g.
  "Mark, caller from Penrith (unconfirmed)" or "unnamed CFA spokesperson".
- Identify the hosts and presenters of each program by name.

Deduplicating people
- Treat every way of referring to the same person as one person: full name,
  surname only, first name only, title only ("the PM", "the Minister"), or a
  nickname. Merge them into a single entry under one canonical form:
  "Full Name, Role".
- Merge across episodes as well as within them. Someone interviewed on two
  podcasts is one entry that lists both podcasts.
- Don't merge different people who share a surname or a similar title (e.g. a
  minister and their shadow minister, or two state premiers). If you're unsure
  whether two references are the same person, keep them separate and flag it:
  "possibly the same as …".
- If someone's role is given differently in different places, use the one that
  is current at the time of broadcast and the most specific (e.g. "Minister for
  Climate Change and Energy" over "the Minister").
- Use exactly the same canonical name everywhere in your output: interviewees,
  actors, and the voices in each episode's framing.

Types of airtime
- Tell apart live interviews, panels, pre-recorded packages, callers and clips
  from other sources (press conferences, archival audio, parliament). A clip of
  a minister played on air is not an interview with that minister. That
  minister is an actor, not an interviewee, unless they were also interviewed.

What to skip
- Ignore ads, station IDs, sponsor reads, music beds, weather and traffic. Count
  news bulletins only for stories that get real discussion.

Your output has two parts: a batch summary first, then a breakdown of each
episode. Work through every episode in detail before writing the summary, so the
summary reflects all of them, but put the summary first in your output.

PART 1 – BATCH SUMMARY
Combine all episodes:
- Interviewees and panellists: people actually interviewed or on a panel (not
  clips or callers), in canonical form, followed by every episode they appeared
  on, e.g. "Jim Chalmers, Treasurer (ABC AM – 23 Nov 2026; RN Breakfast – 23 Nov
  2026)". List each person once. Order the list so interviewees who appeared on multiple
  podcast episodes appear first. 
- Themes: the major stories across the batch. If several podcasts covered the same
  story, merge it into ONE theme and list the episodes that covered it. Where those
  episodes framed it differently, add one sentence on the contrast (e.g. "AM led on
  the economic cost; RN Breakfast on the political fallout"). Order the themes
  so themes repeated on multiple episodes appear first.
- Actors: the main people and organisations being discussed (not only those
  interviewed), in canonical form, each merged into a single entry and listing
  the episodes they came up in. Order the list so actors who appeared across 
  multiple episodes appear first.

PART 2 – EPISODE BREAKDOWN
For each episode, in the order given:
- Podcast name and episode title or date, exactly as labelled.
- Hosts and presenters.
- Main stories: the 3–6 most significant stories in the episode, in running order (the most
important stories are first). Some episodes may only have one story.
  For each one, give its approximate start time (mm:ss) and roughly how long it ran.
- For each story, describe its framing:
  - Angle: the central question or storyline the program built the item around
    (e.g. "a cost-of-living failure by the government" vs "a necessary budget
    repair measure").
  - Voices: who was given airtime, in canonical form, and who was absent or only
    referred to. Say whether each voice was live, pre-recorded or a clip. Note
    whether the host challenged a guest, interrupted them, or let them speak
    unchallenged.
  - Tone: the editorial stance (critical, sympathetic, adversarial, explanatory,
    alarmed, etc.) and towards whom. Include the host's delivery where it adds
    to that (scepticism, urgency, warmth, sarcasm), and any sound design
    that shapes the item (dramatic music, emotional vox pops, sirens).
  - Emphasis: what was foregrounded (human impact, cost, blame, process, conflict,
    numbers) and any notable context that was left out or treated as a side issue.
  - Placement: whether it was the lead item, and how much time it got compared
    with other stories in the episode.
  - Evidence: one short quote (under 25 words) that best shows the framing,
    with its timestamp and the speaker. Quote as exactly as you can hear it.
    If you are not confident of the exact wording, paraphrase and label it
    "(paraphrase)".
  Describe the framing from what was actually broadcast. Don't speculate about
  editorial motives or allege bias the audio doesn't show.

Rules
- Include only items that matter to a senior briefing reader. Pay particular
  attention to politics, disasters, anniversaries and controversies.
- Keep list entries to a short phrase. Framing descriptions can run to 1–2 sentences
  per element.
- Order themes and actors from most to least covered, counting a mention once per
  episode it appears in (not per sentence). Break ties by total airtime.
- Base every claim about what was said, who said it and how it was framed on the
  audio. General knowledge may be used only to complete a partial name or role,
  and must be marked "(inferred)" as described above. Anything you can't make out
  is marked "(unconfirmed)" or "(paraphrase)". Never guess silently.
- Use Australian English spelling."""

_EPISODE_REF_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "podcast": types.Schema(type=types.Type.STRING),
        "title": types.Schema(type=types.Type.STRING),
    },
    required=["podcast", "title"],
)

_STORY_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "story": types.Schema(type=types.Type.STRING),
        "start_time": types.Schema(type=types.Type.STRING),
        "duration": types.Schema(type=types.Type.STRING),
        "angle": types.Schema(type=types.Type.STRING),
        "voices": types.Schema(type=types.Type.STRING),
        "tone": types.Schema(type=types.Type.STRING),
        "emphasis": types.Schema(type=types.Type.STRING),
        "placement": types.Schema(type=types.Type.STRING),
        "evidence": types.Schema(type=types.Type.STRING),
    },
    required=["story", "start_time", "angle", "voices", "tone", "emphasis", "placement", "evidence"],
)

_EPISODE_ANALYSIS_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "podcast": types.Schema(type=types.Type.STRING),
        "title": types.Schema(type=types.Type.STRING),
        "stories": types.Schema(type=types.Type.ARRAY, items=_STORY_SCHEMA),
    },
    required=["podcast", "title", "stories"],
)

_INTERVIEWEE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "name": types.Schema(type=types.Type.STRING),
        "role": types.Schema(type=types.Type.STRING),
        "episodes": types.Schema(type=types.Type.ARRAY, items=_EPISODE_REF_SCHEMA),
    },
    required=["name", "episodes"],
)

_THEME_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "theme": types.Schema(type=types.Type.STRING),
        "contrast": types.Schema(type=types.Type.STRING),
        "episodes": types.Schema(type=types.Type.ARRAY, items=_EPISODE_REF_SCHEMA),
    },
    required=["theme", "episodes"],
)

_ACTOR_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "actor": types.Schema(type=types.Type.STRING),
        "episodes": types.Schema(type=types.Type.ARRAY, items=_EPISODE_REF_SCHEMA),
    },
    required=["actor", "episodes"],
)

ANALYSIS_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "episodes": types.Schema(type=types.Type.ARRAY, items=_EPISODE_ANALYSIS_SCHEMA),
        "interviewees": types.Schema(type=types.Type.ARRAY, items=_INTERVIEWEE_SCHEMA),
        "themes": types.Schema(type=types.Type.ARRAY, items=_THEME_SCHEMA),
        "actors": types.Schema(type=types.Type.ARRAY, items=_ACTOR_SCHEMA),
    },
    required=["episodes", "interviewees", "themes", "actors"],
)


def generate_with_retry(**kwargs):
    attempts = 5
    for attempt in range(1, attempts + 1):
        try:
            return client.models.generate_content(**kwargs)
        except genai_errors.ServerError as exc:
            if attempt == attempts:
                raise
            delay = min(60, 5 * 2 ** (attempt - 1)) + random.uniform(0, 3)
            print(f"Gemini request failed ({exc}), retrying in {delay:.0f}s (attempt {attempt}/{attempts})")
            time.sleep(delay)


def episode_id(audio_url):
    """The audio enclosure URL is the stable unique key for an episode — titles get
    tweaked and some feeds reuse/rewrite guids, but the audio file URL doesn't change."""
    return hashlib.sha1(audio_url.encode()).hexdigest()[:16]


SEEN_PATH = os.path.join(DATA_DIR, "seen.json")

# Dedup only ever matters for episodes published "today" — anything older is
# already excluded by the same-day filter in collect_new_episodes() regardless
# of whether it's in seen.json. This retention window is just a safety buffer
# (clock skew, a run straddling midnight), so seen.json doesn't grow forever.
SEEN_RETENTION_DAYS = 7


def _load_all_seen():
    if not os.path.exists(SEEN_PATH):
        return {}
    with open(SEEN_PATH) as f:
        return json.load(f)


def _prune_seen(all_seen, today_local):
    cutoff = (today_local - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    pruned = {}
    for podcast_name, entries in all_seen.items():
        kept = {eid: date_str for eid, date_str in entries.items() if date_str >= cutoff}
        if kept:
            pruned[podcast_name] = kept
    return pruned


def _load_seen(podcast_name):
    return set(_load_all_seen().get(podcast_name, {}).keys())


def _save_seen(podcast_name, new_ids_with_dates):
    # Re-reads so sequential saves for different podcasts within one run don't
    # clobber each other's entries in the shared file.
    all_seen = _load_all_seen()
    existing = all_seen.get(podcast_name, {})
    existing.update(new_ids_with_dates)
    all_seen[podcast_name] = existing

    all_seen = _prune_seen(all_seen, datetime.now(tz=LOCAL_TZ).date())

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SEEN_PATH, "w") as f:
        json.dump(all_seen, f, indent=2)


def collect_new_episodes(podcast):
    name = podcast["name"]
    feed = feedparser.parse(podcast["rss_url"])
    seen_ids = _load_seen(name)

    is_first_run = len(seen_ids) == 0
    entries = feed.entries[:1] if is_first_run else feed.entries

    today_local = datetime.now(tz=LOCAL_TZ).date()
    new_entries = []
    for entry in entries:
        if not entry.get("enclosures"):
            continue
        audio_url = entry.enclosures[0].href

        eid = episode_id(audio_url)
        if eid in seen_ids:
            continue

        published = entry.get("published_parsed")
        if not published:
            continue
        published_dt = datetime.fromtimestamp(calendar.timegm(published), tz=timezone.utc)
        if published_dt.astimezone(LOCAL_TZ).date() != today_local:
            continue

        print(f"[{name}] new episode: {entry.title}")
        new_entries.append(
            {
                "id": eid,
                # Every entry reaching here passed the same-day check above, so
                # today_local IS this entry's local published date.
                "published_date_local": today_local.isoformat(),
                "podcast_key": name,
                "podcast": podcast["display_name"],
                "title": entry.title,
                "guid": entry.get("id") or entry.get("link"),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "audio_url": audio_url,
            }
        )

    return new_entries


def download_audio(audio_url):
    # Some CDNs (e.g. Omny) reject Python's default urllib user agent with a 403.
    request = Request(audio_url, headers={"User-Agent": "Mozilla/5.0 (compatible; podcast-monitor-bot)"})
    with urlopen(request, timeout=1200) as resp:
        content_type = resp.headers.get("Content-Type", "audio/mpeg").split(";")[0].strip()
        return resp.read(), content_type


def upload_audio(audio_bytes, content_type):
    with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        return client.files.upload(file=tmp.name, config=types.UploadFileConfig(mime_type=content_type))


def analyse_batch_audio(entries):
    """Downloads and uploads every entry's audio, sends it all to Gemini in one
    request alongside BATCH_ANALYSIS_PROMPT, and returns (analysis, processed_entries)
    — processed_entries excludes any entry whose audio failed to download/upload."""
    uploaded_files = []
    contents = []
    processed = []

    for entry in entries:
        try:
            audio_bytes, content_type = download_audio(entry["audio_url"])
            uploaded = upload_audio(audio_bytes, content_type)
        except Exception as exc:
            print(f"[{entry['podcast_key']}] failed to fetch audio for '{entry['title']}', skipping: {exc}")
            continue
        uploaded_files.append(uploaded)
        contents.append(f'Podcast: "{entry["podcast"]}" — Episode: "{entry["title"]}"')
        contents.append(uploaded)
        processed.append(entry)

    if not processed:
        return None, []

    contents.append(BATCH_ANALYSIS_PROMPT)

    try:
        response = generate_with_retry(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ANALYSIS_RESPONSE_SCHEMA,
            ),
        )
        analysis = json.loads(response.text)
    finally:
        for uploaded in uploaded_files:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass

    return analysis, processed


def _html_list(items):
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def _html_interviewees_list(interviewees):
    # Renders both the current {name, role, episodes} shape and plain strings
    # from digest archive files written before this schema existed.
    items = []
    for person in interviewees:
        if isinstance(person, str):
            items.append(person)
            continue
        podcasts = "; ".join(
            dict.fromkeys(
                ref.get("podcast", "") for ref in person.get("episodes", []) if isinstance(ref, dict)
            )
        )
        name = person.get("name", "")
        label = f"{name}, {person['role']}" if person.get("role") else name
        items.append(f"{label} ({podcasts})" if podcasts else label)
    return _html_list(items)


def _html_themes_list(themes):
    # Same backward-compatibility concern as _html_interviewees_list above.
    items = []
    for t in themes:
        if isinstance(t, str):
            items.append(t)
            continue
        items.append(f"{t['theme']} — {t['contrast']}" if t.get("contrast") else t.get("theme", ""))
    return _html_list(items)


def _html_actors_list(actors):
    # Same backward-compatibility concern as _html_interviewees_list above.
    return _html_list(a if isinstance(a, str) else a.get("actor", "") for a in actors)


def _html_episode_breakdown(episodes):
    # Older digest archive files have episodes with no "stories" key at all, or
    # stories missing fields (start_time, duration, placement) added later —
    # every field here is read defensively so old records still render.
    sections = []
    for episode in episodes:
        stories = "".join(
            f"<li><strong>{s.get('story', '')}</strong>"
            + (
                f" ({s['start_time']}" + (f", {s['duration']}" if s.get("duration") else "") + ")"
                if s.get("start_time")
                else ""
            )
            + f" — {s.get('angle', '')}<br>"
            f"Voices: {s.get('voices', '')}<br>"
            f"Tone: {s.get('tone', '')}<br>"
            f"Emphasis: {s.get('emphasis', '')}<br>"
            + (f"Placement: {s['placement']}<br>" if s.get("placement") else "")
            + f"&ldquo;{s.get('evidence', '')}&rdquo;</li>"
            for s in episode.get("stories", [])
        )
        sections.append(
            f"<p><strong>{episode.get('podcast', '')}: {episode.get('title', '')}</strong></p><ul>{stories}</ul>"
        )
    return "".join(sections)


def _format_run_title(run_at_iso, episode_count):
    local_dt = datetime.fromisoformat(run_at_iso).astimezone(ZoneInfo("Australia/Melbourne"))
    hour12 = local_dt.strftime("%I").lstrip("0") or "12"
    time_str = f"{hour12}:{local_dt.strftime('%M')}{local_dt.strftime('%p').lower()}"
    return f"{episode_count} episode(s) — {time_str} {local_dt.strftime('%d/%b')}"


def build_digest_feed_xml(archive_dir):
    records = []
    for filename in sorted(os.listdir(archive_dir), reverse=True):
        with open(os.path.join(archive_dir, filename)) as f:
            records.append(json.load(f))

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = DIGEST_DISPLAY_NAME
    ET.SubElement(channel, "description").text = (
        "Combined interviewees, themes and actors across every episode processed in each monitor run."
    )

    for record in records:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = _format_run_title(
            record["run_at"], len(record["episodes"])
        )
        ET.SubElement(item, "guid").text = record["run_at"]
        ET.SubElement(item, "pubDate").text = record["run_at"]

        description = (
            f"<p><strong>Interviewees:</strong></p>{_html_interviewees_list(record.get('interviewees', []))}"
            f"<p><strong>Key Themes:</strong></p>{_html_themes_list(record.get('themes', []))}"
            f"<p><strong>Key Actors:</strong></p>{_html_actors_list(record.get('actors', []))}"
            f"<p><strong>Episodes in this run:</strong></p>{_html_episode_breakdown(record.get('episodes', []))}"
        )
        ET.SubElement(item, "description").text = description

    return ET.tostring(rss, encoding="unicode")


def write_digest(analysis, run_at):
    digest_dir = os.path.join(DATA_DIR, DIGEST_NAME)
    archive_dir = os.path.join(digest_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)

    record = {
        "run_at": run_at.isoformat(),
        "episodes": analysis["episodes"],
        "interviewees": analysis["interviewees"],
        "actors": analysis["actors"],
        "themes": analysis["themes"],
    }

    with open(
        os.path.join(archive_dir, f"{run_at.strftime('%Y-%m-%dT%H-%M-%SZ')}.json"), "w"
    ) as f:
        json.dump(record, f, indent=2)

    feed_xml = build_digest_feed_xml(archive_dir)
    with open(os.path.join(digest_dir, "feed.xml"), "w") as f:
        f.write(feed_xml)


def _resolve_episode_refs(episode_refs, lookup):
    resolved = []
    for ref in episode_refs:
        entry = lookup.get((ref["podcast"], ref["title"]))
        if entry is None:
            print(f"warning: couldn't match episode reference back to a processed episode: {ref}")
            continue
        resolved.append(entry)
    return resolved


def _quarter_parquet_path(folder_name, run_at_local):
    quarter = (run_at_local.month - 1) // 3 + 1
    out_dir = os.path.join(DATA_DIR, folder_name)
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, f"{run_at_local.year}Q{quarter}.parquet")


def _append_parquet_rows(folder_name, rows, run_at_local):
    if not rows:
        return

    path = _quarter_parquet_path(folder_name, run_at_local)
    new_df = pd.DataFrame(rows)
    if os.path.exists(path):
        existing_df = pd.read_parquet(path)
        combined = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_parquet(path, index=False)


def write_parquet_outputs(analysis, processed_entries, run_at):
    run_at_local = run_at.astimezone(LOCAL_TZ)
    lookup = {(e["podcast"], e["title"]): e for e in processed_entries}

    interviewee_rows = []
    for person in analysis["interviewees"]:
        for entry in _resolve_episode_refs(person.get("episodes", []), lookup):
            interviewee_rows.append(
                {
                    "run_at": run_at.isoformat(),
                    "name": person["name"],
                    "role": person.get("role", ""),
                    "podcast": entry["podcast"],
                    "episode_title": entry["title"],
                    "episode_link": entry.get("link", ""),
                    "audio_url": entry["audio_url"],
                }
            )
    _append_parquet_rows(INTERVIEWEES_FOLDER, interviewee_rows, run_at_local)

    theme_rows = []
    for theme in analysis["themes"]:
        for entry in _resolve_episode_refs(theme.get("episodes", []), lookup):
            theme_rows.append(
                {
                    "run_at": run_at.isoformat(),
                    "theme": theme["theme"],
                    "contrast": theme.get("contrast", ""),
                    "podcast": entry["podcast"],
                    "episode_title": entry["title"],
                    "episode_link": entry.get("link", ""),
                    "audio_url": entry["audio_url"],
                }
            )
    _append_parquet_rows(THEMES_FOLDER, theme_rows, run_at_local)

    actor_rows = []
    for actor in analysis["actors"]:
        for entry in _resolve_episode_refs(actor.get("episodes", []), lookup):
            actor_rows.append(
                {
                    "run_at": run_at.isoformat(),
                    "actor": actor["actor"],
                    "podcast": entry["podcast"],
                    "episode_title": entry["title"],
                    "episode_link": entry.get("link", ""),
                    "audio_url": entry["audio_url"],
                }
            )
    _append_parquet_rows(ACTORS_FOLDER, actor_rows, run_at_local)


def clone_repo():
    if os.path.exists(REPO_DIR):
        shutil.rmtree(REPO_DIR)

    remote = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", GITHUB_BRANCH, remote, REPO_DIR],
        check=True,
    )
    subprocess.run(["git", "-C", REPO_DIR, "config", "user.email", GIT_AUTHOR_EMAIL], check=True)
    subprocess.run(["git", "-C", REPO_DIR, "config", "user.name", GIT_AUTHOR_NAME], check=True)


def git_commit_and_push(total_new):
    subprocess.run(["git", "add", "data"], cwd=REPO_DIR, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"Process {total_new} new episode(s)"],
        cwd=REPO_DIR,
        check=True,
    )
    subprocess.run(["git", "push"], cwd=REPO_DIR, check=True)


def main():
    clone_repo()

    all_new = []
    for podcast in PODCASTS:
        try:
            all_new.extend(collect_new_episodes(podcast))
        except Exception as exc:
            print(f"[{podcast['name']}] failed to check feed: {exc}")

    if not all_new:
        print("No new episodes.")
        return

    print(f"{len(all_new)} new episode(s) found, sending audio to Gemini in one batch...")

    try:
        analysis, processed = analyse_batch_audio(all_new)
    except Exception as exc:
        print(f"batch analysis failed, nothing processed this run: {exc}")
        return

    if not processed:
        print("All episodes failed to download, nothing to analyse.")
        return

    run_at = datetime.now(timezone.utc)
    write_digest(analysis, run_at)
    write_parquet_outputs(analysis, processed, run_at)

    by_podcast = {}
    for entry in processed:
        by_podcast.setdefault(entry["podcast_key"], {})[entry["id"]] = entry["published_date_local"]
    for podcast_key, new_ids_with_dates in by_podcast.items():
        _save_seen(podcast_key, new_ids_with_dates)

    print(f"{len(processed)} episode(s) processed, pushing to git...")
    git_commit_and_push(len(processed))


if __name__ == "__main__":
    main()
