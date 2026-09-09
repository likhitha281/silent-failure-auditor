# Silent Failure Auditor

**Catches when a coding agent silently fails and reports success anyway.**

Coding agents narrate their own progress. Most of the time they're right —
but when a tool call fails, the agent doesn't always say so. A test suite
comes back red and the agent says "all tests passing." A patch applies zero
hunks and the agent says "fix applied." Nobody is watching that layer.
This is a rule-based auditor for it.

It's not another agent observability dashboard — tools like LangSmith or
Langfuse show you *what an agent did*. This specifically catches *when the
agent's own account of what it did doesn't match the evidence in its own log*.

```bash
pip install silent-failure-auditor
sfa audit my-session.jsonl
```

## The four things it catches

| Type | What it looks like | Severity |
|---|---|---|
| **Misread success** | A tool returns an error or failing tests, but the agent's next message claims success | High |
| **Hallucinated path** | The agent edits or references a file that never resolves — the tool call itself reports "no such file" | High |
| **No-op edit** | A patch/edit tool reports zero changes applied, but the agent describes the fix as made | Medium |
| **Retry loop** | The same failing call repeats three or more times with no change in approach, then the agent moves on as if it worked | Medium |

## Works with more than one tool

A format-detection layer normalizes different agent logs into one common
step sequence, so the same detector runs regardless of source:

| Format | Source |
|---|---|
| `codex_rollout` | Codex CLI's `~/.codex/sessions/**/rollout-*.jsonl` |
| `anthropic_openai_transcript` | Any Messages-API-style transcript — this also covers **Claude Code**, which is built on the same API |
| `generic_events` | A plain `[{tool, args, output, textAfter}, ...]` array, for wiring up anything else |
| `aider_markdown` | Best-effort parse of Aider's `.aider.chat.history.md` (Aider has no official structured tool-call schema, so treat this adapter as lower-confidence than the others) |

Adding a new tool means writing one adapter function — the detection rules
and the CLI never need to change.

## Install

```bash
pip install silent-failure-auditor          # core CLI
pip install silent-failure-auditor[llm]     # + optional LLM confirmation pass
```

Or from source:
```bash
git clone https://github.com/likhitha281/silent-failure-auditor
cd silent-failure-auditor
pip install -e ".[llm,dev]"
```

## CLI

```bash
# Audit a saved log
sfa audit examples/sample_rollout.jsonl

# Only show what got flagged
sfa audit examples/sample_rollout.jsonl --flagged-only

# Write the full per-step report to disk
sfa audit examples/sample_rollout.jsonl --output report.json

# Tail a LIVE Codex CLI session and flag failures as they happen
sfa watch

# Try live mode right now, no Codex install needed
sfa watch --demo

# Second opinion from an LLM on just the rule-flagged steps
export ANTHROPIC_API_KEY=...
sfa explain examples/sample_rollout.jsonl
```

Sample output:
```
Silent Failure Auditor -- format detected: codex_rollout
------------------------------------------------------------
Steps: 8   Flagged: 4 (high: 2, medium: 2)   Clean: 4

    2  [misread/high]      run_tests    {"command": "pytest tests/test_validator..."}
       -> The tool output indicates a failure ("failed"), but the following
          message claims success and does not reference the failure.
```

## Use it in CI

Drop this into any workflow to fail a build when an agent's session shows a
high-severity silent failure — for example, gating a PR an agent opened
against your repo:

```yaml
- uses: likhitha281/silent-failure-auditor@main
  with:
    log-path: agent-session.jsonl
    fail-on: high   # "high", "medium", or "none"
```

The full report is uploaded as a build artifact either way, so you can
inspect medium-severity flags even when the build passes.

## Dashboard

A companion single-file HTML dashboard visualizes a flagged run interactively
— upload a log or paste JSON directly in the browser, no server required.
Open `dashboard.html` and try the "Upload log" panel with `examples/sample_rollout.jsonl`.

## Project structure

```
.
├── silent_failure_auditor/   # the installable package
│   ├── detectors.py           # the four rule-based signatures
│   ├── adapters.py            # format detection: codex / transcript / generic / aider
│   └── cli.py                 # sfa audit / watch / explain
├── action.yml                 # GitHub Action wrapper around the CLI
├── scripts/check_severity.py  # severity gate used by the Action
├── dashboard.html              # browser-based visual auditor
├── examples/
│   ├── sample_rollout.jsonl    # used to DESIGN the rules -- not for evaluation
│   ├── sample_generic.json
│   └── eval_set/                # held-out set, used to MEASURE the rules
│       ├── labels.json
│       └── *.json
├── tests/
│   ├── test_detectors.py      # course-baseline tests
│   └── test_package.py        # adapter + CLI tests
├── run_baseline.py            # standalone script (course submission entry point)
├── evaluate.py                 # precision/recall against examples/eval_set
├── watch_live.py               # standalone script, same logic as `sfa watch`
├── llm_explain.py               # standalone script, same logic as `sfa explain`
└── pyproject.toml
```

The standalone scripts (`run_baseline.py`, `watch_live.py`, `llm_explain.py`)
are kept for course reproducibility instructions and work with zero install
beyond Python itself. The `silent_failure_auditor` package and `sfa` CLI are
the same logic, packaged properly for real use.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

11 tests cover the four failure signatures, all four format adapters, and
edge cases (an agent correctly acknowledging an error is *not* flagged; two
repeats don't trigger the retry rule, three do). CI runs these on every push.

## Evaluation

```bash
python evaluate.py --logs-dir examples/eval_set --labels examples/eval_set/labels.json
```

**Precision: 0.50 · Recall: 0.50 · F1: 0.50** — measured against a 4-log
held-out set (`examples/eval_set/`), hand-labeled independently of the logs
used to design the rules (`examples/sample_rollout.jsonl` was used *while
building* the detector, so evaluating against it again would be circular —
this set was written afterward, specifically to include cases the rules
get wrong).

What that 0.50/0.50 is made of, concretely:
- **True positive**: a hallucinated-path case in a different context
  (`ghost_file.json`) — correctly caught.
- **False negative** (`vocab_miss.json`): a real test failure where the
  agent's follow-up ("Looks good, moving on") doesn't contain any of the
  exact success words the regex checks for — missed.
- **False positive** (`transient_recovery.json`): a command that failed,
  retried, and *actually succeeded* within one tool output — the regex
  sees "failed" + "completed successfully" and can't tell that's a
  legitimate recovery, not a silent failure.

That false positive in particular points at the rule engine's real
weakness: it judges each tool call as a single unit and can't distinguish
"failed then recovered in the same call" from "failed and the agent didn't
notice." A semantic pass (see `sfa explain` / `llm_explain.py`) is the
planned fix.

To grow this set: add a new log + its ground truth to `examples/eval_set/labels.json`
whenever you run the auditor against a real session, so the number keeps
being measured against fresh cases rather than the same four forever.

## Limitations

- Detection depends on the agent's follow-up text containing fairly specific
  success vocabulary (e.g. "fixed", "passing", "done"). A vaguer claim like
  "that should do it" can slip through undetected — this came up twice while
  building the test suite itself.
- Rules are regex-based and not semantic; phrasing changes can defeat them.
  `sfa explain` (LLM confirmation pass) is the mitigation in progress.
- The Aider adapter is best-effort — there's no official structured schema
  to parse against.
- `sfa watch` polls the session file every couple of seconds rather than
  true event streaming, since Codex CLI has no live socket/webhook.
- No cross-session pattern learning; each run is audited independently.

## License

MIT — see [LICENSE](LICENSE).