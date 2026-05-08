# Capacity Guardian

**An autonomous comms agent that triages client builds when you have more demand than capacity.**

## The problem

A small AI dev agency has more client build requests than it can take. Inbound mail piles up: cold leads pitching, active clients asking for status, scope changes, blockers, the occasional "we'd love to chat about AI possibilities" with no problem statement. Every email demands a judgment call: is this worth a 30-minute call? Are we ghosting that client who emailed yesterday? Should this go on the waitlist or be politely declined?

When demand outruns capacity, the cost of mistriage isn't a missed reply. It's accidental over-commitment, dropped active clients, or burned high-intent leads. **Capacity Guardian** reads each inbound email, decides what kind of message it is, drafts the right reply in the agency's voice, and updates the team's capacity tracker. Drafts go to Gmail Drafts; nothing is auto-sent. High-priority items (urgent leads, scope changes, blockers) ping Slack.

## Architecture

```
                        ┌─────────────────────┐
   Gmail unread  ──────▶│  Router (Opus 4.7)  │  classify into 4 tracks
                        └──────────┬──────────┘
                                   │
            ┌──────────────────────┼──────────────────────┬──────────┐
            │                      │                      │          │
       track=lead             track=client          track=internal  ignore
            │                      │                      │          │
            ▼                      ▼                      ▼          ▼
   ┌────────────────┐    ┌──────────────────┐         (skip)    (mark seen)
   │  Lead handler  │    │  Client handler  │
   │  (Sonnet 4.6)  │    │  (Sonnet 4.6)    │
   │                │    │                  │
   │ pick 1 of 5    │    │ sub-classify     │
   │ policies       │    │ into 6 buckets   │
   └───────┬────────┘    └────────┬─────────┘
           │                      │
           └──────┬───────────────┴───────┐
                  │                       │
                  ▼                       ▼
        Gmail Drafts (threaded)   SQLite memory.db
        Google Sheet (Capacity)   (clients, threads,
        Slack #alerts             drafts, voice samples)
```

**The voice loop.** Edit a draft once and the next reply for that client matches your tone. Voice samples are stored per-client and few-shot-prompted into every subsequent draft.

## The 5 policies (Lead handler)

The 5 policies are the agent's decision tree for inbound leads — what makes Capacity Guardian a triage system, not a draft-generator. The handler must select exactly one:

1. **High-value urgent.** Clear scope, AI-native problem, ≥$20k inferred budget, real timeline pressure. All four required. → Ask 2-3 sharp qualifying questions; signal we can prioritize. **Slack alert.**
2. **Standard build.** Fits the wheelhouse, normal urgency, decent budget signals. → Real capacity check, honest wait time, offer waitlist. **Adds row to the Capacity sheet.**
3. **Low fit.** The kind of work they want is outside what we do (mobile apps, marketing/web sites, non-AI engineering) or budget is tiny. → Polite decline, suggest a referral if we can.
4. **Needs more info.** They have not described a concrete AI/automation problem with enough detail to scope. "Explore AI", "open to ideas", no use case attached. → Specific clarifying questions: industry, problem, timeline, stack, budget.
5. **Already in queue.** Sender's company already appears on the Capacity sheet. → Surface their position and ETA. Do not re-scope.

The Client handler sub-classifies into `status_request | scope_change | blocker | approval | smalltalk | other`. `scope_change` and `blocker` trigger Slack alerts. Every reply matches the user's voice samples for that client.

## Engineering notes

- **Em-dash trajectory: 5/5 → 0/N.** Claude's well-known em-dash habit collided with the agency's "no em-dashes" voice rule. The diagnosis was an instrumented observation (per-policy violation logger), then a prompt comparison: `lead.md` line 5 demonstrated an em-dash *inside* the rule forbidding them. Three-step fix: rewrite the line, reinforce the rule at the schema-description level, and a deterministic Python post-process as belt-and-suspenders. Each step measured against the eval suite.

- **The clients/capacity no-man's-land bug.** The Router consults the SQLite `clients` table; the capacity sheet is a separate store. A waitlisted prospect lived in one but not the other, so follow-ups got `confidence=0.50 → ignore` and stalled forever. Fixed by having the Lead handler seed `clients` on *every* non-decline policy (1, 2, 4, 5), not just policy-2 capacity adds. Caught while testing policy 5 live.

