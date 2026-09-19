"""Shared CLI input helpers."""

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ValidationError


async def stream_stdin() -> AsyncIterator[Any]:
    """Helper to stream JSON objects from stdin."""
    loop = asyncio.get_event_loop()
    line_number = 0
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        line_number += 1
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON on stdin line {line_number}") from e


async def parse_input_stream[TModel: BaseModel](
    arg_json: tuple[str, ...] | list[str] | None = None,
    model_cls: type[TModel] | None = None,
) -> AsyncIterator[Any]:
    """Helper to yield parsed and validated objects/models from --arg-json options or stdin."""
    if arg_json:
        for aj in arg_json:
            try:
                if model_cls:
                    yield model_cls.model_validate_json(aj)
                else:
                    yield json.loads(aj)
            except (json.JSONDecodeError, ValidationError) as e:
                raise ValueError("Invalid --arg-json payload") from e
    else:
        async for item in stream_stdin():
            if model_cls:
                try:
                    yield model_cls.model_validate(item)
                except ValidationError as e:
                    raise ValueError("Invalid JSON input from stdin") from e
            else:
                yield item
