"""The router's training sample: balanced, deterministic, never the eval set."""

import pandas as pd

import scripts.data as data


def test_balanced_sample_excludes_eval_and_is_deterministic(monkeypatch):
    df = pd.DataFrame({"job_id": range(1, 1201), "fraudulent": [1 if i % 10 == 0 else 0 for i in range(1, 1201)]})
    eval_set = {str(i) for i in range(1, 101)}
    monkeypatch.setattr(data, "eval_ids", lambda: eval_set)

    a = data.dev_rows(df, 200)
    assert len(a) == 200 and a["fraudulent"].sum() == 100
    assert not set(a["job_id"].astype(str)) & eval_set
    assert a["job_id"].tolist() == data.dev_rows(df, 200)["job_id"].tolist()  # fixed seed

    full = data.dev_rows(df, 0)
    assert len(full) == 1100 and not set(full["job_id"].astype(str)) & eval_set
