"""Optional add-ons, installed as separate Python packages.

An add-on is a package that declares an entry point in the ``notoryx_ops.addons`` group.
The entry point is a callable that takes an :class:`AddonContext` and returns an object with:

  id        short name, used in URLs and the config file ([addons.<id>])
  name      shown in the dashboard
  version   its own version
  license   SPDX id, shown on the Station page
  router    optional FastAPI APIRouter, served under /api/addons/<id>
  page_dir  optional folder of static files, served at /addons/<id>/
  start() / stop() / info()

Add-ons keep their own licence. NOTORYX Ops itself stays MIT whatever is installed beside it.
Settings live in the station's config file under [addons.<id>] and may hold strings, numbers,
booleans and lists of those.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

log = logging.getLogger(__name__)
GROUP = "notoryx_ops.addons"


@dataclass
class AddonContext:
    station: Any                         # the running Station
    settings: dict[str, Any]             # this add-on's [addons.<id>] table (a live dict)
    save_settings: Callable[[], None]    # write the config file after changing settings
    require: Callable[[Any, str], Any]   # require(request, "viewer" | "operator" | "admin")


def discover() -> list[tuple[str, Callable]]:
    try:
        eps = entry_points(group=GROUP)
    except TypeError:  # pragma: no cover - Python 3.9 style
        eps = entry_points().get(GROUP, [])
    out = []
    for ep in eps:
        try:
            out.append((ep.name, ep.load()))
        except Exception as exc:  # noqa: BLE001 - a broken add-on must not stop the station
            log.warning("couldn't load add-on %s: %s", ep.name, exc)
    return out


def load(station: Any, factories: list[tuple[str, Callable]] | None = None) -> list[Any]:
    """Create every installed add-on that isn't turned off in the config."""
    cfg = station.cfg
    loaded = []
    for name, factory in (discover() if factories is None else factories):
        if name in cfg.addons_disabled:
            log.info("add-on %s is turned off", name)
            continue
        table = cfg.addons.setdefault(name, {})

        def save(_station=station) -> None:
            from . import settings

            settings.save(_station.cfg)

        ctx = AddonContext(station=station, settings=table, save_settings=save, require=station.auth.require)
        try:
            addon = factory(ctx)
        except Exception as exc:  # noqa: BLE001
            log.warning("add-on %s failed to start: %s", name, exc)
            continue
        loaded.append(addon)
        log.info("add-on %s %s (%s) loaded", addon.name, addon.version, addon.license)
    return loaded


def describe(addon: Any) -> dict[str, Any]:
    info = {}
    try:
        info = addon.info() or {}
    except Exception as exc:  # noqa: BLE001
        info = {"error": str(exc)}
    return {"id": addon.id, "name": addon.name, "version": addon.version, "license": addon.license,
            "page": f"/addons/{addon.id}/" if getattr(addon, "page_dir", None) else None, "info": info}
