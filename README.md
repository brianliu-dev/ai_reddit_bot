# ai_reddit_bot

Weekly digest bot. Pulls top + latest posts from AI-agent / LLM subreddits, distills them
into the most prominent ideas and trends, and delivers a readable summary.

**Status:** Planning (starts after [my_intern](../my_intern) is usable — it does the distillation)

**Stack:** PRAW (official Reddit API) · distillation via `my_intern` router ·
output to Obsidian note + Telegram push · weekly cron · Docker/WSL2.

**Subreddits:** r/LocalLLaMA, r/MachineLearning, r/AI_Agents, r/LangChain, r/OpenAI,
r/ClaudeAI, r/singularity. (Curate over time.)

See [REQUIREMENTS.md](REQUIREMENTS.md) for the full spec and phased plan.
