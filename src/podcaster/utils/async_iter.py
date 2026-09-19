"""Async iterator helper to convert single items or sync iterables into async iterators."""

from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from typing import cast

from aiostream import stream
from pydantic import BaseModel


async def async_iter[T](
    source: AsyncIterable[T] | Iterable[T] | T,
    *extra: T,
) -> AsyncIterator[T]:
    """Turn a single item, sync iterable, or multiple arguments into an async iterator.

    - If multiple arguments are passed, yields each argument in order.
    - If passed str, bytes, Mapping, or Pydantic BaseModel, treats it as a single item and yields it.
    - If passed an existing AsyncIterable or Iterable, iterates using aiostream.
    - Otherwise, treats the input as a single item and yields it.
    """
    if extra:
        yield cast(T, source)
        for item in extra:
            yield item
        return

    if isinstance(source, (str, bytes, Mapping, BaseModel)):
        yield cast(T, source)
    elif isinstance(source, (AsyncIterable, Iterable)):
        s = stream.iterate(source)
        async with s.stream() as streamer:
            async for item in streamer:
                yield item
    else:
        yield source
