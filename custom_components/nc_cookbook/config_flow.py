"""Set up and switch between independent local and Nextcloud collections."""
from __future__ import annotations

import asyncio
import uuid

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .backend import BackendError, LocalBackend, NextcloudBackend
from .const import CONF_MODE, CONF_TODO_ENTITY, DOMAIN, LOCAL, NEXTCLOUD, settings


def mode_schema(values):
    return vol.Schema({
        vol.Required(CONF_MODE, default=values.get(CONF_MODE, LOCAL)): selector.SelectSelector(
            selector.SelectSelectorConfig(options=[LOCAL, NEXTCLOUD], translation_key="mode")
        ),
        vol.Optional(CONF_TODO_ENTITY, description={"suggested_value": values.get(CONF_TODO_ENTITY)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="todo")),
    })


def connection_schema(values):
    return vol.Schema({
        vol.Required("url", default=values.get("url", "https://")): str,
        vol.Required("username", default=values.get("username", "")): str,
        vol.Optional("password"): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    })


class ConnectionFlow:
    """Common steps; passwords are never put into displayed defaults."""
    _pending = None
    _transfer_task = None
    _transfer_error = False

    async def async_step_nextcloud(self, user_input=None):
        errors = {}
        if user_input is not None:
            values = {**self._pending, **user_input}
            values["url"] = values["url"].strip().rstrip("/")
            values["username"] = values["username"].strip()
            values["password"] = user_input.get("password") or self._pending.get("password", "")
            try:
                await NextcloudBackend(self.hass, values).validate()
            except BackendError as error:
                errors["base"] = "invalid_auth" if error.status in (401, 403) else "cannot_connect"
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError):
                errors["base"] = "cannot_connect"
            else:
                self._pending = values
                return self._finish()
        return self.async_show_form(step_id="nextcloud", data_schema=connection_schema(self._pending), errors=errors)

    async def async_step_transfer(self, user_input=None):
        if user_input is not None:
            if user_input["transfer"]:
                self._transfer_task = None
                return await self.async_step_transferring()
            return self._finish()
        return self.async_show_form(
            step_id="transfer",
            data_schema=vol.Schema({vol.Required("transfer", default=False): bool}),
            errors={"base": "transfer_failed"} if self._transfer_error else {},
        )

    async def async_step_transferring(self, user_input=None):
        if self._transfer_task and self._transfer_task.done():
            try:
                self._transfer_task.result()
            except (Exception, asyncio.CancelledError):
                self._transfer_error = True
                return self.async_show_progress_done(next_step_id="transfer")
            return self.async_show_progress_done(next_step_id="complete")
        if not self._transfer_task:
            # Reuse the backend lock so an active local collection cannot lose edits.
            local = self.hass.data.get(DOMAIN, {}).get("local")
            if local is None:
                local = LocalBackend(self.hass, self._storage_id)
            self._transfer_task = self.hass.async_create_task(
                local.import_nextcloud(NextcloudBackend(self.hass, self._pending))
            )
        return self.async_show_progress(
            step_id="transferring", progress_action="transferring", progress_task=self._transfer_task,
        )

    async def async_step_complete(self, user_input=None):
        return self._finish()


class NcCookbookConfigFlow(ConnectionFlow, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NcCookbookOptionsFlow()

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            self._storage_id = uuid.uuid4().hex
            self._pending = {**user_input, "storage_id": self._storage_id}
            if user_input[CONF_MODE] == NEXTCLOUD:
                return await self.async_step_nextcloud()
            return self._finish()
        return self.async_show_form(step_id="user", data_schema=mode_schema({}))

    @callback
    def _finish(self):
        return self.async_create_entry(title="Cookbook", data=self._pending)


class NcCookbookOptionsFlow(ConnectionFlow, config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        current = settings(self.config_entry)
        if user_input is not None:
            self._pending = {**current, **user_input, CONF_TODO_ENTITY: user_input.get(CONF_TODO_ENTITY) or ""}
            self._storage_id = current.get("storage_id", self.config_entry.entry_id)
            if user_input[CONF_MODE] == NEXTCLOUD:
                return await self.async_step_nextcloud()
            if current[CONF_MODE] != LOCAL and current.get("url"):
                return await self.async_step_transfer()
            return self._finish()
        return self.async_show_form(step_id="init", data_schema=mode_schema(current))

    @callback
    def _finish(self):
        # Keep dormant Nextcloud credentials for switching back. The standalone
        # Notes integration can still adopt legacy notes settings during setup.
        return self.async_create_entry(title="", data=self._pending)
