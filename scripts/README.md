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
gcloud config set project YOUR_PROJECT_ID

gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
```

### Store secrets

```bash
printf '%s' 'YOUR_GEMINI_KEY' | gcloud secrets create gemini-api-key --data-file=-
printf '%s' 'YOUR_GITHUB_PAT' | gcloud secrets create github-token   --data-file=-
```

To rotate a secret later (e.g. the GitHub token expired or the Gemini key leaked),
add a new version rather than recreating the secret — the job always reads `:latest`:

```bash
printf '%s' 'NEW_VALUE' | gcloud secrets versions add gemini-api-key --data-file=-
```

### Let the job read those secrets

Cloud Run Jobs use the default compute service account unless you specify another one:

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for SECRET in gemini-api-key github-token; do
  gcloud secrets add-iam-policy-binding "$SECRET" \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor"
done
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
# Let Cloud Scheduler invoke the job
gcloud run jobs add-iam-policy-binding podcast-monitor \
  --region=australia-southeast1 \
  --member="serviceAccount:${SA}" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http podcast-monitor-trigger \
  --location=australia-southeast1 \
  --schedule="*/30 * * * *" \
  --uri="https://run.googleapis.com/v2/projects/YOUR_PROJECT_ID/locations/australia-southeast1/jobs/podcast-monitor:run" \
  --http-method=POST \
  --oauth-service-account-email="${SA}"
```

Adjust `--schedule` (standard unix-cron) to whatever cadence you want — the example
above runs every 30 minutes. To change the schedule later:

```bash
gcloud scheduler jobs update http podcast-monitor-trigger \
  --location=australia-southeast1 \
  --schedule="NEW_CRON_STRING"
```

## Local testing (optional)

```bash
cd scripts
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GEMINI_API_KEY, GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO
python monitor.py
```

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
