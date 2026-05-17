"""Parlant Cloud observability module.

Auto-loaded by the Server when PARLANT_CLOUD_API_KEY is set.
Validates the API key, resolves project context, and sets up
ParlantCloudTracer / ParlantCloudLogger / ParlantCloudMeter.
"""

import os
from contextlib import AsyncExitStack

import httpx
from lagom import Container

from parlant.core.loggers import CompositeLogger, Logger
from parlant.core.meter import Meter
from parlant.core.tracer import CompositeTracer, Tracer

_exit_stack = AsyncExitStack()


async def configure_container(container: Container) -> Container:
    api_key = os.environ.get("PARLANT_CLOUD_API_KEY", "")
    if not api_key:
        return container

    logger = container[Logger]
    api_url = os.environ.get("PARLANT_CLOUD_OTEL_URL", "https://api.parlant.cloud")

    auth_url = f"{api_url}/v1/auth/api-key"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(auth_url, headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            auth_data = resp.json()
            project_id: str = auth_data.get("project_id", "")
    except Exception:
        logger.warning("Parlant Cloud API key validation failed; observability disabled")
        return container

    if not project_id:
        logger.warning("Parlant Cloud auth response missing project_id; observability disabled")
        return container

    from parlant.adapters.observability import (
        ParlantCloudLogger,
        ParlantCloudMeter,
        ParlantCloudTracer,
    )

    tracer = container[Tracer]
    cloud_tracer = await _exit_stack.enter_async_context(ParlantCloudTracer(project_id=project_id))
    if isinstance(tracer, CompositeTracer):
        tracer.append(cloud_tracer)
    else:
        container.define(Tracer, CompositeTracer([tracer, cloud_tracer]))

    existing_logger = container[Logger]
    cloud_logger = await _exit_stack.enter_async_context(
        ParlantCloudLogger(tracer=tracer, project_id=project_id)
    )
    if isinstance(existing_logger, CompositeLogger):
        existing_logger.append(cloud_logger)
    else:
        container.define(Logger, CompositeLogger([existing_logger, cloud_logger]))

    try:
        _ = container[Meter]
    except Exception:
        cloud_meter = await _exit_stack.enter_async_context(
            ParlantCloudMeter(project_id=project_id)
        )
        container[Meter] = cloud_meter

    return container
