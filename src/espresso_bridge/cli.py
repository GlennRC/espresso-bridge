"""CLI tool for espresso-bridge.

Usage:
    espresso-bridge scan              # Find ShotStopper devices
    espresso-bridge status            # Read current ShotStopper state
    espresso-bridge set-weight 36     # Set target brew weight
    espresso-bridge settings          # Show all settings
    espresso-bridge config --auto-tare true --drip-delay 3
"""

from __future__ import annotations

import asyncio
import json
import logging

import typer

from espresso_bridge.ble.shotstopper import ShotStopperAdapter

app = typer.Typer(
    name="espresso-bridge",
    help="Controller bridge for ShotStopper + La Marzocco Linea Micra",
    no_args_is_help=True,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging")):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def scan(timeout: float = typer.Option(8.0, help="Scan duration in seconds")):
    """Scan for ShotStopper BLE devices."""

    async def _scan():
        typer.echo(f"Scanning for ShotStopper devices ({timeout}s)...")
        devices = await ShotStopperAdapter.scan(timeout=timeout)
        if not devices:
            typer.echo("No ShotStopper devices found.")
            raise typer.Exit(1)
        for d in devices:
            typer.echo(f"  ☕ {d.name or 'Unknown'}  ({d.address})")
        typer.echo(f"\nFound {len(devices)} device(s).")

    _run(_scan())


@app.command()
def status(address: str = typer.Option("", help="BLE address (auto-scan if empty)")):
    """Read current ShotStopper state."""

    async def _status():
        adapter = ShotStopperAdapter()
        addr = address or None
        typer.echo("Connecting to ShotStopper...")
        if not await adapter.connect(address=addr):
            typer.echo("Failed to connect.", err=True)
            raise typer.Exit(1)

        state = adapter.state
        typer.echo(f"\n{'─' * 40}")
        typer.echo("  ShotStopper Status")
        typer.echo(f"{'─' * 40}")
        typer.echo("  Connected:      ✅")
        typer.echo(f"  Enabled:        {'✅' if state.enabled else '❌'}")
        typer.echo(f"  Target Weight:  {state.weight_target}g")
        scale = "🟢 Connected" if state.scale_status == 1 else "🔴 Disconnected"
        typer.echo(f"  Scale:          {scale}")
        typer.echo(f"  Brewing:        {'☕ Active' if state.shot_active else '⏸  Idle'}")
        typer.echo(f"  Firmware:       v{state.firmware_version}")
        typer.echo(f"{'─' * 40}")

        await adapter.disconnect()

    _run(_status())


@app.command()
def settings(address: str = typer.Option("", help="BLE address (auto-scan if empty)")):
    """Show all ShotStopper settings."""

    async def _settings():
        adapter = ShotStopperAdapter()
        addr = address or None
        typer.echo("Connecting to ShotStopper...")
        if not await adapter.connect(address=addr):
            typer.echo("Failed to connect.", err=True)
            raise typer.Exit(1)

        state = adapter.state
        typer.echo(f"\n{'─' * 40}")
        typer.echo("  ShotStopper Configuration")
        typer.echo(f"{'─' * 40}")
        typer.echo(f"  Enabled:          {'Yes' if state.enabled else 'No'}")
        typer.echo(f"  Target Weight:    {state.weight_target}g")
        typer.echo(f"  Auto Tare:        {'Yes' if state.auto_tare else 'No'}")
        typer.echo(f"  Momentary Switch: {'Yes' if state.momentary else 'No'}")
        typer.echo(f"  Reed Switch:      {'Yes' if state.reed_switch else 'No'}")
        typer.echo(f"  Min Shot:         {state.min_shot_duration}s")
        typer.echo(f"  Max Shot:         {state.max_shot_duration}s")
        typer.echo(f"  Drip Delay:       {state.drip_delay}s")
        if state.wifi_ssid:
            typer.echo(f"  WiFi SSID:        {state.wifi_ssid}")
        if state.wifi_ip:
            typer.echo(f"  WiFi IP:          {state.wifi_ip}")
        typer.echo(f"{'─' * 40}")

        await adapter.disconnect()

    _run(_settings())


@app.command(name="set-weight")
def set_weight(
    grams: int = typer.Argument(..., min=10, max=200, help="Target weight in grams"),
    address: str = typer.Option("", help="BLE address (auto-scan if empty)"),
):
    """Set the target brew weight."""

    async def _set():
        adapter = ShotStopperAdapter()
        addr = address or None
        typer.echo("Connecting to ShotStopper...")
        if not await adapter.connect(address=addr):
            typer.echo("Failed to connect.", err=True)
            raise typer.Exit(1)

        old = adapter.state.weight_target
        if await adapter.set_weight(grams):
            typer.echo(f"✅ Target weight: {old}g → {grams}g")
        else:
            typer.echo("❌ Failed to set weight.", err=True)
            raise typer.Exit(1)

        await adapter.disconnect()

    _run(_set())


@app.command()
def config(
    address: str = typer.Option("", help="BLE address (auto-scan if empty)"),
    enabled: bool | None = typer.Option(None, help="Enable/disable brew-by-weight"),
    auto_tare: bool | None = typer.Option(None, "--auto-tare", help="Auto-tare on shot start"),
    momentary: bool | None = typer.Option(None, help="Momentary switch mode"),
    reed_switch: bool | None = typer.Option(None, "--reed-switch", help="Reed switch mode"),
    min_shot: int | None = typer.Option(None, "--min-shot", help="Min shot duration (seconds)"),
    max_shot: int | None = typer.Option(None, "--max-shot", help="Max shot duration (seconds)"),
    drip_delay: int | None = typer.Option(None, "--drip-delay", help="Drip delay (seconds)"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """View or update ShotStopper configuration."""
    from espresso_bridge.core.models import ShotStopperConfig

    async def _config():
        adapter = ShotStopperAdapter()
        addr = address or None
        typer.echo("Connecting to ShotStopper...")
        if not await adapter.connect(address=addr):
            typer.echo("Failed to connect.", err=True)
            raise typer.Exit(1)

        # Build config from provided options
        update = ShotStopperConfig(
            enabled=enabled,
            weight_target=None,  # Use set-weight command for this
            auto_tare=auto_tare,
            momentary=momentary,
            reed_switch=reed_switch,
            min_shot_duration=min_shot,
            max_shot_duration=max_shot,
            drip_delay=drip_delay,
        )

        has_updates = any(v is not None for v in update.model_dump().values())
        if has_updates:
            if await adapter.apply_config(update):
                typer.echo("✅ Configuration updated")
            else:
                typer.echo("❌ Some settings failed to update", err=True)

        state = adapter.state
        if as_json:
            typer.echo(json.dumps(state.model_dump(), indent=2))
        else:
            typer.echo(f"\nCurrent config: {state.model_dump()}")

        await adapter.disconnect()

    _run(_config())


if __name__ == "__main__":
    app()
