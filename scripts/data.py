"""Load EMSCAD into the database and train the router.

uv run python -m scripts.data load      # postings + gold labels + eval flags
uv run python -m scripts.data router    # TF-IDF + LR on a balanced sample of 1,000 dev rows (500 + 500)
uv run python -m scripts.data router 0  # ... on all 17,480 dev rows instead

The eval rows are never used for training in either case.
"""

from __future__ import annotations

import sys

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline

from labeler.bootstrap import ROUTER_KEY, build_db, build_store
from labeler.config import get_settings
from labeler.labeling.router import router_text
from labeler.labeling.schemas import Posting

DATASET_URL = (
    "https://huggingface.co/datasets/victor/real-or-fake-fake-jobposting-prediction/resolve/main/fake_job_postings.csv"
)


def read_dataset() -> pd.DataFrame:
    s = get_settings()
    if not s.raw_csv.exists():
        s.raw_csv.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {DATASET_URL}")
        pd.read_csv(DATASET_URL).to_csv(s.raw_csv, index=False)
    return pd.read_csv(s.raw_csv).fillna("")


def eval_ids() -> set[str]:
    return {line.strip() for line in get_settings().eval_ids_path.read_text().split() if line.strip()}


def to_posting(row: pd.Series) -> Posting:
    return Posting(
        job_id=str(row["job_id"]),
        **{
            k: str(row[k])
            for k in (
                "title",
                "company_profile",
                "description",
                "requirements",
                "benefits",
                "location",
                "department",
                "salary_range",
                "employment_type",
                "required_experience",
                "required_education",
                "industry",
                "function",
            )
        },
        telecommuting=bool(int(row["telecommuting"] or 0)),
        has_company_logo=bool(int(row["has_company_logo"] or 0)),
        has_questions=bool(int(row["has_questions"] or 0)),
    )


def load() -> None:
    df = read_dataset()
    ev = eval_ids()
    assert len(ev) == 400, f"expected 400 eval ids, got {len(ev)}"
    db = build_db(get_settings())
    rows = [(to_posting(r), int(r["fraudulent"]), str(r["job_id"]) in ev) for _, r in df.iterrows()]
    db.upsert_postings(rows)
    n_eval = sum(1 for _, _, e in rows if e)
    n_fraud = sum(1 for _, g, e in rows if e and g == 1)
    print(f"loaded {len(rows)} postings; eval set {n_eval} ({n_fraud} fraudulent)")


ROUTER_SAMPLE = 1000  # balanced: half fraudulent, half legitimate
SEED = 7


def dev_rows(df: pd.DataFrame, sample: int | None = ROUTER_SAMPLE) -> pd.DataFrame:
    """Development rows (never the eval set). `sample` > 0: a balanced draw, sample/2 per class,
    fixed seed. 0 or None: every dev row (17,480, 4.4% fraudulent)."""
    dev = df[~df["job_id"].astype(str).isin(eval_ids())]
    if not sample:
        return dev
    per_class = sample // 2
    parts = [dev[dev["fraudulent"] == c].sample(n=per_class, random_state=SEED) for c in (1, 0)]
    return pd.concat(parts).sample(frac=1.0, random_state=SEED)


def make_router_pipeline(n_rows: int):
    # min_df scales with the data: 3 documents is right for 17k rows, too strict for 1k.
    return make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=2 if n_rows < 5000 else 3, max_features=60000, sublinear_tf=True),
        LogisticRegression(C=4.0, class_weight="balanced", max_iter=2000),
    )


def fit_router(dev: pd.DataFrame):
    texts = [router_text(to_posting(r)) for _, r in dev.iterrows()]
    y = dev["fraudulent"].astype(int).to_numpy()
    x_tr, x_va, y_tr, y_va = train_test_split(texts, y, test_size=0.2, stratify=y, random_state=SEED)
    pipe = make_router_pipeline(len(texts))
    pipe.fit(x_tr, y_tr)
    ap = average_precision_score(y_va, pipe.predict_proba(x_va)[:, 1])
    print(f"router: {len(texts)} dev rows ({int(y.sum())} fraudulent); held-out dev AP {ap:.3f} on {len(y_va)}")
    pipe.fit(texts, y)  # refit on the whole sample; eval rows never seen
    return pipe


def train_router(sample: int | None = ROUTER_SAMPLE) -> None:
    pipe = fit_router(dev_rows(read_dataset(), sample))
    settings = get_settings()
    path = settings.router_path
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, path)
    # Publish to the object store so every API and worker container loads the same model.
    build_store(settings).put_bytes(ROUTER_KEY, path.read_bytes())
    print(f"saved {path} and {ROUTER_KEY} in the {settings.storage_backend} object store")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "load"
    if cmd == "router":
        train_router(int(sys.argv[2]) if len(sys.argv) > 2 else ROUTER_SAMPLE)
    else:
        load()
