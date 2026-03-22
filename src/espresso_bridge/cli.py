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


# ── La Marzocco subcommands ──────────────────────────────────────────

lm_app = typer.Typer(
    name="lm",
    help="La Marzocco Linea Micra controls",
    no_args_is_help=True,
)
app.add_typer(lm_app, name="lm")


def _load_lm_config():
    """Load LM credentials from config.yaml."""
    from pathlib import Path

    import yaml

    config_path = Path("config.yaml")
    if not config_path.exists():
        typer.echo(
            "❌ config.yaml not found. Copy config.example.yaml and add LM credentials.",
            err=True,
        )
        raise typer.Exit(1)

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    lm = cfg.get("lamarzocco", {})
    username = lm.get("username", "")
    serial = lm.get("serial_number", "")
    key = lm.get("communication_key", "")

    if not all([username, serial, key]):
        typer.echo(
            "❌ Missing lamarzocco credentials in config.yaml "
            "(username, serial_number, communication_key)",
            err=True,
        )
        raise typer.Exit(1)

    return username, serial, key


@lm_app.command(name="scan")
def lm_scan(timeout: float = typer.Option(8.0, help="Scan duration in seconds")):
    """Scan for La Marzocco machines."""
    from espresso_bridge.ble.lamarzocco import LaMarzoccoAdapter

    async def _scan():
        typer.echo(f"Scanning for La Marzocco machines ({timeout}s)...")
        devices = await LaMarzoccoAdapter.scan(timeout=timeout)
        if not devices:
            typer.echo("No La Marzocco machines found.")
            raise typer.Exit(1)
        for d in devices:
            typer.echo(f"  ☕ {d.name or 'Unknown'}  ({d.address})")
        typer.echo(f"\nFound {len(devices)} machine(s).")

    _run(_scan())


@lm_app.command(name="status")
def lm_status(address: str = typer.Option("", help="BLE address (auto-scan if empty)")):
    """Read La Marzocco machine status."""
    from espresso_bridge.ble.lamarzocco import LaMarzoccoAdapter

    async def _status():
        username, serial, key = _load_lm_config()
        adapter = LaMarzoccoAdapter(username, serial, key)
        addr = address or None

        typer.echo("Connecting to La Marzocco...")
        if not await adapter.connect_silent(address=addr):
            typer.echo("Failed to connect.", err=True)
            raise typer.Exit(1)

        state = adapter.state
        typer.echo(f"\n{'─' * 40}")
        typer.echo("  La Marzocco Linea Micra")
        typer.echo(f"{'─' * 40}")
        typer.echo("  Connected:     ✅")
        typer.echo(f"  Power:         {'🟢 On' if state.turned_on else '🔴 Off'}")
        typer.echo(f"  Coffee Temp:   {state.coffee_temp_target}°C")
        typer.echo(f"  Steam:         {'🟢 On' if state.steam_enabled else '🔴 Off'}")
        typer.echo(f"  Steam Level:   {state.steam_level}")
        typer.echo(f"{'─' * 40}")

        await adapter.disconnect()

    _run(_status())


@lm_app.command(name="power")
def lm_power(
    on: bool = typer.Option(None, "--on/--off", help="Turn machine on or off"),
):
    """Control machine power."""
    from espresso_bridge.ble.lamarzocco import LaMarzoccoAdapter

    if on is None:
        typer.echo("Specify --on or --off", err=True)
        raise typer.Exit(1)

    async def _power():
        username, serial, key = _load_lm_config()
        adapter = LaMarzoccoAdapter(username, serial, key)
        typer.echo("Connecting to La Marzocco...")
        if not await adapter.connect_silent():
            typer.echo("Failed to connect.", err=True)
            raise typer.Exit(1)

        if await adapter.set_power(on):
            typer.echo(f"✅ Machine powered {'on' if on else 'off'}")
        else:
            typer.echo("❌ Failed to set power", err=True)
            raise typer.Exit(1)

        await adapter.disconnect()

    _run(_power())


@lm_app.command(name="temp")
def lm_temp(
    celsius: float = typer.Argument(..., min=85.0, max=104.0, help="Brew boiler temp (85–104°C)"),
):
    """Set brew boiler temperature."""
    from espresso_bridge.ble.lamarzocco import LaMarzoccoAdapter

    async def _temp():
        username, serial, key = _load_lm_config()
        adapter = LaMarzoccoAdapter(username, serial, key)
        typer.echo("Connecting to La Marzocco...")
        if not await adapter.connect_silent():
            typer.echo("Failed to connect.", err=True)
            raise typer.Exit(1)

        if await adapter.set_coffee_temp(celsius):
            typer.echo(f"✅ Coffee boiler target: {celsius}°C")
        else:
            typer.echo("❌ Failed to set temperature", err=True)
            raise typer.Exit(1)

        await adapter.disconnect()

    _run(_temp())


@lm_app.command(name="steam")
def lm_steam(
    level: int = typer.Option(None, "--level", min=1, max=3, help="Steam level (1–3)"),
    on: bool = typer.Option(None, "--on/--off", help="Enable/disable steam boiler"),
):
    """Control steam boiler."""
    from espresso_bridge.ble.lamarzocco import LaMarzoccoAdapter

    if level is None and on is None:
        typer.echo("Specify --level or --on/--off", err=True)
        raise typer.Exit(1)

    async def _steam():
        username, serial, key = _load_lm_config()
        adapter = LaMarzoccoAdapter(username, serial, key)
        typer.echo("Connecting to La Marzocco...")
        if not await adapter.connect_silent():
            typer.echo("Failed to connect.", err=True)
            raise typer.Exit(1)

        if on is not None:
            if await adapter.set_steam_enabled(on):
                typer.echo(f"✅ Steam boiler {'enabled' if on else 'disabled'}")
            else:
                typer.echo("❌ Failed to set steam", err=True)

        if level is not None:
            if await adapter.set_steam_level(level):
                typer.echo(f"✅ Steam level: {level}")
            else:
                typer.echo("❌ Failed to set steam level", err=True)

        await adapter.disconnect()

    _run(_steam())


if __name__ == "__main__":
    app()
