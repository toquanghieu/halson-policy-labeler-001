"""Distil the labeling agent into a small local model (the brief's "nice to have").

    LLM_PROVIDER=claude_cli uv run python -m scripts.distill teacher   # Claude labels the 1,000 dev rows
    uv run python -m scripts.distill export                            # -> data/distill/teacher.json.gz (committed)
    uv run python -m scripts.distill dataset                           # -> data/distill/{train,valid}.jsonl
    make slm-train                                                     # LoRA fine-tune with MLX (Apple silicon)
    make slm-serve                                                     # serve it on :8090 (OpenAI-style API)
    uv run python -m scripts.run_eval --tag slm --provider slm --tier1 slm --no-escalation

Teacher: the production cascade (Haiku 5.5, escalating to Opus 5.5) under policy v1 on the same
1,000 balanced development rows the router trains on (500 fraudulent, 500 legitimate; never the
eval set). Its labels are stored under the version id `v1@teacher`.

Everything needed to skip the expensive steps is committed: the teacher's 1,000 decisions with
traces (data/distill/teacher.json.gz), the train/valid split (train.jsonl.gz, valid.jsonl.gz) and
the trained LoRA adapter (data/distill/adapter/). `dataset` and `make slm-train` use them when present.

Student: Qwen2.5-1.5B-Instruct (Apache-2.0) with a LoRA adapter. It reads the same system prompt
(the rendered policy) and posting text, and is trained to emit the teacher's *verified* findings:
only quotes that passed verification and sections that survived it. At run time its output goes
through the same verification and aggregation as Claude's, so a student that invents a quote is
caught exactly like a large model would be.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from typing import Any

from labeler.bootstrap import build_service
from labeler.config import get_settings
from labeler.infra.llm.slm_adapter import student_messages
from labeler.labeling.prompt import output_schema, system_prompt, user_prompt
from labeler.labeling.signals import extract_signals, metadata_flags
from scripts.data import ROUTER_SAMPLE, dev_rows, read_dataset

TEACHER_TAG = "teacher"
POLICY = "v1"
MAX_CHARS = 14000


def sample_ids() -> list[str]:
    return dev_rows(read_dataset(), ROUTER_SAMPLE)["job_id"].astype(str).tolist()


async def teacher(limit: int | None = None) -> None:
    settings = get_settings()
    svc = build_service(settings)
    policy = svc.policy(POLICY)
    tagged = policy.model_copy(update={"version": f"{POLICY}@{TEACHER_TAG}"})
    ids = sample_ids()[:limit] if limit else sample_ids()
    print(
        f"teacher: {len(ids)} dev postings under {tagged.version} "
        f"({settings.llm_provider}: {settings.tier1_model} -> {settings.tier2_model or 'off'}, "
        f"concurrency {settings.max_concurrency}); resumable"
    )
    await svc.run_ids(tagged, ids, skip_existing=True)
    done = len(svc.db.ids_labeled_under(tagged.version))
    print(f"teacher labels stored: {done}")
    svc.db.close()


def target(result) -> dict[str, Any]:
    """The teacher's final answer, reduced to what survived verification."""
    t = result.trace
    final = next(s for s in reversed(t["steps"]) if "raw" in s)
    raw = final["raw"]
    rejected = {(q["section"], q["quote"]) for q in final.get("rejected_quotes", [])}
    dropped = {d["section"] for d in final.get("dropped_sections", [])}
    findings = []
    for f in raw.get("findings", []):
        if f["section"] in dropped:
            continue
        quotes = [q for q in f.get("quotes", []) if (f["section"], q) not in rejected]
        findings.append({**f, "quotes": quotes})
    return {
        "findings": findings,
        "insufficient_evidence": bool(raw.get("insufficient_evidence", False)),
        "rationale": raw.get("rationale", ""),
        "confidence": round(float(raw.get("confidence", 0.5)), 2),
    }


def _distill_dir():
    return get_settings().data_dir / "distill"


