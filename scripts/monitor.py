import calendar
import hashlib
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

import feedparser
from dotenv import load_dotenv

from config import PODCASTS

load_dotenv()
DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(REPO_ROOT, "data")

MAX_EPISODE_AGE = timedelta(days=2)

DIGEST_NAME = "digest"
DIGEST_DISPLAY_NAME = "Run Digest"

ANALYSIS_PROMPT = """You are a media analyst for a large organisation. Your job is to read the news and summarise the important news stories, players and discussions of the day.
Pay particular attention to politics, disasters, anniversaries, controveries.
Read the following podcast transcript. Answer two questions - who and what are the main news stories.
Condense each to a sentence explanation. Only mention an actor or theme if it is important.

Respond with ONLY a JSON object of the form:
{{"actors": ["...", "..."], "themes": ["...", "..."]}}

Transcript:
{transcript}
"""

BATCH_ANALYSIS_PROMPT = """You are a media analyst for a large organisation. The following podcast episodes
were all published since the last check and are being processed together as one batch.
Read all the transcripts below and identify the main actors and themes across the WHOLE
batch combined, not per episode. Condense each to a short phrase. Only mention an actor
or theme if it is important.

Respond with ONLY a JSON object of the form:
{{"actors": ["...", "..."], "themes": ["...", "..."]}}

Transcripts:
{transcripts}
"""


def episode_id(entry):
    guid = entry.get("id") or entry.get("link")
    digest = hashlib.sha1(guid.encode()).hexdigest()[:12]
    published = entry.get("published_parsed")
    date_prefix = time.strftime("%Y-%m-%d", published) if published else "0000-00-00"
    return f"{date_prefix}_{digest}"


def fetch_transcript(audio_url):
    url = (
        "https://api.deepgram.com/v1/listen"
        "?model=nova-3&language=en&punctuate=true&smart_format=true"
        "&diarize=true&utterances=true"
    )
    req = Request(
        url,
        data=json.dumps({"url": audio_url}).encode(),
        headers={
            "Authorization": f"Token {DEEPGRAM_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=1200) as resp:
        payload = json.loads(resp.read())

    utterances = payload["results"]["utterances"]
    return "\n".join(f"Speaker {u['speaker']}: {u['transcript']}" for u in utterances)


def ask_claude(prompt, timeout=120):
    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(
            {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode(),
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())

    text = payload["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    return json.loads(text)


def analyse_transcript(transcript):
    return ask_claude(ANALYSIS_PROMPT.format(transcript=transcript))


def analyse_batch(entries):
    transcripts = "\n\n".join(
        f"### {entry['podcast']} — {entry['title']}\n{entry['transcript']}"
        for entry in entries
    )
    return ask_claude(BATCH_ANALYSIS_PROMPT.format(transcripts=transcripts), timeout=180)


def build_feed_xml(podcast, archive_dir):
    records = []
    for filename in sorted(os.listdir(archive_dir), reverse=True):
        with open(os.path.join(archive_dir, filename)) as f:
            records.append(json.load(f))

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = podcast["display_name"]
    ET.SubElement(channel, "link").text = podcast["rss_url"]
    ET.SubElement(channel, "description").text = (
        f"Auto-generated feed for {podcast['display_name']} with AI-extracted "
        "actors and themes."
    )

    for record in records:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = record["title"]
        ET.SubElement(item, "link").text = record["link"]
        ET.SubElement(item, "guid").text = record["guid"]
        ET.SubElement(item, "pubDate").text = record["published"]
        ET.SubElement(item, "enclosure", url=record["audio_url"], type="audio/mpeg")

        description = (
            f"<p><strong>Key Actors:</strong> {', '.join(record['actors'])}</p>"
            f"<p><strong>Key Themes:</strong> {', '.join(record['themes'])}</p>"
        )
        ET.SubElement(item, "description").text = description

    return ET.tostring(rss, encoding="unicode")


def build_digest_feed_xml(archive_dir):
    records = []
    for filename in sorted(os.listdir(archive_dir), reverse=True):
        with open(os.path.join(archive_dir, filename)) as f:
            records.append(json.load(f))

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = DIGEST_DISPLAY_NAME
    ET.SubElement(channel, "description").text = (
        "Combined actors and themes across every episode processed in each monitor run."
    )

    for record in records:
        item = ET.SubElement(channel, "item")
        episode_list = "".join(
            f"<li>{e['podcast']}: {e['title']}</li>" for e in record["episodes"]
        )
        ET.SubElement(item, "title").text = (
            f"{len(record['episodes'])} episode(s) — {record['run_at']}"
        )
        ET.SubElement(item, "guid").text = record["run_at"]
        ET.SubElement(item, "pubDate").text = record["run_at"]

        description = (
            f"<p><strong>Key Actors:</strong> {', '.join(record['actors'])}</p>"
            f"<p><strong>Key Themes:</strong> {', '.join(record['themes'])}</p>"
            f"<p><strong>Episodes in this run:</strong></p><ul>{episode_list}</ul>"
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
        if published:
            published_dt = datetime.fromtimestamp(
                calendar.timegm(published), tz=timezone.utc
            )
            if datetime.now(timezone.utc) - published_dt > MAX_EPISODE_AGE:
                continue

        print(f"[{name}] new episode: {entry.title}")
        audio_url = entry.enclosures[0].href
        transcript = fetch_transcript(audio_url)
        analysis = analyse_transcript(transcript)

        record = {
            "title": entry.title,
            "guid": entry.get("id") or entry.get("link"),
            "link": entry.link,
            "published": entry.get("published", ""),
            "audio_url": audio_url,
            "transcript": transcript,
            "actors": analysis["actors"],
            "themes": analysis["themes"],
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

    if new_count:
        feed_xml = build_feed_xml(podcast, archive_dir)
        with open(os.path.join(podcast_dir, "feed.xml"), "w") as f:
            f.write(feed_xml)

    return new_count, new_entries


def git_commit_and_push(total_new):
    subprocess.run(["git", "add", "data"], cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"Add {total_new} new episode(s)"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)


def main():
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
