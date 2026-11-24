"""Generate the Postman collection (postman/policy-labeler.postman_collection.json).

    uv run python -m scripts.postman_collection

Every request carries a description written for a caller (what it does, inputs, side effects,
errors) and saved examples. The examples are not hand-typed: this script runs each request
against the real app (in-process, temporary database, scripted model) in a realistic order
and saves the actual responses, errors included. Re-run it after an API change so the
collection cannot drift from the code. No model is called and nothing touches your data.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from labeler.config import REPO_ROOT, Settings
from labeler.infra.llm.fake_adapter import FakeProvider
from labeler.infra.llm.port import LLMRequest
from labeler_api.main import create_app

OUT = REPO_ROOT / "postman"

MULE = {
    "job_id": "demo-mule",
    "title": "Payment Processing Agent",
    "location": "US, NY, New York",
    "company_profile": "",
    "description": (
        "Work from home and earn $3,000 per week, no experience needed. You will receive client "
        "payments into your personal account and forward them via Western Union."
    ),
    "requirements": "Must be 18+. Bank account required.",
    "benefits": "Flexible hours.",
    "salary_range": "",
    "telecommuting": True,
    "has_company_logo": False,
}
CLERICAL = {
    "job_id": "demo-clerical",
    "title": "Data Entry Clerk",
    "company_profile": "",
    "description": "Easy home-based data entry, start today. Simple typing tasks from your own computer.",
    "requirements": "Basic computer skills.",
}
ENGINEER = {
    "job_id": "demo-engineer",
    "title": "Backend Engineer",
    "company_profile": "Acme builds billing software for clinics across the US.",
    "description": "Design and run our payments API in Python and Postgres.",
    "requirements": "3+ years of Python.",
    "benefits": "Health insurance, 401k.",
    "salary_range": "90000-120000",
    "has_company_logo": True,
}


def scripted_model(req: LLMRequest) -> dict[str, Any]:
    """Stands in for Claude: reports only sections the policy offers, with quotes that exist."""
    if "findings" not in req.schema.get("properties", {}):  # the Settings page's connection test
        return {"ok": True}
    offered = req.schema["properties"]["findings"]["items"]["properties"]["section"]["enum"]
    text = req.user.lower()
    findings = []
    if "western union" in text:
        findings.append(
            {
                "section": "§3",
                "quotes": ["forward them via Western Union"],
                "facts": [],
                "explanation": "Receiving and forwarding client payments is money handling for the employer.",
            }
        )
        findings.append(
            {
                "section": "§4",
                "quotes": ["earn $3,000 per week, no experience needed"],
                "facts": [],
                "explanation": "Pay is disproportionate to a role that needs no experience.",
            }
        )
    if "§6" in offered and "data entry" in text:
        findings.append(
            {
                "section": "§6",
                "quotes": ["Easy home-based data entry, start today"],
                "facts": [],
                "explanation": "Vague home-based clerical work with no employer named.",
            }
        )
    return {
        "findings": findings,
        "insufficient_evidence": False,
        "rationale": (
            "The posting asks applicants to move money for the employer and promises implausible pay."
            if findings and findings[0]["section"] == "§3"
            else "No policy section applies."
            if not findings
            else "Only §6 applies: vague home-based clerical work."
        ),
        "confidence": 0.92 if findings else 0.85,
    }


# ---- request catalogue ------------------------------------------------------------
# Each entry: folder, name, method, path (with :vars), query, body, description, examples.
# An example is (example name, overrides) where overrides may replace path/query/body.
R: list[dict[str, Any]] = []


def req(
    folder: str,
    name: str,
    method: str,
    path: str,
    description: str,
    *,
    query: dict[str, Any] | None = None,
    body: Any = None,
    examples: list[tuple[str, dict[str, Any]]] | None = None,
    tests: str = "",
) -> None:
    R.append(
        {
            "folder": folder,
            "name": name,
            "method": method,
            "path": path,
            "query": query or {},
            "body": body,
            "description": description.strip(),
            "examples": examples or [("200 OK", {})],
            "tests": tests,
        }
    )


V2 = (REPO_ROOT / "policies" / "v2.yaml").read_text()
V3 = re.sub(r"^version:.*$", "version: v3", V2, count=1, flags=re.M)

F_LABEL, F_INBOX, F_ANALYST, F_POLICY, F_SETTINGS, F_OPS = (
    "1. Labeling (marketplace)",
    "1b. Messages (inbox)",
    "2. Analyst reads",
    "3. Policy lifecycle",
    "4. Run-time settings",
    "5. Operations",
)

req(
    F_OPS,
    "Health",
    "GET",
    "/api/health",
    """
