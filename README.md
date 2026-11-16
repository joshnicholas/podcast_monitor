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

### 1. Clone the repo and set up a virtualenv

```bash
git clone https://github.com/joshnicholas/podcast_monitor.git ~/podcast_monitor
cd ~/podcast_monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Add your API keys

Create `~/podcast_monitor/.env`:

```
DEEPGRAM_API_KEY=your_deepgram_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
```

Lock down the permissions:

```bash
chmod 600 ~/podcast_monitor/.env
```

### 3. Give the Pi push access to GitHub

Generate a dedicated SSH key on the Pi:

```bash
ssh-keygen -t ed25519 -C "podcast-monitor-pi" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Copy that output, then on GitHub go to **Settings → Deploy keys → Add deploy key** on
this repo, paste the key, and check **"Allow write access"**.

Back on the Pi, point the repo at the SSH remote and set a commit identity:

```bash
cd ~/podcast_monitor
git remote set-url origin git@github.com:joshnicholas/podcast_monitor.git
git config user.name "podcast-monitor-pi"
git config user.email "pi@localhost"
ssh -T git@github.com
```

The last command should greet you by the deploy key's name — that confirms push access works.

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
