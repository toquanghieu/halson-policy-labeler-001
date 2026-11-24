"""Policy v2 on DEV postings (never eval rows) chosen to exercise §6: does publishing v2 change behaviour with no code change?

    uv run python -m scripts.policy_demo_dev     # or: make demo-v2-dev

Same code, same prompt, two policy documents. Stored as v1@demo-dev / v2@demo-dev so the
main labels are untouched; compare() reads them like any two versions.
"""

import asyncio
import json

from labeler.bootstrap import build_service
from labeler.config import get_settings

HOME_CLERICAL_FRAUD = ["1408", "17573", "17584", "17657", "17537", "17630"]
OFFICE_CLERICAL_LEGIT = ["10081", "10194", "1045", "10490", "10651", "10856"]
ids = HOME_CLERICAL_FRAUD + OFFICE_CLERICAL_LEGIT

svc = build_service(get_settings())
v1 = svc.policy("v1").model_copy(update={"version": "v1@demo-dev"})
v2 = svc.policy("v2").model_copy(update={"version": "v2@demo-dev"})


async def main():
    await asyncio.gather(svc.run_ids(v1, ids, skip_existing=True), svc.run_ids(v2, ids, skip_existing=True))


asyncio.run(main())
rows = []
for j in ids:
    a, b = svc.db.get_label(j, v1.version), svc.db.get_label(j, v2.version)
    p, gold, _ = svc.db.get_posting(j)
    six = [e.quote for e in b.evidence_spans if e.section == "§6"]
    rows.append(
        {
            "job_id": j,
            "gold": gold,
            "title": p.title.strip(),
            "v1": [a.label.value, a.policy_sections],
            "v2": [b.label.value, b.policy_sections],
            "s6_quotes": six,
            "v2_rationale": b.rationale,
        }
    )
    print(
        f"{j:>6} {'F' if gold else 'L'} {p.title.strip()[:45]:45} v1={a.label.value:12} {','.join(a.policy_sections):10}"
        f" v2={b.label.value:12} {','.join(b.policy_sections):10} §6:{six[:1]}"
    )
out = get_settings().data_dir / "eval" / "v2-demo-dev.json"
out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
print("wrote", out)
