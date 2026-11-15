"""Shared configuration keys."""
DOMAIN = "nc_cookbook"
CONF_MODE = "mode"
CONF_TODO_ENTITY = "todo_entity"
LOCAL = "local"
NEXTCLOUD = "nextcloud"


def settings(entry):
    """Old entries without a mode continue using Nextcloud."""
    return {CONF_MODE: NEXTCLOUD, **entry.data, **entry.options}
