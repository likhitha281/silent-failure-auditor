"""
silent_failure_auditor.adapters -- format detection and normalization.

Each adapter turns one agent tool's log format into a common step sequence:
    {step, time, tool, args, output, textBefore, textAfter}

Supported today:
    - codex_rollout            Codex CLI's ~/.codex/sessions/**/rollout-*.jsonl
    - anthropic_openai_transcript   Messages-API-style transcripts (also covers
                                     Claude Code, which is built on this API)
    - generic_events           A plain array of {tool, args, output, textAfter}
    - aider_markdown           Best-effort parse of Aider's .aider.chat.history.md

Adding a new tool means adding one function here and one line in `normalize()`
-- the detector and CLI never need to change.
"""

import json
import re

AIDER_EDIT_BLOCK_RE = re.compile(
    r"([^\n`]+\.\w+)\n<{5,9}\s*SEARCH.*?={5,9}.*?>{5,9}\s*REPLACE", re.DOTALL
)
AIDER_SHELL_RE = re.compile(r"```(?:bash|sh)\n(.*?)\n```", re.DOTALL)


def block_text(content):
    """Extract plain text from a string or a list of {type, text} content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def normalize_generic_events(data):
    steps = []
    for i, it in enumerate(data):
        args = it.get("args", it.get("arguments", ""))
        if not isinstance(args, str):
            args = json.dumps(args)
        output = it.get("output", it.get("result", ""))
        if not isinstance(output, str):
            output = json.dumps(output)
        steps.append({
            "step": i + 1,
            "time": it.get("time", it.get("timestamp", "")),
            "tool": it.get("tool", it.get("tool_name", it.get("name", "unknown"))),
            "args": args,
            "output": output,
            "textBefore": it.get("textBefore", it.get("agent_note", "")),
            "textAfter": it.get("textAfter", it.get("assistant_text", it.get("note", ""))),
        })
    return steps


def normalize_messages_transcript(messages):
    """Anthropic/OpenAI-style {role, content:[...]} transcript with tool_use/tool_result
    blocks. This is also what a Claude Code session looks like under the hood, since
    Claude Code is built on the same Messages API."""
    steps = []
    pending = {}
    text_buffer = ""
    for msg in messages:
        content = msg.get("content", "")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for b in blocks:
            btype = b.get("type")
            if btype == "text":
                text_buffer += (" " if text_buffer else "") + (b.get("text") or "")
                if steps:
                    steps[-1]["textAfter"] = (steps[-1].get("textAfter") or "") + " " + (b.get("text") or "")
            elif btype == "tool_use":
                s = {
                    "step": len(steps) + 1, "time": "", "tool": b.get("name", "unknown"),
                    "args": b.get("input") if isinstance(b.get("input"), str) else json.dumps(b.get("input", {})),
                    "output": "", "textBefore": text_buffer, "textAfter": "",
                }
                steps.append(s)
                pending[b.get("id")] = s
                text_buffer = ""
            elif btype == "tool_result":
                target = pending.get(b.get("tool_use_id"))
                if target is not None:
                    target["output"] = block_text(b.get("content"))
    return steps


def normalize_codex_rollout(lines):
    """Codex CLI's real session format: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.
    Each line is {timestamp, type, payload}; tool calls and outputs are both
    type:"response_item", linked by a flat payload.call_id."""
    steps = []
    pending = {}
    text_buffer = ""
    for line in lines:
        if line.get("type") != "response_item" or "payload" not in line:
            continue
        p = line["payload"]
        ptype = p.get("type")
        if ptype == "function_call":
            args_str = p.get("arguments", "")
            try:
                args_str = json.dumps(json.loads(args_str))
            except (TypeError, ValueError):
                pass
            s = {
                "step": len(steps) + 1, "time": line.get("timestamp", ""),
                "tool": p.get("name", "unknown"), "args": args_str or "",
                "output": "", "textBefore": text_buffer, "textAfter": "",
            }
            steps.append(s)
            pending[p.get("call_id")] = s
            text_buffer = ""
        elif ptype == "function_call_output":
            target = pending.get(p.get("call_id"))
            if target is not None:
                out = p.get("output", "")
                target["output"] = out if isinstance(out, str) else json.dumps(out)
        elif ptype == "message":
            text = block_text(p.get("content"))
            if p.get("role") == "assistant":
                text_buffer += (" " if text_buffer else "") + text
                if steps:
                    steps[-1]["textAfter"] = (steps[-1].get("textAfter") or "") + " " + text
    return steps


def sniff_aider_markdown(raw_text):
    # A SEARCH/REPLACE block is the strongest signal, but a log with no
    # edits at all (e.g. only read/test steps) won't have one -- the
    # "#### " message-header convention is Aider's other distinctive marker.
    return (bool(AIDER_EDIT_BLOCK_RE.search(raw_text)) or "SEARCH/REPLACE" in raw_text
            or bool(re.search(r"^####\s", raw_text, re.MULTILINE)))


def normalize_aider_markdown(raw_text):
    """
    Best-effort parser for Aider's .aider.chat.history.md transcript.

    Honest limitation: Aider doesn't expose structured tool calls the way
    Codex does -- it applies SEARCH/REPLACE edit blocks it parses from the
    LLM's own text, and there's no official machine-readable schema for this.
    This reconstructs approximate steps (one edit_file step per file touched,
    one run_command step per shell block) on a best-effort basis. Treat flags
    from this adapter as lower-confidence than the JSON-based adapters above.
    """
    steps = []
    blocks = re.split(r"\n(?=####\s)", raw_text)
    for block in blocks:
        for match in AIDER_EDIT_BLOCK_RE.finditer(block):
            path = match.group(1).strip()
            steps.append({
                "step": len(steps) + 1, "time": "", "tool": "edit_file", "args": path,
                "output": "edit block applied", "textBefore": "", "textAfter": "",
            })
        for match in AIDER_SHELL_RE.finditer(block):
            cmd = match.group(1).strip()
            steps.append({
                "step": len(steps) + 1, "time": "", "tool": "run_command", "args": cmd,
                "output": "", "textBefore": "", "textAfter": "",
            })
        prose = AIDER_SHELL_RE.sub("", block)
        prose = AIDER_EDIT_BLOCK_RE.sub("", prose).strip()
        if prose and steps:
            steps[-1]["textAfter"] = (steps[-1]["textAfter"] + " " + prose).strip()
    return steps


ADAPTERS = ["codex_rollout", "anthropic_openai_transcript", "generic_events", "aider_markdown"]


def normalize(raw_text):
    """Auto-detect the log format and return (steps, format_name, error)."""
    data = None
    parsed_as_json = True
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        lines = [l for l in (ln.strip() for ln in raw_text.splitlines()) if l]
        try:
            data = [json.loads(l) for l in lines]
        except json.JSONDecodeError:
            parsed_as_json = False

    if parsed_as_json:
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return normalize_messages_transcript(data["messages"]), "anthropic_openai_transcript", None
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict) and isinstance(first.get("type"), str) and "payload" in first:
                return normalize_codex_rollout(data), "codex_rollout", None
            if isinstance(first, dict) and "role" in first and "content" in first:
                return normalize_messages_transcript(data), "anthropic_openai_transcript", None
            if isinstance(first, dict) and ("tool" in first or "tool_name" in first or "name" in first):
                return normalize_generic_events(data), "generic_events", None
        return None, None, "Valid JSON, but an unrecognized shape. See README for supported formats."

    if sniff_aider_markdown(raw_text):
        return normalize_aider_markdown(raw_text), "aider_markdown", None

    return None, None, "Could not parse as JSON, JSON-lines, or Aider markdown."