Liveness plus what this deployment is wired to: model provider, the **effective** tier-1 and tier-2
models (after Settings-page overrides), whether the router model is loaded, the published policy
version, database dialect, queue and storage backends, and the depth of the `live` and `backfill`
queues (`visible` = waiting, `in_flight` = taken by a worker, not yet acknowledged).

Always answers 200 while the process is up; a queue that cannot be reached shows `{"error": ...}`
in its entry instead of failing the call.
""",
)

req(
    F_LABEL,
    "Label one posting (wait for the decision)",
    "POST",
    "/api/postings",
    """
Labels one posting and returns the full decision. The API never calls the model itself: the posting
becomes a one-item job on the live queue, a worker labels it like any other (lease, retries,
dead-letter queue, concurrency cap), and the call waits for the result, up to `SYNC_LABEL_WAIT_S`
(50 s; a typical posting takes 5-15 s). For tools and one-off checks; the volume path is the
batch endpoint.

**Body**: a posting. Only `job_id` is required; text fields are `title`, `company_profile`,
`description`, `requirements`, `benefits`; metadata `location`, `salary_range`, `employment_type`,
`required_experience`, `required_education`, `industry`, `function`, `department`,
`telecommuting`, `has_company_logo`, `has_questions`. The posting is stored (upsert by `job_id`).

**Query**: `policy_version` (optional) labels under that version (a draft too); default is the
published one.

**Response**: `label` (VIOLATING | ALLOWED | NEEDS_REVIEW), `policy_sections`, `evidence` (verbatim
quotes, each verified to occur in the posting), `rationale`, `confidence`, `policy_version`, plus
`evidence_spans` (field + character offsets into the original text, used for highlighting) and
`trace` (facts, hints, router score, each tier's raw output, rejected quotes, escalation reasons,
tokens, cost, latency).

**Side effects**: one or two model calls (tier 1, plus tier 2 if escalated); a `PostingLabeled`
event in the outbox. A model failure is not an error: the posting comes back `NEEDS_REVIEW`,
confidence 0, `trace.tier = "failed"`.

**202** when the worker has not finished within the wait: `{job_id, status: "queued", status_url,
result_url}`. Poll `status_url`, then read `result_url`.

**Errors**: 404 unknown `policy_version` (checked before anything is queued), 422 invalid body, 503
when no worker can label now (none running, or all waiting for an API key; the reason is in
`detail`).
""",
    body=MULE,
    examples=[
        ("200 VIOLATING (§3 + §4)", {}),
        ("200 ALLOWED", {"body": ENGINEER}),
        ("404 unknown policy version", {"query": {"policy_version": "v9"}}),
        ("422 missing job_id", {"body": {"title": "No id"}}),
    ],
    tests='pm.test("status 200", () => pm.response.to.have.status(200));\n'
    'pm.test("label is one of the three", () => pm.expect(["VIOLATING","ALLOWED","NEEDS_REVIEW"]).to.include(pm.response.json().label));',
)

req(
    F_LABEL,
    "Submit a batch (live queue)",
    "POST",
    "/api/postings/batch",
    """
Stores up to **5,000** postings and puts one message per posting on the **live** queue (SQS /
ElasticMQ), then returns at once with a job id. Workers label them asynchronously; the call
returns only after every message is enqueued, so an accepted batch is never lost.

**Body**: `{"postings": [...], "policy_version": null}`; `policy_version` optional (default: the
published one).

**Then**: poll `GET /api/jobs/{job_id}` until `status` is `done`, and read each decision with
`GET /api/results/{posting_id}`, or follow `GET /api/events`.

**Delivery**: at-least-once. A redelivered message is harmless: each job item is recorded once,
and the label is keyed by (posting, version).

**Errors**: 404 unknown `policy_version`, 422 empty list or more than 5,000 postings.
""",
    body={"postings": [MULE, CLERICAL, ENGINEER]},
    examples=[("200 accepted", {}), ("422 empty batch", {"body": {"postings": []}})],
    tests='pm.test("status 200", () => pm.response.to.have.status(200));\n'
    'if (pm.response.code === 200) pm.collectionVariables.set("batchJobId", pm.response.json().job_id);',
)

req(
    F_LABEL,
    "Job progress",
    "GET",
    "/api/jobs/:jobId",
    """
Progress of a batch or a re-label: `status` (queued → running → done, or failed if enqueueing
failed), `total`, `done`, `failed` (items whose model call failed, or messages that went to the
dead-letter queue after 3 attempts), `kind` (batch | relabel), `policy_version`, `queue`
(live | backfill), `detail` (for re-labels, the scope), timestamps.

