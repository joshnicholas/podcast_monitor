# podcast_monitor

Monitors a list of podcast RSS feeds for new episodes, transcribes them with Deepgram,
has Claude extract key actors/themes, writes an enriched RSS feed per podcast, and
publishes those feeds via a SvelteKit site on GitHub Pages.

- `scripts/config.py` — the list of podcasts to monitor
- `scripts/monitor.py` — the pipeline, meant to run on a schedule (e.g. a Raspberry Pi via cron)
- `data/<name>/archive/*.json` — flat-file "database": one JSON record per episode
- `data/<name>/feed.xml` — the enriched RSS feed, regenerated after each new episode
- `site/` — SvelteKit static site that publishes the feeds, deployed by `.github/workflows/deploy.yml`

## Running the pipeline on a Raspberry Pi

### 1. Create a GitHub personal access token

This repo is private, so cloning and pushing from the Pi needs a credential. Go to
**GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**,
create one scoped to just this repo with **Contents: Read and write** permission, and
copy the token — you'll only see it once.

### 2. Clone the repo and set up a virtualenv

```bash
git clone https://YOUR_TOKEN_HERE@github.com/joshnicholas/podcast_monitor.git ~/podcast_monitor
cd ~/podcast_monitor
git remote set-url origin https://github.com/joshnicholas/podcast_monitor.git
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The `remote set-url` strips the token back out of the clone URL once it's done its job —
from here on, `monitor.py` reads the token from `.env` (next step) for every push, so it's
never stored anywhere else on disk.

### 3. Add your API keys and set a commit identity

Create `~/podcast_monitor/.env`:

```
DEEPGRAM_API_KEY=your_deepgram_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
GITHUB_TOKEN=your_github_token_here
```

Lock down the permissions:

```bash
chmod 600 ~/podcast_monitor/.env
```

Set a commit identity for the Pi:

```bash
cd ~/podcast_monitor
git config user.name "podcast-monitor-pi"
git config user.email "pi@localhost"
```

### 4. Test a manual run

```bash
cd ~/podcast_monitor
.venv/bin/python3 scripts/monitor.py
```

It should print either `No new episodes.` or `[<name>] new episode: ...` followed by a
successful `git push`.

### 5. Schedule it with cron, every 2 hours

```bash
crontab -e
```

Add this line (edit the paths if your username/home directory differs from `pi`/`/home/pi`):

```
0 */2 * * * /home/pi/podcast_monitor/.venv/bin/python3 /home/pi/podcast_monitor/scripts/monitor.py >> /home/pi/podcast_monitor/cron.log 2>&1
```

Full absolute paths are used deliberately — cron runs jobs with a minimal environment/PATH,
and this doesn't depend on cron's working directory. Every 2 hours the script wakes up,
checks each feed in `scripts/config.py` for new episodes, and if it finds any, transcribes,
analyzes, commits, and pushes them — which in turn triggers the GitHub Actions workflow
that rebuilds and redeploys the site.

### 6. One-time GitHub Pages setting

In the GitHub repo: **Settings → Pages → Source → GitHub Actions**. This only needs doing once.

## Adding more podcasts

Edit `scripts/config.py`:

```python
PODCASTS = [
    {
        "name": "o7",
        "display_name": "ABC News Top Stories",
        "rss_url": "https://www.abc.net.au/feeds/101858056/podcast.xml",
    },
    {
        "name": "another-slug",
        "display_name": "Another Podcast's Real Name",
        "rss_url": "https://example.com/feed.xml",
    },
]
```

`name` is used for the folder under `data/` and the published feed filename — keep it
short and filesystem-safe. `display_name` is what shows up in the RSS `<title>` and on
the site's homepage.

## Running the site locally

```bash
cd site
npm i
npm run dev
```

`npm run build` (also run by the GitHub Actions workflow) copies every `data/<name>/feed.xml`
into `site/static/feeds/<name>.xml` before building, so the site always reflects whatever is
currently in `data/`.
