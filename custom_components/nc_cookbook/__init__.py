import json
import logging
import os
import asyncio
import aiohttp
from aiohttp import web
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME

_LOGGER = logging.getLogger(__name__)
DOMAIN = "nc_cookbook"
CONF_TODO_ENTITY = "todo_entity"
DEFAULT_TODO_ENTITY = "todo.shopping_list"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    nc_url      = entry.data[CONF_URL].rstrip("/")
    username    = entry.data[CONF_USERNAME]
    password    = entry.data[CONF_PASSWORD]
    todo_entity = entry.data.get(CONF_TODO_ENTITY, DEFAULT_TODO_ENTITY)

    panel_dir = os.path.join(os.path.dirname(__file__), "panel")
    cache_dir = os.path.join(os.path.dirname(__file__), "image_cache")
    os.makedirs(cache_dir, exist_ok=True)

    # Write config.json for the panel (non-blocking)
    def _write_config():
        with open(os.path.join(panel_dir, "config.json"), "w") as f:
            json.dump({"nc_url": nc_url, "todo_entity": todo_entity}, f)

    await hass.async_add_executor_job(_write_config)

    auth = aiohttp.BasicAuth(username, password)
    hass.http.register_view(NcCookbookProxyView(hass, nc_url, auth, cache_dir))

    await hass.http.async_register_static_paths([
        StaticPathConfig("/nc_cookbook_panel", panel_dir, cache_headers=False),
    ])

    # Only register panel if not already registered
    if "nc-cookbook" not in hass.data.get("frontend_panels", {}):
        async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title="Cookbook",
            sidebar_icon="mdi:chef-hat",
            frontend_url_path="nc-cookbook",
            config={"url": "/nc_cookbook_panel/index.html?v=110"},
            require_admin=False,
        )

    hass.data[DOMAIN][entry.entry_id] = entry.data
    hass.async_create_task(prefetch_images(hass, nc_url, auth, cache_dir))
    return True


