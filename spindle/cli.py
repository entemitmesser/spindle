"""`spindle` console script, per phase-09-hardening.md task 3.

The Docker image's `CMD` invokes uvicorn directly (see the Dockerfile), not
`serve` here -- `serve` exists for parity so `uv run spindle serve` works the
same way outside the container. The other commands are offline maintenance
operations meant to be run with the server stopped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from spindle.config import ConfigStore, get_settings
from spindle.db import create_engine, create_session_factory, init_schema
from spindle.services.backup import build_backup_zip, restore_backup_sync
from spindle.services.events import EventBus
from spindle.services.library_scan import scan_library

app = typer.Typer(add_completion=False, help="Spindle: self-hosted automatic disc ripper.")


@app.command()
def serve() -> None:
    """Run the Spindle server (what the Docker image's CMD runs)."""
    from spindle.main import main as run_server

    run_server()


@app.command()
def migrate() -> None:
    """Bring $SPINDLE_DATA/spindle.db up to the latest schema."""
    settings = get_settings()
    engine = create_engine(settings)
    asyncio.run(init_schema(engine, settings))
    typer.echo("Database is up to date.")


@app.command()
def backup(
    output: Path = typer.Option(..., "--output", "-o", help="Path to write the backup zip to."),
) -> None:
    """Write a spindle.db + settings + presets backup zip to OUTPUT."""
    settings = get_settings()

    async def _run() -> bytes:
        session_factory = create_session_factory(create_engine(settings))
        config_store = ConfigStore(session_factory, EventBus(session_factory))
        runtime_config = await config_store.get()
        return await build_backup_zip(
            db_path=settings.data_dir / "spindle.db",
            runtime_config=runtime_config,
            presets_dir=settings.data_dir / "presets",
        )

    zip_bytes = asyncio.run(_run())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(zip_bytes)
    typer.echo(f"Wrote {len(zip_bytes)} bytes to {output}")


@app.command()
def restore(
    archive: Path = typer.Argument(..., help="Backup zip produced by `spindle backup`."),
) -> None:
    """Restore spindle.db and presets from ARCHIVE. Overwrites the current database."""
    settings = get_settings()
    typer.confirm(f"This overwrites {settings.data_dir / 'spindle.db'}. Continue?", abort=True)
    restore_backup_sync(archive, data_dir=settings.data_dir)
    typer.echo("Restore complete. Restart Spindle to pick up the restored database.")


@app.command("scan-library")
def scan_library_command() -> None:
    """Adopt files already in the library into job history (best-effort)."""
    settings = get_settings()

    async def _run() -> int:
        session_factory = create_session_factory(create_engine(settings))
        return await scan_library(session_factory, library_dir=settings.library_dir)

    adopted = asyncio.run(_run())
    typer.echo(f"Adopted {adopted} file(s) into job history.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
