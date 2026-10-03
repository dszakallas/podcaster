import contextlib
from collections.abc import Generator
from pathlib import Path

import pytest
from dbos import DBOS, DBOSConfig


@pytest.fixture(scope="session")
def _dbos_test_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    test_db_dir = tmp_path_factory.mktemp("dbos")
    return test_db_dir / "test_dbos.sqlite"


@pytest.fixture()
def reset_dbos(_dbos_test_db_path: Path) -> Generator[None, None, None]:
    """Provide a clean, isolated DBOS runtime for each test."""
    with contextlib.suppress(Exception):
        DBOS.destroy()

    config = DBOSConfig(
        name="podcaster",
        application_version="0.1.0",
        system_database_url=f"sqlite:///{_dbos_test_db_path}",
        run_admin_server=False,
        enable_otlp=False,
    )
    DBOS(config=config)
    DBOS.reset_system_database(truncate=True)
    DBOS.launch()

    yield

    with contextlib.suppress(Exception):
        DBOS.destroy()


# Alias for backward compatibility with existing tests
dbos_session = reset_dbos