async def prefetch_images(
    hass: HomeAssistant, nc_url: str, auth: aiohttp.BasicAuth, cache_dir: str
) -> None:
    """Download all recipe images to local cache in the background."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{nc_url}/index.php/apps/cookbook/api/v1/recipes",
                auth=auth,
                headers={"OCS-APIREQUEST": "true"},
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if not r.ok:
                    return
                recipes = await r.json()

            if not isinstance(recipes, list):
                return

            _LOGGER.info("Nextcloud Cookbook: caching %d recipe images…", len(recipes))
            for rec in recipes:
                rid = rec.get("recipe_id") or rec.get("id")
                if not rid:
                    continue
                cf = os.path.join(cache_dir, f"{rid}.jpg")
                exists = await hass.async_add_executor_job(os.path.exists, cf)
                if exists:
                    continue
                try:
                    async with session.get(
                        f"{nc_url}/index.php/apps/cookbook/api/v1/recipes/{rid}/image",
                        auth=auth,
                        headers={"OCS-APIREQUEST": "true"},
                        ssl=False,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as ir:
                        if ir.ok:
                            data = await ir.read()
                            await hass.async_add_executor_job(_write_bytes, cf, data)
                except Exception as e:
                    _LOGGER.debug("Image cache error for recipe %s: %s", rid, e)
                await asyncio.sleep(0.2)

        _LOGGER.info("Nextcloud Cookbook: image cache complete.")
    except Exception as e:
        _LOGGER.warning("Nextcloud Cookbook prefetch_images failed: %s", e)


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True


class NcCookbookProxyView(HomeAssistantView):
    """Proxy view that forwards requests to the Nextcloud Cookbook API."""

    url = "/api/nc_cookbook/{path:.*}"
    name = "api:nc_cookbook"
    requires_auth = False
    cors_allowed = True

    def __init__(
        self,
        hass: HomeAssistant,
        nc_url: str,
        auth: aiohttp.BasicAuth,
        cache_dir: str,
    ) -> None:
        self._hass = hass
        self._nc_url = nc_url
        self._auth = auth
        self._cache_dir = cache_dir

    def _target(self, path: str) -> str:
        return f"{self._nc_url}/index.php/apps/cookbook/api/v1/{path}"

    async def get(self, request: web.Request, path: str) -> web.Response:
        # Serve images from local cache
        if path.startswith("recipes/") and path.endswith("/image"):
            rid = path.split("/")[1]
            cf = os.path.join(self._cache_dir, f"{rid}.jpg")
            exists = await self._hass.async_add_executor_job(os.path.exists, cf)
            if exists:
                data = await self._hass.async_add_executor_job(_read_bytes, cf)
                return web.Response(
                    body=data,
                    content_type="image/jpeg",
                    headers={"Cache-Control": "max-age=86400"},
                )

        # Forward all other GET requests to Nextcloud
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    self._target(path),
                    auth=self._auth,
                    headers={"OCS-APIREQUEST": "true"},
                    params=dict(request.query),
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as r:
                    body = await r.read()
                    ct = r.headers.get("Content-Type", "application/json").split(";")[0].strip()

                    # Cache image on first fetch
                    if path.startswith("recipes/") and path.endswith("/image") and r.ok:
                        rid = path.split("/")[1]
                        cf = os.path.join(self._cache_dir, f"{rid}.jpg")
                        exists = await self._hass.async_add_executor_job(os.path.exists, cf)
                        if not exists:
                            await self._hass.async_add_executor_job(_write_bytes, cf, body)

                    return web.Response(body=body, content_type=ct, status=r.status)
        except Exception as e:
            _LOGGER.error("Cookbook GET proxy error for %s: %s", path, e)
            return web.Response(status=502, text=str(e))

    async def post(self, request: web.Request, path: str) -> web.Response:
        """Create a new recipe, import from URL, or add to shopping list."""
        # Shopping list: handled internally via HA services (no Nextcloud needed)
        if path == "__shopping__":
            try:
                data = json.loads(await request.read())
                item = data.get("item", "").strip()
                entity = data.get("entity_id", "todo.shopping_list")
                if not item:
                    return web.Response(status=400, text="No item provided")
                await self._hass.services.async_call(
                    "todo", "add_item",
                    {"entity_id": entity, "item": item},
                    blocking=True,
                )
                return web.Response(status=200, content_type="application/json",
                    body=json.dumps({"ok": True}).encode())
            except Exception as e:
                _LOGGER.error("Shopping list error: %s", e)
                return web.Response(status=500, content_type="application/json",
                    body=json.dumps({"error": str(e)}).encode())

        try:
            body = await request.read()
            _LOGGER.debug("Cookbook POST %s body: %s", path, body[:300])
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    self._target(path),
                    auth=self._auth,
                    headers={"OCS-APIREQUEST": "true", "Content-Type": "application/json"},
                    data=body,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as r:
                    resp_body = await r.read()
                    _LOGGER.info("Cookbook POST %s -> %s: %s", path, r.status, resp_body[:300])
                    return web.Response(
                        body=resp_body,
                        content_type="application/json",
                        status=r.status,
                    )
        except Exception as e:
            _LOGGER.error("Cookbook POST proxy error for %s: %s", path, e)
            return web.Response(
                status=502,
                content_type="application/json",
                body=json.dumps({"error": str(e)}).encode(),
            )

    async def put(self, request: web.Request, path: str) -> web.Response:
        """Update an existing recipe."""
        try:
            body = await request.read()
            async with aiohttp.ClientSession() as s:
                async with s.put(
                    self._target(path),
                    auth=self._auth,
                    headers={"OCS-APIREQUEST": "true", "Content-Type": "application/json"},
                    data=body,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as r:
                    resp_body = await r.read()
                    ct = r.headers.get("Content-Type", "application/json").split(";")[0].strip()
                    _LOGGER.info("Cookbook PUT %s -> %s: %s", path, r.status, resp_body[:300])

                    # Invalidate cached image so it gets refreshed
                    if path.startswith("recipes/"):
                        rid = path.split("/")[1]
                        cf = os.path.join(self._cache_dir, f"{rid}.jpg")
                        exists = await self._hass.async_add_executor_job(os.path.exists, cf)
                        if exists:
                            await self._hass.async_add_executor_job(os.remove, cf)

                    return web.Response(body=resp_body, content_type=ct, status=r.status)
        except Exception as e:
            _LOGGER.error("Cookbook PUT proxy error for %s: %s", path, e)
            return web.Response(status=502, text=str(e))

    async def delete(self, request: web.Request, path: str) -> web.Response:
        """Delete a recipe."""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.delete(
                    self._target(path),
                    auth=self._auth,
                    headers={"OCS-APIREQUEST": "true"},
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    return web.Response(status=r.status)
        except Exception as e:
            return web.Response(status=502, text=str(e))
