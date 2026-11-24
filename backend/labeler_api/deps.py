"""FastAPI dependencies. Routes ask for what they need instead of reaching for globals."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from labeler.config import Settings
from labeler.services.labeling import Service


def get_service(request: Request) -> Service:
    return request.app.state.service


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


ServiceDep = Annotated[Service, Depends(get_service)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
