"""Interchangeable Nextcloud and local recipe backends."""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from urllib.parse import quote

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .importer import IMAGE_TYPES, MAX_IMAGE, download, normalize, parse_recipe
from .storage import RecipeStore


class BackendError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class NoRecipeImage(BackendError):
    """Nextcloud explicitly reports that this recipe has no stored photo."""


class NextcloudBackend:
    def __init__(self, hass, config):
        self.hass = hass
        self.url = config["url"].rstrip("/")
        self.username = config["username"]
        self.auth = aiohttp.BasicAuth(self.username, config["password"])

    async def request(self, method, path, data=None):
        session = async_get_clientsession(self.hass)
        async with session.request(
            method, f"{self.url}/index.php/apps/cookbook/api/v1/{path}",
            auth=self.auth, json=data if method in {"POST", "PUT"} else None,
            headers={"OCS-APIREQUEST": "true"},
            timeout=aiohttp.ClientTimeout(total=60), allow_redirects=False,
        ) as response:
            if response.status >= 300:
                raise BackendError(response.status, "Nextcloud request failed")
            if method == "DELETE" or response.status == 204:
                return None
            return await response.json()

    async def image(self, rid):
        session = async_get_clientsession(self.hass)
        async with session.get(
            f"{self.url}/index.php/apps/cookbook/api/v1/recipes/{quote(str(rid), safe='')}/image",
            auth=self.auth, headers={"OCS-APIREQUEST": "true", "Accept": "image/jpeg,image/png,image/webp,image/gif,image/avif"},
            timeout=aiohttp.ClientTimeout(total=45), allow_redirects=False,
        ) as response:
            # Without an Accept header Nextcloud returns its SVG placeholder.
            # Asking for raster formats makes its missing-photo response 406.
            if response.status == 406:
                raise NoRecipeImage(404, "No recipe photo stored in Nextcloud")
            if response.status >= 300:
                raise BackendError(response.status, "Nextcloud image request failed")
            if response.content_type not in IMAGE_TYPES:
                raise ValueError("Unsupported image type")
            body = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > MAX_IMAGE:
                    raise ValueError("Image exceeds size limit")
            return bytes(body), response.content_type

    async def validate(self):
        result = await self.request("GET", "recipes")
        if not isinstance(result, list):
            raise ValueError("Invalid recipe list")


class LocalBackend:
    def __init__(self, hass, entry_id):
        self.hass = hass
        self.store = RecipeStore(hass.config.path("nc_cookbook", entry_id))
        self.lock = asyncio.Lock()

    async def run(self, fn, *args):
        return await self.hass.async_add_executor_job(fn, *args)

    async def image(self, rid):
        data = await self.run(self.store.load)
        record = data["recipes"].get(rid, {})
        if not record.get("image"):
            raise BackendError(404, "No image")
        return await self.run(self.store.read_image, record["image"])

    async def request(self, method, path, data=None):
        async with self.lock:
            collection = await self.run(self.store.load)
            records = collection["recipes"]
            if method == "GET":
                recipes = [v["recipe"] for v in records.values()]
                if path == "recipes":
                    return sorted(recipes, key=lambda r: r["name"].casefold())
                if path == "categories":
                    return [{"name": c} for c in sorted({c for r in recipes for c in categories(r)})]
                if path.startswith("category/"):
                    return [r for r in recipes if path[9:] in categories(r)]
                rid = path.split("/")[-1]
                if rid not in records:
                    raise BackendError(404, "Recipe not found")
                return records[rid]["recipe"]
            if method == "DELETE":
                rid = path.split("/")[-1]
                if rid not in records:
                    raise BackendError(404, "Recipe not found")
                del records[rid]
                await self.run(self.store.save, collection)
                return None
            if path == "import":
                document, url = await download(data.get("url", ""))
                data = await self.run(parse_recipe, document, url)
            recipe = normalize(data)
            rid = path.split("/")[-1] if method == "PUT" else uuid.uuid4().hex
            if method == "PUT" and rid not in records:
                raise BackendError(404, "Recipe not found")
            old = records.get(rid, {})
            # Preserve Schema.org fields not exposed in the simple editor.
            recipe = {**old.get("recipe", {}), **recipe, "id": rid, "recipe_id": rid}
            record = {"recipe": recipe}
            if recipe.get("image") == old.get("recipe", {}).get("image") and old.get("image"):
                record["image"] = old["image"]
            elif recipe.get("image"):
                image = await download(recipe["image"], image=True)
                record["image"] = await self.run(self.store.write_image, *image)
            records[rid] = record
            await self.run(self.store.save, collection)
            return recipe

    async def import_nextcloud(self, remote):
        """Fetch the complete snapshot before committing or switching mode."""
        async with self.lock:
            recipes = await remote.request("GET", "recipes")
            if not isinstance(recipes, list):
                raise ValueError("Invalid Nextcloud recipe list")
            source = hashlib.sha256(f"{remote.url}\n{remote.username}".encode()).hexdigest()
            known = (await self.run(self.store.load)).get("sources", {}).get(source, {})
            incoming = []
            for summary in recipes:
                rid = str(summary.get("recipe_id") or summary.get("id") or "")
                if not rid:
                    raise ValueError("Recipe without ID")
                if rid in known:
                    continue
                recipe = normalize(await remote.request("GET", f"recipes/{quote(rid, safe='')}"))
                try:
                    image = await remote.image(rid)
                except NoRecipeImage:
                    image = None
                    recipe["image"] = ""
                except BackendError as error:
                    if error.status != 404 or recipe.get("image"):
                        raise
                    image = None
                incoming.append((rid, recipe, image))
            return await self.run(self.store.commit_import, incoming, source)


def categories(recipe):
    value = recipe.get("recipeCategory") or ""
    return [c.strip() for c in (value if isinstance(value, list) else value.split(",")) if c.strip()]
