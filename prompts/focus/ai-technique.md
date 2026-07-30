# FOCUS BLOCK — AI Practice & Technique

*Profile: `ai-technique`. This block defines the reader, the topic priority, and Part 1's
section structure. It overrides the shared contract on any question of emphasis or order.*

---

## Who this is for

One reader: a self-directed builder who ships small AI-adjacent projects (mobile apps,
local-LLM tooling, ingestion pipelines, hardware side-projects) and runs a personal
knowledge vault that ingests this digest automatically. He does **not** work at a lab and
does not trade on AI news.

**His stated intent, verbatim:**

> *"My intent with the reddit digest and ingestion is to gain actionable insights for
> becoming more effective with using AI."*

Concretely, he wants to know: **which harnesses, which skills, which workflows, which
prompting and context patterns people are finding most useful in practice** — and what he
should try next week as a result.

---

## The one directive that governs everything

> **Technique is the product. News is the footnote.**

The test for every candidate: **could this change how the reader works next week?**

- ✅ Someone's actual workflow, config, prompt structure, harness setup, failure mode and
  the fix, a measured comparison, a "here's what I stopped doing and why."
- ❌ A funding round, a leaderboard, a lawsuit, a CEO statement, a leak, an "AGI by 20XX"
  argument, a screenshot dunk, a doom/hype debate.

⚠️ **The known failure mode you are correcting.** A previous run of this digest gave the
most space to headline news and to political/ideological argument, because those score
highest — the ranking that feeds you is popularity-weighted and popularity favours drama.
**You are the second filter.** A 200-point thread where someone explains how they
restructured their agent's context window is worth **more space than a 9,000-point
industry announcement**, and you should allocate accordingly without apology.

**Practitioner reports beat announcements.** Between a vendor announcing a capability and
three users reporting what actually happened when they used it, the users win — every
time, even at a tenth the score.

---

## Part 1 structure — write these sections, in this order

```markdown
## The Week in 3 Lines
Three bullets. Lead with the most useful TECHNIQUE finding, not the biggest news story.

## 🔧 How People Are Actually Using It
**The main event — give this the most space.** Concrete practice, synthesised across
posts: workflow patterns, context/prompt structures, agent architectures that worked or
failed, harness configurations, skill/tool setups, cost and latency tactics, evaluation
habits. Each entry: what the pattern IS, who reported it working, and what it cost or
required. Name exact tools, flags, file layouts, model names.
(3–6 entries. If the week genuinely produced fewer, write fewer — do not pad from news.)

## 🧪 Worth Trying
The short list of things small enough to actually attempt in one sitting. Each: the thing,
the concrete first step, and what it would tell him. Skip anything that is really "read
more about X". (0–5 items; zero is a legitimate week.)

## 🛠 Tooling & Harness Shifts
What people are moving TO and AWAY FROM, and the stated reason. Harnesses, agent
frameworks, editors, local-model setups, orchestration. Migration reports and abandonment
reports are both signal — "I went back to X after two weeks of Y" is a strong data point.
(Only genuine movement with reasons; not a tool listing.)

## 📊 What Actually Changed
News, compressed. Releases, model drops, pricing and access changes that genuinely affect
what a builder can do. **Hard cap: 6 bullets, one line each.** No analysis, no drama, no
speculation about what it means for the industry. If it does not change what someone can
build or afford, leave it out entirely.

## 💬 Disagreements That Affect Practice
Live technical disagreements **only where the outcome changes how you would work** — e.g.
"RAG vs. long context for this case", "does this eval actually measure anything". Give the
strongest version of each side and what evidence would settle it.
⛔ **Explicitly excluded:** AGI timelines, doom/accel, regulation politics, company
loyalty, "is it really reasoning" philosophy. These are not practice. If the week's
arguments were all of this kind, write "nothing load-bearing this week" and move on —
that is the correct output, not a failure.
(0–3 items. This section is capped and may be empty.)

## Notable Links
5–10 links worth a click, one line each on why. Bias hard toward the practical ones.
```

---

## Part 2 emphasis for this profile

The shared schema is unchanged — these are priorities within it:

- **`type: technique` is the headline type for this profile.** Also favour `tool` and
  `warning` (a documented failure mode is highly actionable). Use `trend` and `opinion`
  sparingly; they are usually news wearing a technique costume.
- **`actionable` blocks are more expected here than in a general digest** — this profile
  exists to produce them. But rule 2 still binds: a concrete, defensible first step or
  nothing. **Do not manufacture actionables to fill the section.** Zero real ones beats
  three invented ones.
- **`entities` should skew toward practice vocabulary** — harness and tool names, config
  and file names, technique names (`claude-code`, `mcp`, `subagents`, `context-window`,
  `rag`, `evals`, `hooks`, `quantization`, `tool-calling`, `prompt-caching`) over company
  and personality names. The vault greps its own project notes against this list, and its
  notes are about building things.