def export() -> None:
    """Write the teacher's decisions (with full traces) to the repo, so nobody re-runs ~1,400 calls."""
    import gzip

    svc = build_service(get_settings())
    rows = []
    for jid in sample_ids():
        r = svc.db.get_label(jid, f"{POLICY}@{TEACHER_TAG}")
        if r is not None:
            rows.append(r.model_dump(mode="json"))
    out = _distill_dir() / "teacher.json.gz"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f"wrote {out}: {len(rows)} teacher decisions, {out.stat().st_size // 1024} KiB")
    svc.db.close()


def _import_teacher(svc) -> int:
    """Load the committed teacher decisions into the database if they are not there yet."""
    import gzip

    from labeler.labeling.schemas import LabelResult

    path = _distill_dir() / "teacher.json.gz"
    if not path.exists() or svc.db.ids_labeled_under(f"{POLICY}@{TEACHER_TAG}"):
        return 0
    with gzip.open(path, "rt", encoding="utf-8") as f:
        rows = json.load(f)
    for row in rows:
        svc.db.save_label(LabelResult.model_validate(row), emit_event=False)
    return len(rows)


def unpack() -> None:
    """data/distill/*.jsonl.gz (committed) -> *.jsonl, which mlx_lm.lora reads."""
    import gzip
    import shutil

    for name in ("train", "valid"):
        gz, plain = _distill_dir() / f"{name}.jsonl.gz", _distill_dir() / f"{name}.jsonl"
        if gz.exists() and not plain.exists():
            with gzip.open(gz, "rb") as src, plain.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            print(f"unpacked {plain}")


def dataset(valid_share: float = 0.1) -> None:
    import gzip
    import shutil

    settings = get_settings()
    svc = build_service(settings)
    if n := _import_teacher(svc):
        print(f"loaded {n} teacher decisions from data/distill/teacher.json.gz")
    policy = svc.policy(POLICY)
    system, schema = system_prompt(policy), output_schema(policy)
    rows = []
    for jid in sample_ids():
        r = svc.db.get_label(jid, f"{POLICY}@{TEACHER_TAG}")
        if r is None or r.trace.get("tier") == "failed":
            continue
        posting = svc.db.get_posting(jid)[0]
        user = user_prompt(posting, metadata_flags(posting), extract_signals(posting))
        msgs = student_messages(system, user, schema)
        msgs.append({"role": "assistant", "content": json.dumps(target(r), ensure_ascii=False)})
        if sum(len(m["content"]) for m in msgs) > MAX_CHARS:  # ~4k tokens: longer ones would be truncated
            continue
        rows.append({"job_id": jid, "messages": msgs})
    random.Random(7).shuffle(rows)  # noqa: S311  (a reproducible split, not security)
    n_valid = max(1, int(len(rows) * valid_share))
    out = settings.data_dir / "distill"
    out.mkdir(parents=True, exist_ok=True)
    for name, part in (("valid", rows[:n_valid]), ("train", rows[n_valid:])):
        with (out / f"{name}.jsonl").open("w") as f:
            for row in part:
                f.write(json.dumps({"messages": row["messages"]}, ensure_ascii=False) + "\n")
        with (out / f"{name}.jsonl").open("rb") as src, gzip.open(out / f"{name}.jsonl.gz", "wb") as dst:
            shutil.copyfileobj(src, dst)  # the committed copy
    labels = [json.loads(r["messages"][-1]["content"]) for r in rows]
    with_sections = sum(1 for x in labels if x["findings"])
    print(f"wrote {out}/train.jsonl ({len(rows) - n_valid}) and valid.jsonl ({n_valid}); {with_sections} with findings")
    svc.db.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "teacher"
    if cmd == "teacher":
        asyncio.run(teacher(int(sys.argv[2]) if len(sys.argv) > 2 else None))
    elif cmd == "dataset":
        dataset()
    elif cmd == "export":
        export()
    elif cmd == "unpack":
        unpack()
    else:
        raise SystemExit(f"unknown command {cmd!r}")
