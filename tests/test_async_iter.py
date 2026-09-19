"""Unit tests for podcaster.utils.async_iter."""

import pytest
from pydantic import BaseModel

from podcaster.utils import async_iter


async def to_list(aiter):
    result = []
    async for item in aiter:
        result.append(item)
    return result


@pytest.mark.anyio
async def test_async_iter_single_item():
    class Dummy:
        pass

    obj = Dummy()
    assert await to_list(async_iter(obj)) == [obj]
    assert await to_list(async_iter(42)) == [42]


@pytest.mark.anyio
async def test_async_iter_pydantic_model():
    class SampleModel(BaseModel):
        title: str
        count: int

    model = SampleModel(title="Podcast", count=1)
    assert await to_list(async_iter(model)) == [model]


@pytest.mark.anyio
async def test_async_iter_string_and_bytes():
    assert await to_list(async_iter("hello world")) == ["hello world"]
    assert await to_list(async_iter(b"bytes content")) == [b"bytes content"]


@pytest.mark.anyio
async def test_async_iter_dict():
    d = {"key": "value", "count": 2}
    assert await to_list(async_iter(d)) == [d]


@pytest.mark.anyio
async def test_async_iter_list_and_tuple():
    assert await to_list(async_iter([1, 2, 3])) == [1, 2, 3]
    assert await to_list(async_iter((10, 20))) == [10, 20]
    assert await to_list(async_iter([])) == []


@pytest.mark.anyio
async def test_async_iter_sync_generator():
    def gen():
        yield "a"
        yield "b"

    assert await to_list(async_iter(gen())) == ["a", "b"]


@pytest.mark.anyio
async def test_async_iter_multiple_args():
    assert await to_list(async_iter(1, 2, 3)) == [1, 2, 3]


@pytest.mark.anyio
async def test_async_iter_existing_async_iterable():
    async def agen():
        yield 100
        yield 200

    assert await to_list(async_iter(agen())) == [100, 200]
