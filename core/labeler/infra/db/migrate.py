"""Run Alembic migrations programmatically (startup, tests, the compose `init` service)."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

_CORE = Path(__file__).resolve().parents[3]  # core/: alembic.ini and migrations/


def alembic_config(url: str) -> Config:
    cfg = Config(str(_CORE / "alembic.ini"))
    cfg.set_main_option("script_location", str(_CORE / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(engine: Engine) -> None:
    logging.getLogger("alembic").setLevel(logging.WARNING)  # one line per start, not twenty
    cfg = alembic_config(engine.url.render_as_string(hide_password=False))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
