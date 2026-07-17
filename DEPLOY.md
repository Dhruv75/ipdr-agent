# Deploying the IPDR Forensic Agent

The app is built to run **key-free** in `LOCAL` mode, so you can put a public,
zero-cost demo online in a few minutes. The recommended host is **Streamlit
Community Cloud** (free, first-party, integrates with GitHub).

> The synthetic dataset is generated automatically on first launch, so there is
> nothing to upload and no build step to configure.

---

## 0. Before you push anything: rotate old credentials

The original Colab notebook hard-coded a live **ngrok auth token** and an
**OpenAI key**. Treat both as compromised:

- ngrok dashboard -> *Your Authtoken* -> **Regenerate**.
- OpenAI dashboard -> *API keys* -> **Revoke** the old key.

This repo keeps all secrets out of source (`.env` and `.streamlit/secrets.toml`
are gitignored), so nothing sensitive should ever be committed. Double-check
with `git status` before your first push.

---

## 1. Push the repo to GitHub

```bash
git init
git add .
git commit -m "IPDR Forensic Agent v5.0"
git branch -M main
git remote add origin https://github.com/<your-username>/ipdr-forensic-agent.git
git push -u origin main
```

The dataset (`data/*.xlsx`, `data/*.csv`) is intentionally **not** committed - it
is regenerated on demand.

---

## 2. Deploy on Streamlit Community Cloud

1. Go to **https://share.streamlit.io** and sign in with GitHub.
2. Click **Create app -> Deploy a public app from a repo**.
3. Fill in:
   - **Repository:** `<your-username>/ipdr-forensic-agent`
   - **Branch:** `main`
   - **Main file path:** `app/streamlit_app.py`
4. Open **Advanced settings** and set **Python version** to `3.11`.
5. Click **Deploy**.

The first boot installs `requirements.txt` (light core - no torch, so the build
is fast and stays under the free tier's resource limit) and then generates the
~5,000-row dataset on first page load (about 10-20 seconds, shown by a spinner).
You get a public URL like `https://<app-name>.streamlit.app`.

That URL is what you put on your resume / share with interviewers.

---

## 3. (Optional) Enable the cloud path with API keys

The demo is fully functional without any keys. If you want live GPT-4o NL-to-SQL
and narration, add secrets **in the Streamlit dashboard** (never in the repo):

App -> **Settings -> Secrets**, then paste TOML:

```toml
OPENAI_API_KEY = "sk-..."
IPDR_MODE = "auto"
# Optional managed vector search:
QDRANT_URL = "https://<cluster>.qdrant.io"
QDRANT_API_KEY = "..."
```

> **Cost warning.** With `OPENAI_API_KEY` set, every question makes paid API
> calls. For a public link this can run up a bill or leak quota. For an
> always-on demo, prefer leaving keys unset (LOCAL mode) and switch to cloud
> mode only when demoing live. You will also need the cloud extras on that
> deployment - point the install at `requirements-cloud.txt` (rename it to
> `requirements.txt` for the Streamlit build, or add `-r requirements-cloud.txt`)
> because torch and the OpenAI/Qdrant clients are not in the light core.

---

## 4. Redeploying

Streamlit Cloud watches your repo: every `git push` to `main` redeploys
automatically. Free apps sleep after a period of inactivity and wake on the next
visit (a few seconds).

---

## Alternatives

- **Hugging Face Spaces.** Create a Space (Streamlit SDK), push the same repo.
  Same key-free behaviour; the runtime dataset generation works there too.
- **Docker anywhere (Render / Railway / Fly / a VPS).** The repo ships a
  `Dockerfile` and `docker-compose.yml` (app + Qdrant):
  ```bash
  docker compose up --build   # app on :8501
  ```
  Point the host at the Dockerfile. This is the most "production-shaped" option
  and the best story for a systems-design conversation, but free tiers sleep and
  have build-minute limits.
