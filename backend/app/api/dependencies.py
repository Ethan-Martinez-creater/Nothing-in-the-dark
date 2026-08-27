from __future__ import annotations

from typing import cast

from fastapi import Request

from app.bootstrap import ApplicationContainer


def get_container(request: Request) -> ApplicationContainer:
    return cast(ApplicationContainer, request.app.state.container)
