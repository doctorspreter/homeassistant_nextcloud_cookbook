"""Cookbook for Home Assistant, backed by local files or Nextcloud."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
from urllib.parse import quote

import aiohttp
from aiohttp import web
from homeassistant.components.frontend import async_register_built_in_panel, async_remove_panel
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.helpers import entity_registry as er

from .backend import BackendError, LocalBackend, NextcloudBackend
from .const import CONF_MODE, CONF_TODO_ENTITY, DOMAIN, LOCAL, settings

_LOGGER = logging.getLogger(__name__)


def resolve_todo(hass, config):
    if config.get(CONF_TODO_ENTITY):
        return config[CONF_TODO_ENTITY]
    todos = [e for e in er.async_get(hass).entities.values() if e.domain == "todo"]
    return next((e.entity_id for e in todos if e.platform == "shopping_list"), todos[0].entity_id if todos else "")


async def async_setup(hass, config):
    return True


async def async_setup_entry(hass, entry):
    state = hass.data.setdefault(DOMAIN, {})
    if state.get("entry_id") not in (None, entry.entry_id):
        _LOGGER.error("Only one Cookbook entry can own the shared panel")
        return False
    config = settings(entry)
    storage_id = config.get("storage_id", entry.entry_id)
    local = state.get("local") if state.get("storage_id") == storage_id else None
    local = local or LocalBackend(hass, storage_id)
    await local.run(local.store.load)  # Fail visibly on corrupt data; never overwrite it.
    state.update(entry_id=entry.entry_id, local=local, storage_id=storage_id,
                 backend=local if config[CONF_MODE] == LOCAL else NextcloudBackend(hass, config),
                 mode=config[CONF_MODE], todo_entity=resolve_todo(hass, config))
    if not state.get("registered"):
        hass.http.register_view(CookbookView(hass))
        await hass.http.async_register_static_paths([
            StaticPathConfig("/nc_cookbook_panel", str(Path(__file__).parent / "panel"), cache_headers=False),
        ])
        state["registered"] = True
    if "nc-cookbook" not in hass.data.get("frontend_panels", {}):
        async_register_built_in_panel(
            hass, component_name="iframe", sidebar_title="Cookbook", sidebar_icon="mdi:chef-hat",
            frontend_url_path="nc-cookbook", config={"url": "/nc_cookbook_panel/index.html?v=200"},
            require_admin=False,
        )
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    state = hass.data.get(DOMAIN, {})
    state.pop("backend", None)
    state.pop("entry_id", None)
    async_remove_panel(hass, "nc-cookbook", warn_if_unknown=False)
    return True


class CookbookView(HomeAssistantView):
    url = "/api/nc_cookbook/{path:.*}"
    name = "api:nc_cookbook"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def handle(self, request, path, method):
        state = self.hass.data.get(DOMAIN, {})
        backend = state.get("backend")
        if backend is None:
            return self.json({"error": "Cookbook is not loaded"}, status_code=503)
        if method == "GET" and path == "__config__":
            return self.json({"mode": state["mode"], "todo_entity": state["todo_entity"]})
        # An allowlist prevents arbitrary Nextcloud endpoints and path traversal.
        is_recipe = bool(re.fullmatch(r"recipes/[A-Za-z0-9_-]+", path))
        is_image = bool(re.fullmatch(r"recipes/[A-Za-z0-9_-]+/image", path))
        is_category = path.startswith("category/") and len(path) > 9
        allowed = (
            (method == "GET" and (path in {"recipes", "categories"} or is_recipe or is_image or is_category))
            or (method == "POST" and path in {"recipes", "import", "__shopping__"})
            or (method in {"PUT", "DELETE"} and is_recipe)
        )
        if not allowed or ".." in path:
            return self.json({"error": "Unknown endpoint"}, status_code=404)
        try:
            data = await request.json() if method in {"POST", "PUT"} else None
            if data is not None and not isinstance(data, dict):
                raise ValueError("Expected an object")
            if path == "__shopping__":
                entity = state.get("todo_entity")
                item = str(data.get("item", "")).strip()
                if not item or not entity:
                    raise ValueError("Choose a shopping list in Cookbook settings")
                await self.hass.services.async_call("todo", "add_item", {"entity_id": entity, "item": item}, blocking=True)
                return self.json({"ok": True})
            if is_image:
                body, content_type = await backend.image(path.split("/")[1])
                return web.Response(body=body, content_type=content_type, headers={"Cache-Control": "private, max-age=300"})
            if is_category and isinstance(backend, NextcloudBackend):
                path = "category/" + quote(path[9:], safe="")
            result = await backend.request(method, path, data)
            return web.Response(status=204) if method == "DELETE" else self.json(result)
        except BackendError as error:
            return self.json({"error": str(error)}, status_code=error.status)
        except (ValueError, TypeError, KeyError):
            return self.json({"error": "Invalid recipe, URL or unsupported website/image"}, status_code=400)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return self.json({"error": "Download or Nextcloud connection failed"}, status_code=502)
        except OSError:
            _LOGGER.exception("Cookbook storage operation failed")
            return self.json({"error": "Recipe storage failed; collection was not replaced"}, status_code=500)

    async def get(self, request, path):
        return await self.handle(request, path, "GET")

    async def post(self, request, path):
        return await self.handle(request, path, "POST")

    async def put(self, request, path):
        return await self.handle(request, path, "PUT")

    async def delete(self, request, path):
        return await self.handle(request, path, "DELETE")
