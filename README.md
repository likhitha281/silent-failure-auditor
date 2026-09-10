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

> **No Codex CLI installation is required to reproduce these results.**
> The baseline only *reads* a log file — a real Codex CLI export is one
> supported format, but the bundled example
> (`examples/sample_rollout.jsonl`) is already in that format, so the full
> baseline runs with nothing beyond Python. Codex CLI itself is only
> needed if you want to generate a *new* real session to audit.

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
- uses: likhitha281/silent-failure-auditor@v0.1.0   # pin to a release, not @main
  with:
    log-path: agent-session.jsonl
    fail-on: high   # "high", "medium", or "none"
```

Findings show up in three places, not just a JSON file you'd have to
download: a **step summary** in the workflow run UI (severity counts plus
one paragraph per finding), **inline `::error`/`::warning` annotations** on
the run itself, and the full per-step **`sfa-report` artifact** for anything
that needs the raw data. `fail-on` and `log-path` are validated up front —
an unrecognized severity or a missing log file fails fast with a clear
message instead of a confusing error three steps later.

**What this action needs access to:** nothing. No secrets, no write
permissions, no network calls beyond installing the package from PyPI. It
only reads the log file you point it at.

**Supply-chain note for maintainers:** the two third-party actions this
depends on (`setup-python`, `upload-artifact`) are pinned to full commit
SHAs rather than version tags, since a tag can be moved but a commit can't.
The `setup-python` pin was cross-checked against two independent sources;
the `upload-artifact` pin came from one dependabot-generated diff — worth
an independent `git ls-remote` check before bumping either.

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
├── action.yml                  # GitHub Action wrapper around the CLI
├── scripts/
│   ├── check_severity.py      # severity gate used by the Action
│   ├── write_summary.py        # writes findings to $GITHUB_STEP_SUMMARY
│   └── emit_annotations.py     # writes ::error/::warning workflow commands
├── .github/workflows/
│   ├── test.yml                # unit tests against the Python package
│   └── action-integration.yml  # invokes the packaged Action itself, end to end
├── dashboard.html              # browser-based visual auditor
├── examples/
│   ├── sample_rollout.jsonl    # used to DESIGN the rules -- not for evaluation
│   ├── sample_generic.json
│   └── eval_set/                # held-out set, used to MEASURE the rules
│       ├── labels.json
│       ├── codex/ claude/ aider/ generic/   # 130 generated logs
│       └── handcrafted/          # 4 original hand-written cases
├── tests/
│   ├── test_detectors.py      # course-baseline tests
│   └── test_package.py        # adapter + CLI tests
├── run_baseline.py            # standalone script (course submission entry point)
├── generate_eval_set.py        # builds examples/eval_set/ with known ground truth
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
repeats don't trigger the retry rule, three do). `test.yml` runs these on
every push.

These are unit tests against the Python package directly — they can't
catch a broken `action.yml`, a wrong input name, or a severity threshold
that's silently ignored. `action-integration.yml` covers that gap by
invoking the packaged Action itself (`uses: ./`) exactly the way a real
consumer would, in three scenarios: a clean log passes, a flagged log with
`fail-on: high` is asserted to actually fail (using `continue-on-error`
plus an explicit outcome check, since a job failing is the expected,
correct result there), and the same flagged log with `fail-on: none`
passes despite the flags. This is the difference between "the code works"
and "the thing someone else drops into their CI works."

## Evaluation

```bash
python generate_eval_set.py --out examples/eval_set --codex 40 --claude 40 --aider 25 --generic 25
python evaluate.py --logs-dir examples/eval_set --labels examples/eval_set/labels.json
```

**Overall: Precision 0.83 · Recall 0.60 · F1 0.70** — measured across 134
generated logs (40 Codex, 40 Claude/Anthropic-transcript, 25 Aider, 25
generic, plus 4 original hand-written cases), independent of the logs used
to design the rules. Ground truth is known by construction: each log is
generated with an explicit label for whether each step *should* be flagged,
including deliberately hard cases (a real failure described in vocabulary
the regex doesn't recognize — expected to be missed) and traps (something
that looks like a failure but is a genuine in-call recovery — expected to
cause a false positive). See `generate_eval_set.py` for exactly how each
case is constructed.

By format:

| Format | Precision | Recall | F1 | Notes |
|---|---|---|---|---|
| Codex rollout | 0.94 | 0.67 | 0.79 | Full output text available — all four signatures usable |
| Claude/Anthropic transcript | 0.80 | 0.68 | 0.74 | Same as above |
| Generic events | 0.74 | 0.72 | 0.73 | Same as above |
| Aider markdown | 1.00 | 0.23 | 0.37 | See below |

**The Aider result is a real, structural finding, not noise.** Aider's
chat-history transcript doesn't reliably expose a distinct tool-*output*
field the way JSON-based tool-calling logs do — only the proposed edit and
the surrounding prose. Three of the four signatures (misread success,
hallucinated path, no-op edit) depend on inspecting that output, so they
cannot fire on Aider logs by construction, regardless of rule quality. Only
retry-loop detection survives, since it only needs repeated identical
calls, not their output — which is exactly what the numbers show: 100%
precision (it never guesses wrong on what little it can see) but recall
capped well below the other formats. Fixing this would require parsing the
prose *between* edit blocks for failure/success language directly, rather
than treating it purely as the agent's after-the-fact claim.

Across the other three formats, the main recall loss is the same
vocabulary-brittleness pattern documented from the original 4-log set: a
real failure described without the regex's specific success/failure words
slips through. The main precision loss is the transient-recovery trap —
a command that failed, retried, and genuinely succeeded within one call,
which the rule can't distinguish from a silent failure since it judges
each tool call as a single unit.

To regenerate with different counts or a different seed, edit the
`random.seed(...)` call or the `--codex/--claude/--aider/--generic` flags
in `generate_eval_set.py`. To add real (not generated) logs to the set,
drop them anywhere under `examples/eval_set/` and add their ground truth to
`labels.json` by hand, the same way the original 4 hand-written cases are
still included today.

## Limitations

- Detection depends on the agent's follow-up text containing fairly specific
  success vocabulary (e.g. "fixed", "passing", "done"). A vaguer claim like
  "that should do it" can slip through undetected — this came up twice while
  building the test suite itself.
- Rules are regex-based and not semantic; phrasing changes can defeat them.
  `sfa explain` (LLM confirmation pass) is the mitigation in progress.
- The Aider adapter can only ever catch retry-loops (23% recall, measured):
  Aider's transcript doesn't expose tool output, so the other three
  signatures are structurally invisible to it, not just harder to detect.
- `sfa watch` polls the session file every couple of seconds rather than
  true event streaming, since Codex CLI has no live socket/webhook.
- No cross-session pattern learning; each run is audited independently.

## License

MIT — see [LICENSE](LICENSE).
