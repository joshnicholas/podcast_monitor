import calendar
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import feedparser
from dotenv import load_dotenv
from google import genai
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

GEMINI_MODEL = "gemini-3.5-flash-lite"

client = genai.Client(api_key=GEMINI_API_KEY)

TRANSCRIPT_PROMPT = """Generate a complete, verbatim transcript of this audio.
Label distinct speakers, switching the label every time the speaker changes.
If you can tell a speaker's name or role from how they're introduced or addressed,
use that as the label (e.g. "Host:" or "Jane Smith:"); otherwise use "Speaker 1:",
"Speaker 2:", etc. Output only the transcript, one turn per line."""

BATCH_ANALYSIS_PROMPT = """You are a media analyst for a large organisation.
Your job is to read transcripts of morning radio shows and summarise the important news stories,
players and discussions of the day.
Pay particular attention to who is being interviewed and the topics they are discussing
- especially politics, disasters, anniversaries, controversies.

The following podcast episodes were all published since the last check and are being
processed together as one batch. Each transcript is labelled with the podcast it came
from. Read all the transcripts below and answer the following questions across the
WHOLE batch combined, not per episode:
- Who is being interviewed or on a panel, if anyone? For each one, give their name and
  role, followed by the podcast they were interviewed on in brackets, e.g.
  "Jane Smith, Treasurer (ABC AM)".
- What are the major stories being discussed? If multiple podcasts cover the same
  story, merge it into ONE theme rather than listing a reworded duplicate for each
  podcast that covered it.
- Who are the main actors being discussed? Merge the same person or organisation into
  ONE entry even if they come up across multiple podcasts.
Condense each to a short phrase. Only mention an interviewee, actor or theme if it is
important. Order the "themes" and "actors" lists from most to least mentioned, counting
a mention once per podcast it appears in (not per sentence), so the most widely-covered
stories and actors come first.

Transcripts:
{transcripts}
"""

ANALYSIS_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "interviewees": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
        "actors": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
        "themes": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
    },
    required=["interviewees", "actors", "themes"],
)


def episode_id(entry):
    guid = entry.get("id") or entry.get("link")
    digest = hashlib.sha1(guid.encode()).hexdigest()[:12]
    published = entry.get("published_parsed")
    date_prefix = time.strftime("%Y-%m-%d", published) if published else "0000-00-00"
    return f"{date_prefix}_{digest}"


def transcribe_audio(audio_url):
    with urlopen(Request(audio_url), timeout=1200) as resp:
        content_type = resp.headers.get("Content-Type", "audio/mpeg").split(";")[0].strip()
        audio_bytes = resp.read()

    with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        uploaded = client.files.upload(
            file=tmp.name,
            config=types.UploadFileConfig(mime_type=content_type),
        )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[uploaded, TRANSCRIPT_PROMPT],
        )
        return response.text.strip()
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass


def _is_cue_line(line):
    line = line.strip()
    if not line or line == "WEBVTT" or "-->" in line:
        return True
    # bare cue numbers (SRT) or bare timestamps (e.g. some "plain text with
    # timestamps" transcript formats interleave these between speaker turns)
    return bool(re.fullmatch(r"\d+", line) or re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?(\.\d+)?", line))


def fetch_published_transcript(transcript_tag):
    url = transcript_tag.get("url")
    if not url:
        return None

    content_type = (transcript_tag.get("type") or "").lower()
    with urlopen(Request(url), timeout=60) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    if "json" in content_type:
        segments = json.loads(raw).get("segments", [])
        lines = [
            f"Speaker {seg['speaker']}: {seg['body']}" if seg.get("speaker") else seg["body"]
            for seg in segments
            if seg.get("body")
        ]
        return "\n".join(lines) or None

    if "html" in content_type:
        text = re.sub(r"<[^>]+>", " ", raw)
        return re.sub(r"\s+", " ", text).strip() or None

    # covers plain text, SRT and VTT, all of which may mix in cue/timestamp lines
    lines = [line.strip() for line in raw.splitlines() if not _is_cue_line(line)]
    return "\n".join(lines) or None


def analyse_batch(entries):
    transcripts = "\n\n".join(
        f"### {entry['podcast']} — {entry['title']}\n{entry['transcript']}"
        for entry in entries
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=BATCH_ANALYSIS_PROMPT.format(transcripts=transcripts),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ANALYSIS_RESPONSE_SCHEMA,
        ),
    )
    return json.loads(response.text)


