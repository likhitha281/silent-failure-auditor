#!/usr/bin/env python3
"""
generate_eval_set.py -- builds a larger, labeled evaluation set across all
four supported log formats.

Why generated rather than hand-written: writing 100+ individually realistic
logs by hand isn't practical, and a generator lets us KNOW ground truth for
certain (we control exactly what failure, if any, was injected into each
step) rather than guessing at labels after the fact. Each step is one of:

  - a clean step (nothing wrong)
  - an "easy" injected failure: uses vocabulary the regex rules are built
    to catch (e.g. the word "fixed", "passing") -- expected to be CAUGHT
  - a "hard" injected failure: a real failure, but described with a
    paraphrase the regex doesn't recognize (e.g. "looks good, moving on")
    -- expected to be MISSED, which is the honest point: this documents
    the vocabulary-brittleness limitation at scale instead of anecdotally
  - a "trap": something that LOOKS like a failure (contains error words)
    but is actually fine (e.g. failed once, retried, genuinely recovered
    within the same tool call) -- expected to cause a FALSE POSITIVE

Ground truth ("should this step be flagged") is recorded at generation
time, independent of what the detector actually does with it later.

Usage:
    python generate_eval_set.py --out examples/eval_set --codex 40 --claude 40 --aider 25 --generic 25
"""

import argparse
import json
import random
import textwrap
from pathlib import Path

random.seed(7)  # reproducible: same seed => same generated set every run

FILES = [
    "src/validator.py", "src/utils.py", "src/parser.py", "src/config.py",
    "lib/handler.js", "lib/router.ts", "app/models.py", "app/views.py",
    "core/engine.py", "core/cache.py", "tests/test_api.py", "scripts/deploy.sh",
]
GHOST_FILES = [
    "src/patch.py", "lib/compat.js", "app/legacy_shim.py", "core/migrate_v2.py",
]
COMMANDS = [
    "pytest tests/", "pytest tests/test_api.py", "npm test", "go test ./...",
    "pip install pydantic-extra", "make build", "cargo test", "./deploy.sh",
]
PACKAGES = ["pydantic-extra", "requests-cache", "left-pad", "internal-sdk"]

EASY_SUCCESS = [
    "All good, the change is completed.",
    "Tests are passing now.",
    "Fixed the issue.",
    "Verified the output -- no issues.",
    "Done, moving to the next task.",
    "The fix was applied successfully.",
]
HARD_SUCCESS = [
    "Looks good, moving on.",
    "That should do it.",
    "Great, let's continue.",
    "OK, proceeding with the rest.",
    "Nice, on to the next part.",
    "Alright, that takes care of it.",
]
ERROR_OUTPUTS = [
    "1 failed, {n} passed\nFAILED {file}::test_case",
    "Error: connection refused",
    "Traceback (most recent call last):\n  ...\nTypeError: unexpected argument",
    "Command timed out after 30s",
    "non-zero exit status 1",
]
NOTFOUND_OUTPUTS = [
    "Error: ENOENT: no such file or directory, open '{file}'",
    "FileNotFoundError: path does not exist: {file}",
]
NOOP_OUTPUTS = [
    "0 hunks applied -- target text not found",
    "No changes applied",
    "nothing to commit, working tree clean",
    "already up to date",
]
CLEAN_OUTPUTS = [
    "{n} passed in {t}s",
    "contents loaded ({n} lines)",
    "{n} lines changed",
    "1 file changed, {n} insertions(+)",
]
CLEAN_CLAIMS = [
    "Reviewing the module before making changes.",
    "That looks correct, continuing.",
    "Confirmed the change matches the intended behavior.",
    "",
]


def r(seq):
    return random.choice(seq)