**Errors**: 404 unknown job.
""",
    examples=[("200 done", {}), ("404 unknown job", {"path": "/api/jobs/999999"})],
)

req(
    F_LABEL,
    "Cancel a job",
    "POST",
    "/api/jobs/:jobId/cancel",
    """
Stops a queued or running batch or re-label. Items not yet labeled are cancelled: a worker that
receives their message acknowledges it without calling the model. Postings already labeled keep
their labels, and every posting's status returns to what its stored label says.

**Response**: `job_id`, `status: "cancelled"`, `cancelled_items`.
**Errors**: 404 unknown job, 409 the job already finished.
""",
    examples=[("409 already finished", {}), ("404 unknown job", {"path": "/api/jobs/999999/cancel"})],
)

req(
    F_LABEL,
    "List recent jobs",
    "GET",
    "/api/jobs",
    "The 20 most recent jobs, newest first, with the same fields as *Job progress*.",
)

req(
    F_LABEL,
    "Work in progress (queued, labeling, failed)",
    "GET",
    "/api/work",
    "What the system is doing now, per posting and policy version: `labeling` (a worker has "
    "taken it), `queued` (waiting for a worker) or `failed`. Labeling ones come first. `counts` "
    "covers every version and state; `items` is one page of the states asked for.\n\n"
    "Next to it: `queues` (the broker's own depth), `workers` (heartbeats, each `running` or "
    "`waiting` with the reason, e.g. no API key) and the open `jobs`. If `counts` says queued "
    "but the broker holds nothing, a local ElasticMQ lost its messages on restart: "
    "`POST /api/jobs/{id}/requeue` puts them back. The UI's **Work** tab shows this page.",
    query={"status": "queued", "version": "", "limit": "50", "offset": "0"},
)

req(
    F_LABEL,
    "Get a decision (public shape)",
    "GET",
    "/api/results/:postingId",
    """
The decision for a posting under the **published** policy: exactly the fields of the assignment's
output schema (`job_id`, `label`, `policy_sections`, `evidence`, `rationale`, `confidence`,
`policy_version`). This is the marketplace's read path.

**Query**: `policy_version` for another version (e.g. a draft being shadow-run); `full=true` adds
`evidence_spans` and the full `trace`.

**Errors**: 404 when the posting has no label under that version (not labeled yet, or unknown).
""",
    examples=[
        ("200 public shape", {}),
        ("200 full=true (spans + trace)", {"query": {"full": "true"}}),
        ("404 not labeled", {"path": "/api/results/never-seen"}),
    ],
)

req(
    F_LABEL,
    "Decision events (outbox)",
    "GET",
    "/api/events",
    """
`PostingLabeled` events in id order, written in the **same transaction** as each label (transactional
outbox), so an event exists if and only if the label does. The marketplace polls with
`after=<next_after from the previous page>` and acts on `VIOLATING`. In production a relay
publishes the same rows to EventBridge.

**Query**: `after` (event id, default 0), `limit` (1–1000, default 100).
**Response**: `items[]` (`id`, `type`, `job_id`, `policy_version`, `payload` with label, sections,
confidence), `next_after`.
""",
    query={"after": "0", "limit": "100"},
)

req(
    F_INBOX,
    "Import postings (no labeling)",
    "POST",
    "/api/inbox/import",
    """
Stores up to 5,000 postings **without labeling them**; they wait in the Messages tab. Same posting
shape as *Label one posting*. An existing `job_id` is updated in place and keeps its dataset label
and eval-set flag.

**Response**: `stored`, `new`, `updated`.
**Errors**: 422 empty list, more than 5,000, or a duplicate `job_id` in the upload.
""",
    body={
        "postings": [
            CLERICAL,
            {"job_id": "demo-new", "title": "Warehouse Associate", "description": "Pick and pack orders."},
        ]
    },
    examples=[("201 stored", {}), ("422 duplicate job_id", {"body": {"postings": [{"job_id": "x"}, {"job_id": "x"}]}})],
)

req(
    F_INBOX,
    "Messages waiting for a decision",
    "GET",
    "/api/inbox",
    """
Postings with **no decision under the published policy** (or `policy_version`). Once labeled,
a posting leaves this list and shows up in the decision queue (`GET /api/labels`).