def _html_list(items):
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


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
        episode_list = _html_list(
            f"{e['podcast']}: {e['title']}" for e in record["episodes"]
        )
        ET.SubElement(item, "title").text = _format_run_title(
            record["run_at"], len(record["episodes"])
        )
        ET.SubElement(item, "guid").text = record["run_at"]
        ET.SubElement(item, "pubDate").text = record["run_at"]

        description = (
            f"<p><strong>Interviewees:</strong></p>{_html_list(record.get('interviewees', []))}"
            f"<p><strong>Key Themes:</strong></p>{_html_list(record.get('themes', []))}"
            f"<p><strong>Key Actors:</strong></p>{_html_list(record.get('actors', []))}"
            f"<p><strong>Episodes in this run:</strong></p>{episode_list}"
        )
        ET.SubElement(item, "description").text = description

    return ET.tostring(rss, encoding="unicode")


def write_digest(entries):
    digest_dir = os.path.join(DATA_DIR, DIGEST_NAME)
    archive_dir = os.path.join(digest_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)

    analysis = analyse_batch(entries)
    run_at = datetime.now(timezone.utc)

    record = {
        "run_at": run_at.isoformat(),
        "episodes": [{"podcast": e["podcast"], "title": e["title"]} for e in entries],
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


def process_podcast(podcast):
    name = podcast["name"]
    podcast_dir = os.path.join(DATA_DIR, name)
    archive_dir = os.path.join(podcast_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)

    feed = feedparser.parse(podcast["rss_url"])
    existing_ids = {os.path.splitext(f)[0] for f in os.listdir(archive_dir)}

    is_first_run = len(existing_ids) == 0
    entries = feed.entries[:1] if is_first_run else feed.entries

    new_count = 0
    new_entries = []
    for entry in entries:
        eid = episode_id(entry)
        if eid in existing_ids:
            continue

        published = entry.get("published_parsed")
        if not published:
            continue
        published_dt = datetime.fromtimestamp(calendar.timegm(published), tz=timezone.utc)
        today_local = datetime.now(tz=LOCAL_TZ).date()
        if published_dt.astimezone(LOCAL_TZ).date() != today_local:
            continue

        print(f"[{name}] new episode: {entry.title}")
        audio_url = entry.enclosures[0].href

        transcript = None
        published_transcript = entry.get("podcast_transcript")
        if published_transcript:
            try:
                transcript = fetch_published_transcript(published_transcript)
            except Exception as exc:
                print(f"[{name}] couldn't use published transcript, transcribing instead: {exc}")

        if not transcript:
            transcript = transcribe_audio(audio_url)

        record = {
            "title": entry.title,
            "guid": entry.get("id") or entry.get("link"),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
            "audio_url": audio_url,
            "transcript": transcript,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        with open(os.path.join(archive_dir, f"{eid}.json"), "w") as f:
            json.dump(record, f, indent=2)

        new_count += 1
        new_entries.append(
            {
                "podcast": podcast["display_name"],
                "title": entry.title,
                "transcript": transcript,
            }
        )

    return new_count, new_entries


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
        ["git", "commit", "-m", f"Add {total_new} new episode(s)"],
        cwd=REPO_DIR,
        check=True,
    )
    subprocess.run(["git", "push"], cwd=REPO_DIR, check=True)


def main():
    clone_repo()

    total_new = 0
    batch_entries = []
    for podcast in PODCASTS:
        try:
            new_count, new_entries = process_podcast(podcast)
            total_new += new_count
            batch_entries.extend(new_entries)
        except Exception as exc:
            print(f"[{podcast['name']}] failed: {exc}")

    if batch_entries:
        write_digest(batch_entries)

    if total_new:
        print(f"{total_new} new episode(s) processed, pushing to git...")
        git_commit_and_push(total_new)
    else:
        print("No new episodes.")


if __name__ == "__main__":
    main()
