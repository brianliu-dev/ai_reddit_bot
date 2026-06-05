# ai_reddit_bot — Requirements & Execution Plan

## What it is
A weekly bot that pulls top + latest posts (with top comments) from AI-agent / LLM
subreddits, distills them into the most prominent ideas and trends, and delivers a
readable digest.

## Requirements
- **Ingestion:** PRAW (official Reddit API, OAuth script app) — *not* HTML scraping.
- **Subreddits:** r/LocalLLaMA, r/MachineLearning, r/AI_Agents, r/LangChain, r/OpenAI,
  r/ClaudeAI, r/singularity. (Curate over time.)
- **Pull:** top (past week) + new; min-score threshold + top-N per sub. **Top comments
  included** for richer signal.
- **Distillation:** via the [[my_intern]] router — cluster posts into themes → summarize
  prominent ideas/trends → notable links + "what's new this week".
- **Output:** dated **Obsidian markdown note** into the vault + **Telegram push** of the
  summary. (Email is a future add.)
- **Schedule:** weekly cron (e.g. Sunday AM), as a container in WSL2.
- **History:** retain raw pulls (JSON) for later trend-over-time analysis.

## Dependency
Needs [[my_intern]] running (it does the LLM distillation) — hence the ordering.

## Phased plan
- **Phase 1:** PRAW ingestion → raw JSON (posts + top comments) for the sub list.
- **Phase 2:** distillation via my_intern → themed markdown digest written to the vault.
- **Phase 3:** Telegram push of the summary.
- **Phase 4:** weekly schedule (cron/container) + raw history retention.