**Query**: `scope` = `inbox` (default: the 400 eval postings + everything imported) | `imported` |
`eval` | `all` (includes the dataset's development rows); `q` text or job-id search; `limit`, `offset`.

**Response**: `policy_version`, `total`, `counts` per scope, `items[]` (job id, title, location,
snippet, has_company_profile, source = eval set | imported | dataset).
""",
    query={"scope": "inbox", "limit": "50", "offset": "0"},
)

req(
    F_INBOX,
    "Label messages",
    "POST",
    "/api/inbox/label",
    """
Puts stored postings on the **live** queue under the published policy (or `policy_version`).
Either `ids`, or `all_in_scope: true` with `scope` (and optional `q`) for every unlabeled posting
in that view, up to 5,000. Calls the model once per posting (plus escalations).

**Response**: `job_id`, `count`, `policy_version`, `status_url`.
**Errors**: 422 neither `ids` nor `all_in_scope`, 404 an unknown posting id.
""",
    body={"ids": ["demo-new"]},
    examples=[("200 queued", {}), ("404 unknown id", {"body": {"ids": ["never-seen"]}})],
)

req(
    F_ANALYST,
    "Decision queue (filter + sort)",
    "GET",
    "/api/labels",
    """
What the analyst UI's *Decisions* page lists. All filters are optional and combine:

- `policy_version` (default: published)
- `label` = VIOLATING | ALLOWED | NEEDS_REVIEW
- `section` = a section id, e.g. `§3` (URL-encoded `%C2%A73`)
- `tier` = tier1 | tier2 | failed (who made the final call)
- `eval_only` = true for the fixed 400-posting evaluation set
- `disagree` = true for decisions that disagree with the dataset's gold label
- `q` = case-insensitive text search over the posting (or a job id)
- `order` = risk (default) | confidence | recent | id
- `limit` 1–200 (default 50), `offset`

**Response**: `policy_version`, `total` (before paging), `items[]` with job id, title, location,
label, sections, confidence, rationale, tier, gold, is_eval.
""",
    query={"label": "VIOLATING", "order": "risk", "limit": "50", "offset": "0"},
    examples=[("200 VIOLATING only", {}), ("200 by section §3", {"query": {"section": "§3"}})],
)

req(
    F_ANALYST,
    "One case (posting + decision + history)",
    "GET",
    "/api/postings/:postingId",
    """
Everything the case view needs: the posting text, the dataset gold label (`gold`, null for postings
not from EMSCAD), `is_eval`, the full decision under `policy_version` (default: published; `result`
is null if it was never labeled under it), and `history`: one line per version that labeled this
posting (label, sections, confidence, when).

**Errors**: 404 unknown posting.
""",
    examples=[("200", {}), ("404 unknown posting", {"path": "/api/postings/never-seen"})],
)

req(
    F_ANALYST,
    "One posting under several versions",
    "GET",
    "/api/postings/:postingId/versions",
    """
The same posting's **full** decision under 1–8 policy versions, side by side, in the order asked
(repeat `v`: `?v=v1&v=v2&v=v3`). Each entry has `policy_version`, the version's `status`
(draft | published | retired) and `result` (or `null` when that version never labeled this
posting). This backs the case view's *Compare vN with* panel.

To fill a `null`, label just this posting under that version:
`POST /api/policies/{version}/relabel {"scope": "ids", "ids": ["<postingId>"]}`.

**Errors**: 404 unknown posting or unknown version; 422 no `v` or more than 8.
""",
    query={"v": ["v1", "v2"]},
    examples=[
        ("200 v1 labeled, v2 not yet", {}),
        ("404 unknown version", {"query": {"v": ["v1", "v9"]}}),
    ],
)

req(
    F_ANALYST,
    "Evaluation metrics",
    "GET",
    "/api/metrics",
    """
Metrics for one version against the dataset's gold labels. Precision/recall/F1 for the fraudulent
class are reported two ways: **strict** (only VIOLATING counts as positive) and **flagged**
(VIOLATING + NEEDS_REVIEW positive). Also the 3×2 confusion matrix, review rate, escalation rate,
sections cited by gold class, decisions per tier, cost per 1,000 postings and p50/p95 latency.

**Query**: `policy_version` (default published), `eval_only` (default true: the fixed 400 set;
false = every labeled posting with a gold label).
""",
    query={"eval_only": "false"},
)

req(
    F_ANALYST,
    "Compare two versions",
    "GET",
    "/api/compare",
    """
Which postings changed decision between version `a` and version `b`, over postings labeled under
both: `compared`, `transitions` (counts like `ALLOWED->NEEDS_REVIEW`) and `changed[]` (job id,
before/after label and sections, gold). The Policy page's *Impact* panel.
""",
    query={"a": "v1", "b": "v2"},
)

req(
    F_POLICY,
    "List versions",
    "GET",
    "/api/policies",
    "Every policy version with `status` (draft | published | retired), the YAML `source`, change `note`, "
    "timestamps and `labeled` (how many postings have a decision under it). Exactly one is published.",
)

req(
    F_POLICY,
    "Validate a document (no save)",
    "POST",
    "/api/policies/validate",
    """
Parses a policy YAML without saving it; the editor calls this as the analyst types. Returns
`{"ok": true, "parsed": ..., "rendered": "<the exact policy text the model will read>"}` or
`{"ok": false, "error": "<readable reason>"}`. Always 200.
""",
    body={"source": V2},
    examples=[("200 ok", {}), ("200 invalid YAML", {"body": {"source": "version: v3\ntitle: x\nsections: []"}})],
)

req(
    F_POLICY,
    "Create a draft",
    "POST",
    "/api/policies",
    """
Creates a **draft** from a policy YAML. The version id comes from the document (`version: v2`).
Drafts can be edited, shadow-run and deleted; nothing serves them until published.

**Body**: `{"source": "<yaml>", "note": "why this version exists"}`.

**Errors**: 409 the version id already exists (ids are immutable), 422 the document is invalid
(the message says what is wrong: missing field, duplicate section id, unknown effect, ...).
""",
    body={"source": V2, "note": "Adds §6 vague home-based clerical work (review only)."},
    examples=[
        ("201 created", {}),
        ("409 version exists", {}),
        ("422 invalid document", {"body": {"source": "version: v4\ntitle: x\nsections: []"}}),
    ],
)

req(
    F_POLICY,
    "Get one version",
    "GET",
    "/api/policies/:version",
    "The stored row plus `parsed` (sections with id, name, effect, text; edge cases). 404 if unknown.",
    examples=[("200", {}), ("404 unknown", {"path": "/api/policies/v9"})],
)

req(
    F_POLICY,
    "Edit a draft",
    "PUT",
    "/api/policies/:version",
    """
Replaces a draft's YAML and note. Only drafts are editable: published and retired versions are
immutable (409), so every label can be traced to the exact text that produced it. The document's
`version:` must match the URL (422).
""",
    body={"source": V2, "note": "Adds §6; wording tightened."},
    examples=[("200 saved", {}), ("409 not a draft", {"path": "/api/policies/v1", "body": {"source": V2}})],
)

req(
    F_POLICY,
    "Preview a re-label (count only)",
    "GET",
    "/api/policies/:version/relabel/preview",
    """
How many postings a `recent` re-label under `version` would touch, **before** starting it and
paying for it. The publish dialog shows this number.

**Query**: `days` (1–365, default 30), `skip_labeled` (default true: postings already labeled under
this version are not counted, as the publish backfill skips them).

Note for local data: the EMSCAD dataset has no posting dates, so `created_at` is when it was loaded
and every posting counts as recent.
""",
    query={"days": "30", "skip_labeled": "true"},
)

req(
    F_POLICY,
    "Re-label / shadow-run",
    "POST",
    "/api/policies/:version/relabel",
    """
Runs a version over existing postings on the **backfill** queue. Works on drafts (a shadow run: the
published version keeps serving) and on the published version. Workers read the backfill queue
only when the live queue is empty, so this never delays new postings.

**Body**:
- `scope`: `eval` (the fixed 400 set) | `recent` (posted in the last `days`) | `previous`
  (everything labeled under the published version) | `ids` (the given `ids`)
- `days` (1–365, default 30), `limit` (optional cap), `ids` (for scope=ids),
  `skip_labeled` (default false)

**Response**: `job_id`, `status_url`. Results land under `version` without touching other versions.

**Errors**: 404 unknown version or posting id, 422 unknown scope or `ids` missing for scope=ids.
""",
    body={"scope": "ids", "ids": ["demo-clerical"]},
    examples=[
        ("200 one posting (scope=ids)", {}),
        ("200 eval set", {"body": {"scope": "eval", "limit": 50}}),
        ("422 unknown scope", {"body": {"scope": "everything"}}),
    ],
)

req(
    F_POLICY,
    "Publish (optionally with backfill)",
    "POST",
    "/api/policies/:version/publish",
    """
Makes a draft the published version. New postings are labeled with it immediately; the previous
version becomes `retired` (kept, never deleted). No code change, no restart.

**Body** (optional):
- `backfill`: true to also re-label the last `days` under the new version on the backfill queue;
  null = the `backfill_on_publish` setting (Settings page)
- `days`: null = the `backfill_days` setting (default 30)

Postings that already have a label under this version are skipped (labels are keyed by posting
and version).

**Response**: `version`, `status`, `backfill_job_id` (null if no backfill), `status_url`.

**Errors**: 404 unknown version, 409 already published.
""",
    body={"backfill": True, "days": 30},
    examples=[("200 published + backfill job", {}), ("409 already published", {})],
)

req(
    F_POLICY,
    "Roll back (re-publish a retired version)",
    "POST",
    "/api/policies/:version/publish",
    """
Same endpoint as *Publish*, called on a **retired** version: it becomes the published one again and
the current one is retired. Because the old version's labels are still stored, a backfill here only
labels postings that arrived after it was retired; in this example that is none (`total: 0`).
""",
    body={"backfill": True},
)

req(
    F_POLICY,
    "Delete a draft",
    "DELETE",
    "/api/policies/:version",
    "Deletes a **draft**. Published and retired versions cannot be deleted (409): labels refer to them. "
    "The request targets `v3`: create it first with *Create a draft* (`version: v3` in the YAML).",
    examples=[("200 deleted", {}), ("409 not a draft", {"path": "/api/policies/v1"})],
)

req(
    F_SETTINGS,
    "Get settings",
    "GET",
    "/api/settings",
    """
Run-time settings an operator can change without a redeploy: `tier1_model`, `tier1_effort`,
`tier2_model` (empty = escalation off), `tier2_effort`, `escalate_below_confidence`,
`router_disagree_threshold`, `backfill_days`, `backfill_on_publish`.

**Response**: `values` (effective), `defaults` (from environment variables), `overridden` (keys
changed from the default), `descriptions`, `choices` (models, efforts), `prices_per_mtok`,
`provider`. Infrastructure settings (database, queues, buckets, provider) are not here: those need
a restart.
""",
)

req(
    F_SETTINGS,
    "Update settings",
    "PUT",
    "/api/settings",
    """
Changes any subset: `{"values": {"tier2_model": "claude-sonnet-5-5", "escalate_below_confidence": 0.5}}`.
Validated as a whole before saving. Stored in the `app_settings` table; the API applies it at once,
workers within ~5 seconds. Setting a key back to its default removes the override. Past decisions
are not rewritten: each trace records the model that made it.

**Errors**: 422 unknown key, a value out of range, an unknown effort, or an empty tier-1 model.
""",
    body={"values": {"tier2_model": "claude-sonnet-5-5", "escalate_below_confidence": 0.5, "backfill_days": 14}},
    examples=[
        ("200 saved", {}),
        ("422 out of range", {"body": {"values": {"escalate_below_confidence": 2}}}),
        ("422 not a run-time setting", {"body": {"values": {"database_url": "postgresql://..."}}}),
    ],
)

req(
    F_SETTINGS,
    "Providers and key status",
    "GET",
    "/api/settings/providers",
    """
Every model provider the app can use (Claude API, Claude CLI, OpenAI, an OpenAI-compatible endpoint
such as Ollama, Gemini, Groq or OpenRouter, the distilled local model): label, free-tier note,
known models, base URL, which tiers use it, and the **status** of its API key: `set`, `source`
(settings store / environment / AWS Secrets Manager) and the last four characters. Never the key.
`secrets_backend` says where keys entered through the API are kept (env | db | aws).
""",
)

req(
    F_SETTINGS,
    "Store an API key",
    "PUT",
    "/api/settings/secrets/:secretName",
    """
Write-only. Stores the key in the configured secret store: encrypted in the database (Fernet) locally,
AWS Secrets Manager in production. Only `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `COMPAT_API_KEY`
are accepted (404 otherwise). The response shows the source and last four characters. Workers pick a
new key up within seconds. `DELETE` the same path removes it (an environment variable then applies).
""",
    body={"value": "paste-your-key-here-1234"},
    examples=[("200 stored", {}), ("404 unknown secret", {"path": "/api/settings/secrets/DATABASE_URL"})],
)

req(
    F_SETTINGS,
    "Test a provider connection",
    "POST",
    "/api/settings/providers/:providerName/test",
    """
Checks a provider with the current key and base URL. It lists the provider's models where it can
(OpenAI-style `/models`, Anthropic models API), then, if `model` is given, makes one tiny structured
call (a few dozen tokens). Always 200: `ok`, `models`, `latency_ms`, `reply` or `error`
(e.g. "authentication failed (401); check the API key"). 404 for an unknown provider.
""",
    body={"model": "claude-haiku-5-5"},
)

req(
    F_SETTINGS,
    "Reset settings",
    "DELETE",
    "/api/settings",
    "Drops every override: back to the environment defaults. Returns the same shape as *Get settings*.",
)

req(
    F_ANALYST,
    "Eval runs",
    "GET",
    "/api/metrics/runs",
    "Versions and named eval runs with decisions on the eval set, and how many: what the "
    "Evaluation page can show. `v1@current` is v1 labeled by the code as submitted (RESULTS.md, Run 2); "
    "`v1` is the first-prompt headline (Run 1).",
)

ORDER = [
    ("Health", 0),
    ("Label one posting (wait for the decision)", None),
    ("Submit a batch (live queue)", None),
    ("Job progress", None),
    ("List recent jobs", None),
    ("Work in progress (queued, labeling, failed)", None),
    ("Get a decision (public shape)", None),
    ("Decision events (outbox)", None),
    ("Import postings (no labeling)", None),
    ("Messages waiting for a decision", None),
    ("Label messages", None),
    ("Decision queue (filter + sort)", None),
    ("One case (posting + decision + history)", None),
    ("Validate a document (no save)", None),
    ("Create a draft", None),
    ("List versions", None),
    ("Get one version", None),
    ("Edit a draft", None),
    ("One posting under several versions", "before"),
    ("Re-label / shadow-run", None),
    ("Compare two versions", None),
    ("Preview a re-label (count only)", None),
    ("Publish (optionally with backfill)", None),
    ("Eval runs", None),
    ("Evaluation metrics", None),
    ("Roll back (re-publish a retired version)", None),
    ("Delete a draft", None),
    ("Get settings", None),
    ("Update settings", None),
    ("Providers and key status", None),
    ("Store an API key", None),
    ("Test a provider connection", None),
    ("Cancel a job", None),
    ("Reset settings", None),
]

# Path variables used when the example does not override the path.
PATH_VARS = {
    "jobId": "1",
    "postingId": "demo-mule",
    "version": "v2",
    "secretName": "OPENAI_API_KEY",
    "providerName": "anthropic",
}
PER_REQUEST_VARS = {
    "Roll back (re-publish a retired version)": {"version": "v1"},
    "Delete a draft": {"version": "v3"},
    "One posting under several versions": {"postingId": "demo-clerical"},
    "Get a decision (public shape)": {"postingId": "demo-mule"},
}


def _fill(path: str, vars_: dict[str, str]) -> str:
    return re.sub(r":(\w+)", lambda m: vars_[m.group(1)], path)


def _wait(c: TestClient, job_id: int) -> None:
    import time

    for _ in range(500):
        if c.get(f"/api/jobs/{job_id}").json()["status"] in ("done", "failed"):
            return
        time.sleep(0.01)


def capture() -> dict[str, list[dict[str, Any]]]:
    """Run every example against the app and return the responses, keyed by request name."""
    by_name = {r["name"]: r for r in R}
    out: dict[str, list[dict[str, Any]]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(_env_file=None, data_dir=Path(tmp), llm_provider="fake")
        with TestClient(create_app(settings, FakeProvider(scripted_model))) as c:
            for name, _ in ORDER:
                r = by_name[name]
                if name == "Delete a draft":
                    c.post("/api/policies", json={"source": V3, "note": "scratch draft"})
                vars_ = {**PATH_VARS, **PER_REQUEST_VARS.get(name, {})}
                results = []
                for ex_name, ov in r["examples"]:
                    path = ov.get("path") or _fill(r["path"], vars_)
                    query = ov.get("query", r["query"])
                    body = ov.get("body", r["body"])
                    resp = c.request(r["method"], path, params=query or None, json=body)
                    if name == "Submit a batch (live queue)" and resp.status_code == 200:
                        _wait(c, resp.json()["job_id"])
                    if name.startswith("Publish") and resp.status_code == 200 and resp.json().get("backfill_job_id"):
                        _wait(c, resp.json()["backfill_job_id"])
                    if name in ("Re-label / shadow-run", "Label messages") and resp.status_code == 200:
                        _wait(c, resp.json()["job_id"])
                    results.append(
                        {
                            "name": ex_name,
                            "path": path,
                            "query": query,
                            "body": body,
                            "status": resp.status_code,
                            "response": resp.json(),
                        }
                    )
                out[name] = results
                if name == "Roll back (re-publish a retired version)":
                    job = results[0]["response"].get("backfill_job_id")
                    if job:
                        _wait(c, job)
    return out


# ---- Postman v2.1 ---------------------------------------------------------------------
def _url(path: str, query: dict[str, Any], fixed: dict[str, str] | None = None) -> dict[str, Any]:
    raw_path = re.sub(r"^/", "", path)
    q = []
    for k, v in (query or {}).items():
        for item in v if isinstance(v, list) else [v]:
            q.append({"key": k, "value": str(item)})
    qs = "&".join(f"{x['key']}={x['value']}" for x in q)
    url: dict[str, Any] = {
        "raw": "{{baseUrl}}/" + raw_path + (f"?{qs}" if qs else ""),
        "host": ["{{baseUrl}}"],
        "path": raw_path.split("/"),
    }
    if q:
        url["query"] = q
    vars_ = re.findall(r":(\w+)", path)
    if vars_:
        fixed = fixed or {}
        url["variable"] = [
            {"key": v, "value": fixed.get(v) or ("{{batchJobId}}" if v == "jobId" else "{{" + v + "}}")} for v in vars_
        ]
    return url


def _request(
    method: str, path: str, query: dict[str, Any], body: Any, description: str = "", fixed: dict[str, str] | None = None
) -> dict[str, Any]:
    r: dict[str, Any] = {"method": method, "header": [], "url": _url(path, query, fixed)}
    if description:
        r["description"] = description
    if body is not None:
        r["header"] = [{"key": "Content-Type", "value": "application/json"}]
        r["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2, ensure_ascii=False),
            "options": {"raw": {"language": "json"}},
        }
    return r


STATUS_TEXT = {200: "OK", 201: "Created", 404: "Not Found", 409: "Conflict", 422: "Unprocessable Entity"}


def build(captured: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    folders: dict[str, list[dict[str, Any]]] = {}
    for r in R:
        item: dict[str, Any] = {
            "name": r["name"],
            "request": _request(
                r["method"], r["path"], r["query"], r["body"], r["description"], PER_REQUEST_VARS.get(r["name"])
            ),
            "response": [
                {
                    "name": ex["name"],
                    "originalRequest": _request(r["method"], ex["path"], ex["query"], ex["body"]),
                    "status": STATUS_TEXT.get(ex["status"], ""),
                    "code": ex["status"],
                    "_postman_previewlanguage": "json",
                    "header": [{"key": "Content-Type", "value": "application/json"}],
                    "body": json.dumps(ex["response"], indent=2, ensure_ascii=False),
                }
                for ex in captured[r["name"]]
            ],
        }
        if r["tests"]:
            item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": r["tests"].split("\n")}}]
        folders.setdefault(r["folder"], []).append(item)
    return {
        "info": {
            "name": "Policy Labeler API",
            "_postman_id": "8d0b2a4e-policy-labeler",
            "description": (
                "Agentic policy labeling for job postings: label postings against a versioned written policy, "
                "with verified evidence quotes, and manage policy versions.\n\n"
                "Run the stack (`docker compose up`) and keep `baseUrl` = http://localhost:8000. "
                "Requests are ordered as a walk-through (label, batch, create v2, shadow-run, publish, roll back, "
                "settings); run the folders top to bottom.\n\n"
                "**Cost note**: *Label one posting* and anything that labels (batch, re-label, publish with backfill) "
                "calls the configured model. Against a stack with LLM_PROVIDER=fake nothing is billed.\n\n"
                "No authentication in this build (DESIGN.md: Cognito in production).\n\n"
                "Saved examples are real responses, captured by `scripts/postman_collection.py` from the app "
                "with a scripted model; regenerate after any API change."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "baseUrl", "value": "http://localhost:8000"},
            {"key": "batchJobId", "value": "1"},
            {"key": "postingId", "value": "demo-mule"},
            {"key": "version", "value": "v2"},
            {"key": "secretName", "value": "OPENAI_API_KEY"},
            {"key": "providerName", "value": "anthropic"},
        ],
        "item": [{"name": f, "item": items} for f, items in sorted(folders.items())],
    }


def main() -> None:
    import logging

    logging.disable(logging.INFO)
    captured = capture()
    OUT.mkdir(exist_ok=True)
    col = build(captured)
    path = OUT / "policy-labeler.postman_collection.json"
    path.write_text(json.dumps(col, indent=2, ensure_ascii=False) + "\n")
    env = {
        "name": "Policy Labeler · local",
        "values": [{"key": "baseUrl", "value": "http://localhost:8000", "enabled": True}],
        "_postman_variable_scope": "environment",
    }
    (OUT / "local.postman_environment.json").write_text(json.dumps(env, indent=2) + "\n")
    n = sum(len(f["item"]) for f in col["item"])
    ex = sum(len(i["response"]) for f in col["item"] for i in f["item"])
    print(f"wrote {path.relative_to(REPO_ROOT)}: {n} requests, {ex} saved examples")
    for name, results in captured.items():
        for e in results:
            print(f"  {e['status']}  {name} · {e['name']}")


if __name__ == "__main__":
    main()
