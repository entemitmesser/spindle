from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spindle.config import ConfigStore
from spindle.models.event import Event


async def test_get_returns_defaults(config_store: ConfigStore) -> None:
    config = await config_store.get()
    assert config.rip.backend == "makemkv"
    assert config.transcode.workers == 1
    assert config.behaviour.identify_confidence_threshold == 0.75


async def test_get_returns_independent_snapshots(config_store: ConfigStore) -> None:
    first = await config_store.get()
    second = await config_store.get()
    assert first is not second
    assert first == second


async def test_update_deep_merges_partial_leaving_siblings_untouched(
    config_store: ConfigStore,
) -> None:
    updated = await config_store.update({"rip": {"backend": "handbrake"}})
    assert updated.rip.backend == "handbrake"
    assert updated.rip.title_selection == "longest"

    fetched = await config_store.get()
    assert fetched.rip.backend == "handbrake"


async def test_update_rejects_bad_enum_value(config_store: ConfigStore) -> None:
    with pytest.raises(ValidationError):
        await config_store.update({"rip": {"backend": "not-a-real-backend"}})

    # the rejected update must not have been applied
    config = await config_store.get()
    assert config.rip.backend == "makemkv"


async def test_update_emits_exactly_one_settings_updated_event(
    config_store: ConfigStore, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await config_store.update({"behaviour": {"auto_eject": False}})

    async with session_factory() as session:
        result = await session.execute(select(Event).where(Event.type == "settings.updated"))
        events = result.scalars().all()

    assert len(events) == 1
    assert events[0].data == {"keys": ["behaviour"]}


# -- phase-06: naming templates are validated at settings-save time --------


async def test_update_rejects_a_naming_template_with_an_unknown_variable(
    config_store: ConfigStore,
) -> None:
    with pytest.raises(ValidationError):
        await config_store.update({"library": {"movie_file_template": "{title} {nonexistent}"}})

    config = await config_store.get()
    assert config.library.movie_file_template == "{title} ({year}) - {source}-{resolution}"


async def test_update_rejects_a_naming_template_with_a_bad_format_spec(
    config_store: ConfigStore,
) -> None:
    with pytest.raises(ValidationError):
        await config_store.update({"library": {"movie_dir_template": "{title:02d}"}})


async def test_update_accepts_a_valid_custom_naming_template(config_store: ConfigStore) -> None:
    updated = await config_store.update({"library": {"movie_file_template": "{title} [{tmdb_id}]"}})
    assert updated.library.movie_file_template == "{title} [{tmdb_id}]"