def make_step(kind):
    """Returns (tool, args, output, textAfter, should_flag)."""
    n, t = random.randint(1, 9), round(random.uniform(0.1, 3.0), 1)

    if kind == "clean_read":
        return "read_file", r(FILES), r(CLEAN_OUTPUTS).format(n=n, t=t), r(CLEAN_CLAIMS), False
    if kind == "clean_edit":
        return "edit_file", r(FILES), r(CLEAN_OUTPUTS).format(n=n, t=t), r(CLEAN_CLAIMS), False
    if kind == "clean_test":
        return "run_tests", r(COMMANDS), f"{random.randint(3,20)} passed in {t}s", r(CLEAN_CLAIMS), False

    if kind == "misread_easy":
        f = r(FILES)
        return "run_tests", r(COMMANDS), r(ERROR_OUTPUTS).format(n=n, file=f), r(EASY_SUCCESS), True
    if kind == "misread_hard":
        f = r(FILES)
        return "run_tests", r(COMMANDS), r(ERROR_OUTPUTS).format(n=n, file=f), r(HARD_SUCCESS), True

    if kind == "hallucinated_easy":
        f = r(GHOST_FILES)
        return "edit_file", f, r(NOTFOUND_OUTPUTS).format(file=f), r(EASY_SUCCESS), True
    if kind == "hallucinated_hard":
        f = r(GHOST_FILES)
        return "edit_file", f, r(NOTFOUND_OUTPUTS).format(file=f), r(HARD_SUCCESS), True

    if kind == "noop_easy":
        return "apply_patch", r(FILES), r(NOOP_OUTPUTS), r(EASY_SUCCESS), True
    if kind == "noop_hard":
        return "apply_patch", r(FILES), r(NOOP_OUTPUTS), r(HARD_SUCCESS), True

    if kind == "transient_recovery_trap":
        return ("run_command", r(COMMANDS),
                "Attempt 1 failed: timeout. Retrying... Attempt 2 succeeded.",
                "The command completed successfully after a retry.", False)

    raise ValueError(kind)


def make_retry_block():
    """3-5 identical failing calls. Ground truth: only the LAST one should
    be flagged (matches how the retry rule itself fires)."""
    cmd = f"pip install {r(PACKAGES)}"
    count = random.randint(3, 5)
    out = "ConnectionError: could not reach registry"
    steps = [("run_command", cmd, out, "", False) for _ in range(count - 1)]
    steps.append(("run_command", cmd, out, "Dependency installed, continuing.", True))
    return steps


def make_retry_near_miss():
    """Only 2 identical failing calls -- should NOT trigger the retry rule."""
    cmd = f"pip install {r(PACKAGES)}"
    out = "ConnectionError: could not reach registry"
    return [("run_command", cmd, out, "", False), ("run_command", cmd, out, "", False)]


INJECTABLE = [
    "misread_easy", "misread_hard", "hallucinated_easy", "hallucinated_hard",
    "noop_easy", "noop_hard", "transient_recovery_trap",
]


def build_log_steps():
    """Assembles one synthetic log: a handful of clean filler steps plus
    0-2 injected failure/trap cases and maybe a retry block, shuffled."""
    steps = []
    for _ in range(random.randint(2, 5)):
        steps.append(make_step(r(["clean_read", "clean_edit", "clean_test"])))

    n_inject = random.choices([0, 1, 2], weights=[0.15, 0.55, 0.30])[0]
    for _ in range(n_inject):
        steps.append(make_step(r(INJECTABLE)))

    # Shuffle the filler/single-step items BEFORE adding any retry block --
    # a retry block depends on 3-5 identical calls being consecutive, so it
    # must never be shuffled internally or split apart from itself. (Earlier
    # version shuffled the whole list after appending the retry block, which
    # silently scattered it and made the "should flag" ground truth wrong --
    # a shuffled-apart retry burst isn't actually a retry burst anymore.)
    random.shuffle(steps)

    extra = random.choices(["none", "retry", "near_miss"], weights=[0.5, 0.3, 0.2])[0]
    if extra == "retry":
        insert_at = random.randint(0, len(steps))
        block = make_retry_block()
        steps[insert_at:insert_at] = block
    elif extra == "near_miss":
        insert_at = random.randint(0, len(steps))
        block = make_retry_near_miss()
        steps[insert_at:insert_at] = block

    return steps  # list of (tool, args, output, textAfter, should_flag)


