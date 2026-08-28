from typing import Generator

import pytest
from dbos import DBOS, DBOSConfig


@pytest.fixture
def dbos_session(tmp_path_factory) -> Generator[None, None, None]:
    """Explicit fixture for tests that require DBOS runtime with an isolated temporary SQLite database."""
    test_db_dir = tmp_path_factory.mktemp("dbos")
    test_db = test_db_dir / "test_dbos.sqlite"

    try:
        DBOS.destroy()
    except Exception:
        pass

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

    try:
        DBOS.destroy()
    except Exception:
        pass
