import contextlib
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor

import pytest
from dbos import DBOS, DBOSConfig
from dbos._dbos import _get_dbos_instance


@pytest.fixture(scope="session")
def dbos_session(tmp_path_factory) -> Generator[None, None, None]:
    """Explicit fixture for tests that require DBOS runtime with an isolated temporary SQLite database."""
    test_db_dir = tmp_path_factory.mktemp("dbos")
    test_db = test_db_dir / "test_dbos.sqlite"

    with contextlib.suppress(Exception):
        DBOS.destroy()

    DBOS(
        config=DBOSConfig(
            name="podcaster",
            system_database_url=f"sqlite:///{test_db}",
            run_admin_server=False,
            enable_otlp=False,
        )
    )
    DBOS.launch()

    yield

    with contextlib.suppress(Exception):
        DBOS.destroy()


@pytest.fixture(autouse=True)
def _reset_dbos_executor() -> Generator[None, None, None]:
    """Reset DBOS's ThreadPoolExecutor after each test.

    asyncio.run() shuts down whichever executor is set as the loop's default
    executor when the loop closes. DBOS sets its own ThreadPoolExecutor as the
    default executor via _configure_asyncio_thread_pool(), so after the first
    asyncio.run() completes, that executor is dead. Setting _executor_field to
    None lets DBOS lazily create a fresh one for the next test's event loop.
    """
    yield
    with contextlib.suppress(Exception):
        instance = _get_dbos_instance()
        instance._executor_field = ThreadPoolExecutor(
            thread_name_prefix="dbos-executor-"
        )
