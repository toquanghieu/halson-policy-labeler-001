from __future__ import annotations

from pathlib import Path

import pytest

from labeler.config import REPO_ROOT, Settings
from labeler.labeling.policy import parse_policy
from labeler.labeling.schemas import Posting


@pytest.fixture
def policy_v1():
    return parse_policy((REPO_ROOT / "policies" / "v1.yaml").read_text())


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # tests never read the developer's .env
        data_dir=tmp_path,
        llm_provider="fake",
        tier1_model="fake-small",
        tier2_model="fake-large",
        sync_label_wait_s=5.0,  # POST /api/postings with no worker answers 202 after this; keep tests quick
    )


@pytest.fixture
def scam_posting() -> Posting:
    return Posting(
        job_id="t1",
        title="Payment Processing Agent",
        company_profile="",
        description=(
            "Work from home!  Earn $3,000 per week, no experience needed.\n"
            "You will receive client payments into your account and forward them via Western Union. "
            "To complete the application, email your bank account details to #EMAIL_abc123#."
        ),
        requirements="Must be 18+.",
        benefits="Flexible hours.",
        telecommuting=True,
    )


@pytest.fixture
def plain_posting() -> Posting:
    return Posting(
        job_id="t2",
        title="Backend Engineer",
        company_profile="Acme builds billing software for clinics.",
        description="Design and run our payments API in Python.",
        requirements="3+ years Python.",
        benefits="Health insurance.",
        has_company_logo=True,
        salary_range="90000-120000",
    )


# ---- real Postgres for repository tests (skipped when Docker is unavailable) ----------
@pytest.fixture(scope="session")
def postgres_server():
    try:
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer("postgres:16-alpine", driver="psycopg")
        container.start()
    except Exception as e:  # noqa: BLE001  (no Docker daemon, image pull blocked, ...)
        pytest.skip(f"Postgres container unavailable: {e}")
    yield container
    container.stop()


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path: Path) -> str:
    """Each repository test runs against both engines: SQLite (dev/tests) and Postgres (compose/AWS)."""
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'test.db'}"
    import uuid

    from sqlalchemy import create_engine, text

    server = request.getfixturevalue("postgres_server")
    admin_url = server.get_connection_url()
    name = f"t_{uuid.uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return admin_url.rsplit("/", 1)[0] + f"/{name}"


@pytest.fixture(autouse=True)
def _dispose_engines():
    """Close every engine a test opened (SQLite connections otherwise linger until GC)."""
    from sqlalchemy.engine import Engine

    created: list[Engine] = []
    import labeler.infra.db.repository as repo

    real = repo.make_engine

    def tracking(*a, **kw):
        e = real(*a, **kw)
        created.append(e)
        return e

    repo.make_engine = tracking
    import labeler.bootstrap as boot

    boot_real = boot.make_engine
    boot.make_engine = tracking
    yield
    repo.make_engine, boot.make_engine = real, boot_real
    for e in created:
        e.dispose()
