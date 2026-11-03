from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from jambachat.config import get_settings
from jambachat.db.models import Base

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    db_file = tmp_path / "migrations.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["sync_url"] = f"sqlite:///{db_file.as_posix()}"
    yield config
    get_settings.cache_clear()


def test_upgrade_creates_all_tables_and_matches_models(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    engine = create_engine(alembic_config.attributes["sync_url"])
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()

    assert {"users", "messages", "reminders", "expenses"} <= tables
    assert diff == []


def test_downgrade_removes_all_tables(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    engine = create_engine(alembic_config.attributes["sync_url"])
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
    engine.dispose()

    assert tables <= {"alembic_version"}