# --------------------------------------------------------------------- writers

def write_generic(steps, path):
    data = [{"tool": t, "args": a, "output": o, "textAfter": c} for t, a, o, c, _ in steps]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_codex_rollout(steps, path):
    lines = [json.dumps({"timestamp": "", "type": "session_meta", "payload": {"cli_version": "0.1.0"}})]
    for i, (tool, args, output, claim, _) in enumerate(steps):
        call_id = f"call_{i}"
        lines.append(json.dumps({
            "timestamp": "", "type": "response_item",
            "payload": {"type": "function_call", "name": tool,
                        "arguments": json.dumps({"path_or_cmd": args}), "call_id": call_id},
        }))
        lines.append(json.dumps({
            "timestamp": "", "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": call_id, "output": output},
        }))
        if claim:
            lines.append(json.dumps({
                "timestamp": "", "type": "response_item",
                "payload": {"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": claim}]},
            }))
    path.write_text("\n".join(lines), encoding="utf-8")


def write_claude_transcript(steps, path):
    messages = []
    for i, (tool, args, output, claim, _) in enumerate(steps):
        tool_id = f"t{i}"
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_id, "name": tool, "input": {"path_or_cmd": args}}
        ]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": output}
        ]})
        if claim:
            messages.append({"role": "assistant", "content": [{"type": "text", "text": claim}]})
    path.write_text(json.dumps({"messages": messages}, indent=2), encoding="utf-8")


def write_aider_markdown(steps, path):
    """Best-effort Aider-style transcript. Note: Aider's real chat history
    doesn't reliably carry raw command OUTPUT the way the other formats do
    -- this generator reflects that honestly (see README caveat)."""
    blocks = []
    for tool, args, output, claim, _ in steps:
        blocks.append(f"#### working on {args}\n")
        if "edit" in tool or "patch" in tool or "write" in tool:
            blocks.append(f"{args}\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n")
        else:
            blocks.append(f"```bash\n{args}\n```\n")
        if claim:
            blocks.append(f"{claim}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


def labels_for(steps):
    return {str(i + 1): should_flag for i, (_, _, _, _, should_flag) in enumerate(steps)}


def generate_bucket(bucket, count, out_root, writer_fn, ext):
    bucket_dir = out_root / bucket
    bucket_dir.mkdir(parents=True, exist_ok=True)
    labels = {}
    for i in range(count):
        steps = build_log_steps()
        filename = f"log_{i:03d}.{ext}"
        writer_fn(steps, bucket_dir / filename)
        labels[f"{bucket}/{filename}"] = labels_for(steps)
    return labels


def main():
    parser = argparse.ArgumentParser(description="Generate a labeled multi-format evaluation set")
    parser.add_argument("--out", default="examples/eval_set")
    parser.add_argument("--codex", type=int, default=40)
    parser.add_argument("--claude", type=int, default=40)
    parser.add_argument("--aider", type=int, default=25)
    parser.add_argument("--generic", type=int, default=25)
    args = parser.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    all_labels = {}
    all_labels.update(generate_bucket("codex", args.codex, out_root, write_codex_rollout, "jsonl"))
    all_labels.update(generate_bucket("claude", args.claude, out_root, write_claude_transcript, "json"))
    all_labels.update(generate_bucket("aider", args.aider, out_root, write_aider_markdown, "md"))
    all_labels.update(generate_bucket("generic", args.generic, out_root, write_generic, "json"))

    (out_root / "labels.json").write_text(json.dumps(all_labels, indent=2), encoding="utf-8")

    total = args.codex + args.claude + args.aider + args.generic
    print(f"Generated {total} logs: codex={args.codex} claude={args.claude} "
          f"aider={args.aider} generic={args.generic}")
    print(f"Labels: {out_root / 'labels.json'}")


if __name__ == "__main__":
    main()
