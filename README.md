# Cookbook for Home Assistant

A cookbook inside Home Assistant with a choice of **Local** or **Nextcloud** storage.
Browse, search and categorize recipes, scale servings, use cooking timers, send ingredients
to your HA shopping list, edit recipes and import them from recipe websites.
English and German are supported.

## Install with GitHub / HACS

Requires Home Assistant 2025.1 or newer. Tested with Home Assistant 2026.10.4.

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/doctorspreter/homeassistant_nextcloud_cookbook`, category **Integration**.
3. Download **Cookbook** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → Cookbook**.
5. Choose **Local (Home Assistant)** or **Nextcloud**, and optionally your shopping list.

Local mode needs no Nextcloud server, account, PHP service or additional container.
Nextcloud mode requires the Nextcloud Cookbook app and an app password.

Manual installation: copy `custom_components/nc_cookbook` from a GitHub release into
`/config/custom_components/`, then restart Home Assistant. Forgejo is the development
source; installation and updates use GitHub.

## Switch storage

Open **Settings → Devices & services → Cookbook → Configure**.
Existing installations keep using Nextcloud until you change the storage mode.

When switching from Nextcloud to Local, a separate question offers to copy **all recipes
and images**. A progress screen stays open during the copy. Nextcloud originals are never
changed. The local collection is committed only after the complete copy succeeds; a failed
copy leaves the previous mode active and can be retried.

Choosing not to copy opens the existing local collection (empty on first use). Switching
back to Nextcloud opens the Nextcloud collection. There is **no automatic synchronization**
in either direction. Repeated transfers skip recipes previously copied from the same
Nextcloud account, preserving local edits and deletions; new remote recipes are added.

## Local files and backups

Recipe JSON and images live in `/config/nc_cookbook/<storage-id>/`, outside the installed
integration folder. Include this folder in your Home Assistant backups. HACS updates do
not replace it. Removing a config entry does not delete recipe files.

Each stored recipe uses Schema.org Recipe fields. Additional fields (for example nutrition)
are preserved when editing. Imported images are downloaded locally. Reading and editing
stored recipes works without internet. Importing a website or a new image URL requires
internet. Website imports support JSON-LD and HTML microdata; sites without readable
Recipe markup, behind a login, or requiring JavaScript may not import. Only public HTTP(S)
website/image URLs are accepted; private addresses and SVG files are rejected.

## Notes are a separate integration

Cookbook 2.0 contains **no Notes backend or Notes card**. Install
[Nextcloud Notes](https://github.com/doctorspreter/homeassistant_nextcloud_notes)
separately through HACS if you used the earlier locally bundled Notes feature. Add that
integration before removing the old Cookbook configuration: Notes setup suggests the
previous account and accepts a blank password to adopt the saved app password.
The card type `custom:nc-notes-card` and resource `/nc_notes/nc-notes-card.js` stay the same.

## Security and implementation

Recipe and Notes APIs require a Home Assistant login. Nextcloud passwords remain on the
server in HA configuration storage; protect your configuration and backups. The cookbook
panel uses the current HA session and short-lived signed image URLs. Nextcloud HTTPS
certificates are validated. A configured shopping list is used for ingredient additions.

The local backend is an independent Python implementation of Schema.org Recipe storage
and import, compatible with the data format used by Nextcloud Cookbook. No Nextcloud PHP
source or AGPL components are included. This project retains its MIT license.

## Development and releases

Work is maintained on Forgejo in `projekte/homeassistant_nextcloud_cookbook`.
Publishing a Forgejo release triggers the existing Git-Spiegel service to publish the
release commit and tag to the matching GitHub repository. HACS reads the GitHub release.

Tests in a Home Assistant Python environment:

```sh
python -m unittest discover -s tests -v
```

## 2.0.0

- Choose Local or Nextcloud during setup and in integration options.
- Optional, transactional recipe and image migration with progress and retry.
- Local recipe editing, categories, images and website import.
- Preserve shopping list selection and existing Nextcloud configuration.
- Separate Nextcloud Notes into its own HACS integration.
- Authenticated recipe API and safe website downloads.
