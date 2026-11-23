# Podcast monitor — Cloud Run Job

`monitor.py` polls the podcast RSS feeds in `config.py`, transcribes and analyses new
episodes with Gemini, and commits the results to `data/` in this repo (which triggers
the GitHub Pages build via `.github/workflows/deploy.yml`). It's designed to run as a
**Cloud Run Job** on a **Cloud Scheduler** cron trigger — a real container, not a
long-running server, so it just runs to completion and exits each time it fires.

Because the container has no persistent disk, the job `git clone`s this repo fresh on
every run to figure out which episodes it's already processed, then commits and pushes
back to it at the end. There's no other state to manage.

## What you need before deploying

- A Google Cloud project with billing enabled, and the `gcloud` CLI (`gcloud init`) —
  or use [Cloud Shell](https://shell.cloud.google.com) in the browser, which has `gcloud`
  preinstalled and needs no local setup.
- A **Gemini API key**: [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
- A **GitHub token**: a fine-grained personal access token scoped to just this repo,
  with "Contents: Read and write" permission. (Classic PATs with `repo` scope also work,
  but a fine-grained token scoped to one repo is safer.)

Never commit either of these to the repo — they're stored in Secret Manager (below) and
injected into the container at runtime.

## One-time setup

```bash
gcloud config set project podcast-monitor-515309

gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
```

### Store secrets



```bash
(
set -e
P=podcast-monitor-515309; R=australia-southeast1; J=podcast-monitor; F=scripts/.env
getenv() { awk -v k="$1" '{sub(/\r$/,"");sub(/^export[ \t]+/,"")} index($0,k"=")==1{v=substr($0,length(k)+2);gsub(/^["'\'']|["'\'']$/,"",v);printf "%s",v;exit}' "$F"; }
gem_key=$(getenv GEMINI_API_KEY); gh_tok=$(getenv GITHUB_TOKEN); gh_owner=$(getenv GITHUB_OWNER); gh_repo=$(getenv GITHUB_REPO)
[ -n "$gem_key" ] && [ -n "$gh_tok" ] && [ -n "$gh_owner" ] && [ -n "$gh_repo" ] || { echo "missing value in $F" >&2; exit 1; }
put() { if gcloud secrets describe "$1" --project="$P" >/dev/null 2>&1; then printf '%s' "$2" | gcloud secrets versions add "$1" --project="$P" --data-file=-; else printf '%s' "$2" | gcloud secrets create "$1" --project="$P" --data-file=-; fi; }
put gemini-api-key "$gem_key"
put github-token "$gh_tok"
sa=$(gcloud run jobs describe "$J" --project="$P" --region="$R" --format='value(spec.template.spec.template.spec.serviceAccountName)')
[ -n "$sa" ] || sa="$(gcloud projects describe "$P" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for s in gemini-api-key github-token; do gcloud secrets add-iam-policy-binding "$s" --project="$P" --member="serviceAccount:$sa" --role=roles/secretmanager.secretAccessor >/dev/null; done
gcloud run jobs update "$J" --project="$P" --region="$R" --update-env-vars="GITHUB_OWNER=$gh_owner,GITHUB_REPO=$gh_repo" --update-secrets="GEMINI_API_KEY=gemini-api-key:latest,GITHUB_TOKEN=github-token:latest"
echo "Done. Job runs as: $sa"
)
```

## Deploy the job

Run from the repo root — `--source scripts` builds the container straight from
`scripts/Dockerfile` via Cloud Build, no manual image build/push needed:

```bash
gcloud run jobs deploy podcast-monitor \
  --source scripts \
  --region australia-southeast1 \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest,GITHUB_TOKEN=github-token:latest \
  --set-env-vars GITHUB_OWNER=YOUR_GH_USERNAME,GITHUB_REPO=podcast_monitor,GITHUB_BRANCH=main \
  --max-retries 1 \
  --task-timeout 900
```

Re-run the same command whenever you change `scripts/monitor.py` or `scripts/config.py`
— it rebuilds the image and updates the job in place.

### Environment variables the script reads

| Variable | Required | Notes |
|---|---|---|
| `GEMINI_API_KEY` | yes | from Secret Manager, set via `--set-secrets` above |
| `GITHUB_TOKEN` | yes | from Secret Manager, set via `--set-secrets` above |
| `GITHUB_OWNER` | yes | e.g. your GitHub username |
| `GITHUB_REPO` | yes | e.g. `podcast_monitor` |
| `GITHUB_BRANCH` | no | defaults to `main` |
| `GIT_AUTHOR_NAME` | no | defaults to `podcast-monitor-bot` |
| `GIT_AUTHOR_EMAIL` | no | defaults to `podcast-monitor-bot@users.noreply.github.com` |
| `REPO_DIR` | no | defaults to `/tmp/podcast_monitor_repo`, the clone's working directory |

## Test it manually before scheduling anything

```bash
gcloud run jobs execute podcast-monitor --region australia-southeast1 --wait
```

Check what happened:

```bash
gcloud run jobs executions list --job=podcast-monitor --region=australia-southeast1
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="podcast-monitor"' \
  --limit=50 --order=asc
```

(Or Cloud Console → Cloud Run → Jobs → `podcast-monitor` → Logs.) Then confirm a new
commit landed in GitHub under `data/`, and that the "Deploy site" Actions workflow ran.

## Schedule it

```bash
gcloud iam service-accounts create podcast-scheduler \
  --display-name="Podcast monitor scheduler"

SA="podcast-scheduler@podcast-monitor-515309.iam.gserviceaccount.com"

gcloud run jobs add-iam-policy-binding podcast-monitor \
  --region=australia-southeast1 \
  --member="serviceAccount:${SA}" \
  --role="roles/run.invoker"

JOB_URI="https://run.googleapis.com/v2/projects/podcast-monitor-515309/locations/australia-southeast1/jobs/podcast-monitor:run"

gcloud scheduler jobs create http podcast-monitor-0800 \
  --location=australia-southeast1 \
  --schedule="0 8 * * 1-5" \
  --time-zone="Australia/Melbourne" \
  --uri="${JOB_URI}" \
  --http-method=POST \
  --oauth-service-account-email="${SA}"

gcloud scheduler jobs create http podcast-monitor-0930-1730 \
  --location=australia-southeast1 \
  --schedule="30 9,17 * * 1-5" \
  --time-zone="Australia/Melbourne" \
  --uri="${JOB_URI}" \
  --http-method=POST \
  --oauth-service-account-email="${SA}"
```

## Local testing (optional)

```bash
cd scripts
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python monitor.py
```

Before running `python monitor.py`, edit `.env` and fill in `GEMINI_API_KEY`,
`GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO` with real values — don't put the `cp`
command and a trailing `#` comment on the same line if you're on zsh (the default
macOS shell): zsh doesn't treat `#` as a comment in interactive mode unless
`setopt interactive_comments` is set, so anything after it gets parsed as extra
arguments to the command instead of being ignored.

This clones the real repo into `REPO_DIR` (default `/tmp/podcast_monitor_repo`) and will
push to it if it finds new episodes — point `GITHUB_REPO`/`GITHUB_BRANCH` at a fork or
test branch first if you don't want a local test run landing commits on `main`.

## Troubleshooting

- **`403`/permission errors reading secrets**: the service account in step "Let the job
  read those secrets" doesn't match the one the job actually runs as. Check with
  `gcloud run jobs describe podcast-monitor --region=... --format='value(spec.template.spec.template.spec.serviceAccountName)'`
  — if blank, it's the default compute SA computed above.
- **Push fails with an auth error**: the GitHub token has expired or doesn't have
  Contents: Read and write on the repo. Rotate it with `gcloud secrets versions add`
  (above) — no redeploy needed, the job reads `:latest` on its next run.
- **Scheduler fires but nothing happens**: check
  `gcloud scheduler jobs describe podcast-monitor-trigger --location=...` for the last
  attempt's status, and confirm the `roles/run.invoker` binding above is in place.
- **Cost check**: Cloud Run Jobs' free tier (240,000 vCPU-seconds/month) comfortably
  covers a few short runs a day; Gemini usage is the real cost driver — check
  [Cloud Billing](https://console.cloud.google.com/billing) if it seems off from the
  ~$0.02-0.03/episode estimate (Flash-Lite tier).
