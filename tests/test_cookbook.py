"""Run with the Home Assistant Python runtime: unittest discover -s tests."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.nc_cookbook.backend import BackendError, LocalBackend, NoRecipeImage
from custom_components.nc_cookbook.config_flow import NcCookbookConfigFlow, NcCookbookOptionsFlow
from custom_components.nc_cookbook.const import settings
from custom_components.nc_cookbook.importer import normalize, parse_recipe, validate_url
from custom_components.nc_cookbook.storage import RecipeStore


RECIPE = {"name": "Soup", "recipeIngredient": ["2 carrots"], "recipeInstructions": ["Cook"], "recipeCategory": "Dinner", "image": ""}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RecipeStore(self.temp.name)

    def test_atomic_failure_preserves_existing_collection(self):
        self.store.commit_import([("1", RECIPE, None)], "cloud")
        before = self.store.index.read_bytes()
        with patch("custom_components.nc_cookbook.storage.os.replace", side_effect=OSError):
            with self.assertRaises(OSError):
                self.store.commit_import([("2", {**RECIPE, "name": "Other"}, None)], "cloud")
        self.assertEqual(before, self.store.index.read_bytes())

    def test_reimport_preserves_local_edits_and_images(self):
        self.assertEqual(self.store.commit_import([("1", RECIPE, (b"image", "image/png"))], "cloud"), 1)
        data = self.store.load()
        rid = next(iter(data["recipes"]))
        data["recipes"][rid]["recipe"]["name"] = "My edit"
        self.store.save(data)
        self.assertEqual(self.store.commit_import([("1", RECIPE, None)], "cloud"), 0)
        stored = RecipeStore(self.temp.name).load()["recipes"][rid]
        self.assertEqual(stored["recipe"]["name"], "My edit")
        self.assertEqual(self.store.read_image(stored["image"]), (b"image", "image/png"))

    def test_distinct_sources_do_not_collide(self):
        for source in ("one", "two"):
            self.store.commit_import([("1", RECIPE, None)], source)
        self.assertEqual(len(self.store.load()["recipes"]), 2)

    def test_corrupt_storage_is_not_replaced(self):
        self.store.index.write_text("broken")
        with self.assertRaises(ValueError):
            self.store.commit_import([], "cloud")
        self.assertEqual(self.store.index.read_text(), "broken")

    def test_path_traversal_in_image_metadata_rejected(self):
        with self.assertRaises(ValueError):
            self.store.read_image({"file": "../../secrets.yaml", "type": "image/png"})


class ParserTests(unittest.TestCase):
    def test_jsonld_graph_sections_images_and_extra_fields(self):
        recipe = {"@type": ["Thing", "Recipe"], "name": "<b>Soup</b>", "recipeInstructions": [{"@type": "HowToSection", "itemListElement": [{"text": "Cook"}, {"text": "Eat"}]}], "image": [{"url": "/soup.jpg"}], "nutrition": {"calories": "100"}}
        doc = '<script type="application/ld+json">broken</script><script type="application/ld+json">'+json.dumps({"@graph": [recipe]})+'</script>'
        result = parse_recipe(doc, "https://example.com/recipe")
        self.assertEqual(result["recipeInstructions"], ["Cook", "Eat"])
        self.assertEqual(result["image"], "https://example.com/soup.jpg")
        self.assertEqual(result["nutrition"]["calories"], "100")
        self.assertEqual(result["name"], "Soup")

    def test_microdata(self):
        doc = '<div itemscope itemtype="https://schema.org/Recipe"><h1 itemprop="name">Soup</h1><div><span itemprop="recipeIngredient">2 carrots</span></div><p itemprop="recipeInstructions">Cook</p><img itemprop="image" src="/img.png"><meta itemprop="prepTime" content="PT10M"></div>'
        result = parse_recipe(doc, "https://example.com")
        self.assertEqual(result["recipeIngredient"], ["2 carrots"])
        self.assertEqual(result["prepTime"], "PT10M")

    def test_non_recipe_rejected(self):
        with self.assertRaises(ValueError):
            parse_recipe('<script type="application/ld+json">{"@type":"Article"}</script>', "https://example.com")

    def test_private_or_credentialed_urls_rejected(self):
        for url in ("file:///tmp/x", "http://127.0.0.1/x", "http://192.168.1.1/x", "http://[::1]/x", "http://localhost/x", "http://host.local/x", "https://user:password@example.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        validate_url("https://example.com/recipe")

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            normalize({"name": " "})


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hass = SimpleNamespace(config=SimpleNamespace(path=lambda *p: str(Path(self.temp.name, *p))), async_add_executor_job=asyncio.to_thread, data={})
        self.backend = LocalBackend(self.hass, "test-entry")

    async def test_crud_and_category_and_restart(self):
        recipe = await self.backend.request("POST", "recipes", {**RECIPE, "nutrition": {"calories": "100"}})
        rid = recipe["id"]
        self.assertEqual(len(await self.backend.request("GET", "category/Dinner")), 1)
        self.assertEqual(await self.backend.request("GET", "categories"), [{"name": "Dinner"}])
        await self.backend.request("PUT", f"recipes/{rid}", {**RECIPE, "name": "Changed"})
        restarted = LocalBackend(self.hass, "test-entry")
        result = await restarted.request("GET", f"recipes/{rid}")
        self.assertEqual(result["name"], "Changed")
        self.assertEqual(result["nutrition"]["calories"], "100")
        await restarted.request("DELETE", f"recipes/{rid}")
        self.assertEqual(await restarted.request("GET", "recipes"), [])

    async def test_concurrent_writes_preserve_all_recipes(self):
        await asyncio.gather(*(self.backend.request("POST", "recipes", {**RECIPE, "name": str(i)}) for i in range(20)))
        self.assertEqual(len(await self.backend.request("GET", "recipes")), 20)

    async def test_image_survives_edit_without_redownload(self):
        async def image(*args, **kwargs):
            return b"image", "image/png"
        with patch("custom_components.nc_cookbook.backend.download", side_effect=image) as download:
            recipe = await self.backend.request("POST", "recipes", {**RECIPE, "image": "https://example.com/image"})
            await self.backend.request("PUT", f'recipes/{recipe["id"]}', {**recipe, "name": "Edit"})
            self.assertEqual(download.call_count, 1)
            self.assertEqual(await self.backend.image(recipe["id"]), (b"image", "image/png"))

    async def test_failed_image_download_does_not_create_recipe(self):
        with patch("custom_components.nc_cookbook.backend.download", side_effect=ValueError):
            with self.assertRaises(ValueError):
                await self.backend.request("POST", "recipes", {**RECIPE, "image": "https://example.com/image"})
        self.assertEqual(await self.backend.request("GET", "recipes"), [])

    async def test_partial_remote_failure_is_atomic_and_retry_is_idempotent(self):
        class Remote:
            url, username, fail = "https://cloud.example", "cook", True
            async def request(self, method, path):
                if path == "recipes":
                    return [{"id": "1"}, {"id": "2"}]
                if path == "recipes/2" and self.fail:
                    raise BackendError(502, "Unavailable")
                return RECIPE
            async def image(self, rid):
                return b"image", "image/png"
        remote = Remote()
        with self.assertRaises(BackendError):
            await self.backend.import_nextcloud(remote)
        self.assertEqual(await self.backend.request("GET", "recipes"), [])
        remote.fail = False
        self.assertEqual(await self.backend.import_nextcloud(remote), 2)
        self.assertEqual(await self.backend.import_nextcloud(remote), 0)
        self.assertEqual(len(await self.backend.request("GET", "recipes")), 2)

    async def test_nextcloud_placeholder_is_not_treated_as_a_failed_photo(self):
        class Remote:
            url, username = "https://cloud.example", "cook"
            async def request(self, method, path):
                return [{"id": "1"}] if path == "recipes" else {**RECIPE, "image": "https://example.com/old-photo"}
            async def image(self, rid):
                raise NoRecipeImage(404, "Nextcloud has no stored photo")
        self.assertEqual(await self.backend.import_nextcloud(Remote()), 1)
        recipe = (await self.backend.request("GET", "recipes"))[0]
        self.assertEqual(recipe["image"], "")

    async def test_missing_expected_image_aborts_transfer(self):
        class Remote:
            url, username = "https://cloud.example", "cook"
            async def request(self, method, path):
                return [{"id": "1"}] if path == "recipes" else {**RECIPE, "image": "https://example.com/image"}
            async def image(self, rid):
                raise BackendError(404, "missing")
        with self.assertRaises(BackendError):
            await self.backend.import_nextcloud(Remote())
        self.assertEqual(await self.backend.request("GET", "recipes"), [])


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def options(self, data=None, options=None):
        entry = SimpleNamespace(entry_id="existing", data=data or {"url": "https://cloud.example", "username": "cook", "password": "secret"}, options=options or {})
        class TestOptions(NcCookbookOptionsFlow):
            @property
            def config_entry(self):
                return entry
        flow = TestOptions()
        flow.hass = SimpleNamespace(data={}, async_create_task=asyncio.create_task)
        return flow

    async def test_old_entries_default_to_nextcloud(self):
        self.assertEqual(settings(self.options().config_entry)["mode"], "nextcloud")

    async def test_switch_to_local_requires_explicit_transfer_choice(self):
        flow = self.options()
        result = await flow.async_step_init({"mode": "local", "todo_entity": "todo.food"})
        self.assertEqual(result["step_id"], "transfer")
        self.assertNotIn("mode", flow.config_entry.options)
        result = await flow.async_step_transfer({"transfer": False})
        self.assertEqual(result["data"]["mode"], "local")
        self.assertEqual(result["data"]["password"], "secret")

    async def test_transfer_failure_does_not_switch_mode(self):
        flow = self.options()
        await flow.async_step_init({"mode": "local"})
        future = asyncio.get_running_loop().create_future()
        future.set_exception(BackendError(502, "offline"))
        flow._transfer_task = future
        result = await flow.async_step_transferring()
        self.assertEqual(result["step_id"], "transfer")
        result = await flow.async_step_transfer()
        self.assertEqual(result["errors"]["base"], "transfer_failed")
        self.assertEqual(settings(flow.config_entry)["mode"], "nextcloud")

    async def test_local_can_be_configured_without_any_cloud_credentials(self):
        flow = NcCookbookConfigFlow()
        flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda *args, **kwargs: []))
        result = await flow.async_step_user({"mode": "local"})
        self.assertEqual(result["data"]["mode"], "local")
        self.assertNotIn("password", result["data"])
        self.assertIn("storage_id", result["data"])

    async def test_switch_back_checks_cloud_and_retains_password(self):
        flow = self.options(options={"mode": "local"})
        result = await flow.async_step_init({"mode": "nextcloud"})
        self.assertEqual(result["step_id"], "nextcloud")
        with patch("custom_components.nc_cookbook.config_flow.NextcloudBackend.validate", return_value=None):
            result = await flow.async_step_nextcloud({"url": "https://cloud.example", "username": "cook", "password": ""})
        self.assertEqual(result["data"]["mode"], "nextcloud")
        self.assertEqual(result["data"]["password"], "secret")
