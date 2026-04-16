import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME

DOMAIN = "nc_cookbook"
CONF_TODO_ENTITY = "todo_entity"
DEFAULT_TODO_ENTITY = "todo.shopping_list"


class NcCookbookConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            todo_entity = user_input.get(CONF_TODO_ENTITY, DEFAULT_TODO_ENTITY).strip()

            try:
                auth = aiohttp.BasicAuth(username, password)
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{url}/index.php/apps/cookbook/api/v1/recipes",
                        auth=auth,
                        headers={"OCS-APIREQUEST": "true"},
                        timeout=aiohttp.ClientTimeout(total=10),
                        ssl=False,
                    ) as resp:
                        if resp.status == 401:
                            errors["base"] = "invalid_auth"
                        elif resp.status >= 400:
                            errors["base"] = "cannot_connect"
                        else:
                            await self.async_set_unique_id(f"{url}_{username}")
                            self._abort_if_unique_id_configured()
                            return self.async_create_entry(
                                title=f"Nextcloud Cookbook ({username})",
                                data={
                                    CONF_URL: url,
                                    CONF_USERNAME: username,
                                    CONF_PASSWORD: password,
                                    CONF_TODO_ENTITY: todo_entity,
                                },
                            )
            except aiohttp.ClientConnectorError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_URL, default="https://"): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_TODO_ENTITY, default=DEFAULT_TODO_ENTITY): str,
            }),
            errors=errors,
        )
