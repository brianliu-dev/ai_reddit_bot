# ai_reddit_bot

Weekly digest bot. Pulls top + latest posts from AI-agent / LLM subreddits, distills them
into the most prominent ideas and trends, and delivers a readable summary.

**Status:** Phase 1–4 scaffolded — full pipeline built; ingestion runs standalone,
distillation needs [my_intern](../my_intern) up.

**Stack:** PRAW (official Reddit API) · distillation via `my_intern` router ·
output to Obsidian note + Telegram push · weekly cron · Docker/WSL2.

**Subreddits:** r/LocalLLaMA, r/MachineLearning, r/AI_Agents, r/LangChain, r/OpenAI,
r/ClaudeAI, r/singularity. (Curate over time — edit [`config/subreddits.yaml`](config/subreddits.yaml).)

See [REQUIREMENTS.md](REQUIREMENTS.md) for the full spec and phased plan.

---

## Pipeline

```
ingest.py   PRAW: top(week) + new per sub, +top comments, min-score filter
   │
   ▼
storage.py  raw JSON → data/raw/YYYY-MM-DD.json  (retained for trend analysis)
   │
   ▼
distill.py  → my_intern router (/v1/chat/completions) → themed markdown digest
   │
   ▼
deliver.py  Obsidian note (dated) + Telegram push
```

Orchestrated by [`src/main.py`](src/main.py). Ingestion (`ingest.py` + `storage.py`)
has **no dependency on my_intern** — you can build and test it before the router exists;
only `distill.py` needs the router live.

## Setup & run

1. **Reddit app:** create a *script* app at <https://www.reddit.com/prefs/apps> →
   client id + secret.
2. **Configure:**
   ```bash
   cp .env.example .env     # Reddit creds, MI_BASE_URL/MI_API_KEY, Obsidian path, Telegram
   ```
3. **Test ingestion alone** (no router needed) — pull and inspect the raw JSON:
   ```bash
   docker compose build
   docker compose run --rm -e OBSIDIAN_VAULT_DIR= bot \
     python -c "from src import ingest, storage; print(storage.save_raw(ingest.pull()))"
   ```
4. **Full run** (needs my_intern up):
   ```bash
   docker compose run --rm bot
   ```

## Weekly schedule (WSL2 cron)

```cron
# Sunday 9am — adjust path to the repo
0 9 * * 0  cd /home/you/ai_reddit_bot && /usr/bin/docker compose run --rm bot >> cron.log 2>&1
```

## Notes / what's tested

Built on the Mac (dev box); first live run is on the server alongside my_intern. The
Reddit pull is standard PRAW and testable anywhere with API creds; the distillation
step is wired to the router's OpenAI-compatible ingress. Tune `config/subreddits.yaml`
(sub list, score threshold, comment depth) to taste.