- **Pre-fetched context vs multi-tool ReAct loop.** Capacity rows, voice samples, and thread history are fetched Python-side before the LLM call, then formatted into the user message. Deterministic data goes through deterministic code; the LLM's job is judgment, not lookups. Cheaper, faster, and failure modes are obvious because they're synchronous.

- **Dry-run via context manager, not parameter threading.** A single `agent/dry_run.py` mocks every side-effecting call at the I/O boundary. The orchestrator's `--dry-run` flag and the eval harness use the same context manager. Evals exercise the *production* code path, not a parallel test path.

- **Voice violations as observation, not enforcement.** The handler logs every rule violation but doesn't reject drafts. That gave me a per-policy violation map and pointed straight at `lead.md` as the source. Diagnosis from data, not theory.

- **Hallucination guard on the Router.** If the model emits `track=client` with a `matched_client_id` that's not in the Python-side match set, the Router downgrades to `lead` with a log warning. Catches the case where Claude wants to fuzzy-match by company name without the grounding signal we actually require — the Python matcher is the source of truth.

## Evals

10 fixtures across all four router tracks plus the 5 lead policies and 3 client subclasses. Run with `python evals/run_evals.py`.

| Run | Result | Em-dash leakage* |
|---|---|---|
| Initial | 9/10 | 5/5 (endemic, pre-eval) |
| After prompt fix | 10/10 | 1/10 (drift) |
| After Python normalizer | 10/10 | 0/N (post-process strips remaining drift) |

*Denominators differ by phase: the "5/5" is from earlier policy-1 drafts produced during step-by-step development; the "1/10" and "0/N" are from full eval runs (10 fixtures each) plus subsequent live traffic.

Run 1 → run 2 was tightened by sharpening the policy-3 vs policy-4 boundary in `lead.md`: a `marketing agency` lead had been misclassified as low-fit (policy 3) when it was actually a vague-needs-info case (policy 4). The model was reading "marketing" as a low-fit work-type signal; the prompt now distinguishes work type from customer industry.

The em-dash issue was diagnosed by comparing `lead.md` vs `client.md`, addressed by a prompt revision, and finally eliminated with a deterministic post-process. Drift is observable in logs (so prompt regressions stay detectable) but invisible to the user.

## Setup

```bash
git clone <this-repo> && cd capacity-guardian
pip install -r requirements.txt
cp .env.example .env  # then fill in keys, sheet id, webhook url
python -m agent.orchestrator --once
```

Required env vars (full list in `.env.example`):

- `PPQ_API_KEY` — ppq.ai key. The build calls Claude (Opus 4.7 router, Sonnet 4.6 handlers) through ppq.ai's OpenAI-compatible endpoint. ppq.ai was used for development cost; production deployment would use the Anthropic SDK directly for data residency. The wrapper in `agent/llm.py` is ~150 lines of `requests` and is straightforward to swap.
- `GMAIL_CREDENTIALS_PATH` / `GMAIL_TOKEN_PATH` — OAuth client (Desktop app) + saved refresh token. Run `python scripts/gmail_auth.py` once to mint the token.
- `GOOGLE_SERVICE_ACCOUNT_PATH` / `CAPACITY_SHEET_ID` — Sheets service account + sheet id. Share the sheet with the service-account email (Editor access). Run `python scripts/seed_capacity.py` to populate demo data.
- `SLACK_WEBHOOK_URL` — incoming webhook for high-value lead and client scope/blocker alerts.
- `AGENCY_INTERNAL_DOMAIN` — your team's email domain, for the `internal` track.

CLI flags:

- `--once` — single pass, exit. Use this for the demo and for smoke-testing.
- *(no flag)* — polling daemon. **Default mode.** Polls Gmail every 60s, processes unread mail, runs until killed. This is the autonomous mode.
- `--verbose` — DEBUG-level logging.
- `--dry-run` — exercise the full agent against the real inbox without producing side effects. Real LLM calls, real reads, all writes mocked. Used by the eval harness for the same reason: tests the production code path, not a separate test path.

## Demo

2-minute walkthrough on real email: *[link to be added once recorded]*. The demo highlights the voice-loop moment — edit a draft, send, watch the next reply for that client pick up your tone.

Run it yourself: `python -m agent.orchestrator --once` after setup.
