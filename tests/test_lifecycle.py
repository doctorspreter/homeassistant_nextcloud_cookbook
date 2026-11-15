"""Exercise real HA config entries, options and HTTP routes in an isolated instance."""
import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import aiohttp
from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_routes_options_reload_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).parents[1] / 'custom_components', root / 'custom_components', ignore=shutil.ignore_patterns('__pycache__'))
            hass = HomeAssistant(tmp)
            loader.async_setup(hass)
            # All data and listeners belong to this temporary HA instance.
            try:
                await bootstrap.async_from_config_dict({
                    'homeassistant': {'latitude': 0, 'longitude': 0, 'elevation': 0, 'unit_system': 'metric', 'time_zone': 'UTC'},
                    'http': {'server_host': '127.0.0.1', 'server_port': 18124},
                    'frontend': {}, 'config': {}, 'api': {},
                }, hass)
                await hass.async_start()
                result = await hass.config_entries.flow.async_init('nc_cookbook', context={'source':'user'}, data={'mode':'local'})
                self.assertEqual(result['type'], 'create_entry', result)
                entry = result['result']
                await hass.async_block_till_done()
                self.assertEqual(entry.state, ConfigEntryState.LOADED)
                self.assertIn('nc-cookbook', hass.data['frontend_panels'])
                user = await hass.auth.async_create_user('Test owner', group_ids=['system-admin'])
                refresh = await hass.auth.async_create_refresh_token(user, client_id="http://test.local/")
                token = hass.auth.async_create_access_token(refresh)
                async with aiohttp.ClientSession() as client:
                    url = 'http://127.0.0.1:18124/api/nc_cookbook/'
                    async with client.get(url+'recipes') as response:
                        self.assertEqual(response.status, 401)
                    headers = {'Authorization': 'Bearer '+token}
                    async with client.post(url+'recipes', headers=headers, json={'name':'Lifecycle recipe','recipeIngredient':['Salt'],'recipeInstructions':['Cook']}) as response:
                        self.assertEqual(response.status, 200)
                        rid = (await response.json())['id']
                    async with client.get(url+'__config__', headers=headers) as response:
                        self.assertEqual((await response.json())['mode'], 'local')
                    options = await hass.config_entries.options.async_init(entry.entry_id, data={'mode':'local'})
                    self.assertEqual(options['type'], 'create_entry')
                    await hass.async_block_till_done()
                    self.assertEqual(entry.state, ConfigEntryState.LOADED)
                    async with client.get(url+'recipes/'+rid, headers=headers) as response:
                        self.assertEqual((await response.json())['name'], 'Lifecycle recipe')
                    async with client.get(url+'settings', headers=headers) as response:
                        self.assertEqual(response.status, 404)
                    # Exercise the actual HA options manager including progress.
                    switch = await hass.config_entries.options.async_init(entry.entry_id, data={'mode':'nextcloud'})
                    self.assertEqual(switch['step_id'], 'nextcloud')
                    with patch('custom_components.nc_cookbook.config_flow.NextcloudBackend.validate', return_value=None):
                        switch = await hass.config_entries.options.async_configure(switch['flow_id'], {'url':'https://cloud.example','username':'test','password':'secret'})
                    await hass.async_block_till_done()
                    self.assertEqual(entry.state, ConfigEntryState.LOADED)
                    switch = await hass.config_entries.options.async_init(entry.entry_id, data={'mode':'local'})
                    self.assertEqual(switch['step_id'], 'transfer')
                    with patch('custom_components.nc_cookbook.backend.LocalBackend.import_nextcloud', return_value=0):
                        switch = await hass.config_entries.options.async_configure(switch['flow_id'], {'transfer':True})
                        self.assertEqual(switch['type'], 'progress')
                        await hass.async_block_till_done()
                        switch = await hass.config_entries.options.async_configure(switch['flow_id'])
                        self.assertEqual(switch['type'], 'create_entry')
                    await hass.async_block_till_done()
                    self.assertEqual(entry.options['mode'], 'local')
                    self.assertTrue(await hass.config_entries.async_unload(entry.entry_id))
                    async with client.get(url+'recipes', headers=headers) as response:
                        self.assertEqual(response.status, 503)
                    self.assertTrue(await hass.config_entries.async_setup(entry.entry_id))
                    async with client.get(url+'recipes', headers=headers) as response:
                        self.assertEqual(len(await response.json()), 1)
            finally:
                await hass.async_stop()
