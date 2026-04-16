# Nextcloud Cookbook – Home Assistant Integration

A custom Home Assistant integration that brings your [Nextcloud Cookbook](https://apps.nextcloud.com/apps/cookbook) directly into your Home Assistant dashboard – no separate browser tab needed.

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue)
![HACS](https://img.shields.io/badge/HACS-Custom%20Repository-orange)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

- Browse all your Nextcloud Cookbook recipes from within Home Assistant
- Search and filter by category
- Scale ingredient quantities by adjusting servings
- Check off ingredients and preparation steps while cooking
- Built-in timers for preparation steps
- Add ingredients directly to your HA shopping list (Todo entity)
- Edit existing recipes without leaving Home Assistant
- Create new recipes manually or import from a URL
- Local image cache for fast loading
- Automatically uses your Home Assistant theme colors
- Supports English and German (auto-detected from Home Assistant language settings)

---

## Requirements

- Home Assistant 2024.1 or newer
- [Nextcloud Cookbook app](https://apps.nextcloud.com/apps/cookbook) installed on your Nextcloud instance
- A Nextcloud **App Password** (not your main password)

---

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations → Custom Repositories**
3. Add `https://github.com/doctorspreter/homeassistant_nextcloud_cookbook` as a custom repository (type: Integration)
4. Search for **Nextcloud Cookbook** and install it
5. Restart Home Assistant

### Manual Installation

1. Download the latest release ZIP
2. Extract the `nc_cookbook` folder into `/config/custom_components/`
3. Restart Home Assistant

---

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Nextcloud Cookbook**
3. Fill in the following fields:

| Field | Description | Example |
|-------|-------------|---------|
| **Nextcloud URL** | Full URL of your Nextcloud instance | `https://cloud.example.com` |
| **Username** | Your Nextcloud username | `john` |
| **App Password** | App password (not your main password) | `xxxx-xxxx-xxxx-xxxx` |
| **Shopping List Entity** | HA Todo entity for the shopping list | `todo.shopping_list` |

### Creating an App Password

1. Log in to Nextcloud
2. Go to **Settings → Security → App passwords**
3. Enter a name (e.g. "Home Assistant") and click **Generate new app password**
4. Copy the generated password – you won't see it again

---

## Usage

After setup, a **Cookbook** entry appears in the Home Assistant sidebar (left menu).

### Recipe List
- Use the **search bar** to find recipes by name
- Use the **category chips** to filter by category
- Click any recipe card to open it

### Recipe Detail
- Adjust **servings** with the `−` / `+` buttons – ingredient quantities update automatically
- **Check off** ingredients and steps by tapping them
- Tap the 🛒 **cart icon** next to any ingredient to add it to your HA shopping list
- Tap **▶** to start a timer for a step (if the recipe has timing data)
- Use the **↺** buttons to reset checkboxes

### Edit / Create Recipes
- Open a recipe and tap **Edit** in the top bar to edit it directly
- Tap the **＋ button** (bottom right) to create a new recipe or import one from a URL

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Integration fails to set up | Check the Nextcloud URL (must include `https://`) and verify the app password |
| Recipes don't load | Make sure the Nextcloud Cookbook app is installed and you have recipes |
| Images are slow to load | Images are cached locally on first load – subsequent loads are instant |
| Shopping list doesn't work | Verify the Todo entity name in the integration settings matches your actual HA entity |
| Edit/Save gives error | Make sure your Nextcloud Cookbook app is up to date |

---

## Known Issues

- Recipe URL import depends on the target website supporting [Schema.org Recipe](https://schema.org/Recipe) markup

---

## Changelog

### v1.1.0
- Auto language detection (English / German) based on Home Assistant or browser settings
- Delete recipe button in editor
- Category dropdown in editor (alphabetically sorted)
- Image URL field with preview in editor
- Fixed: PUT/POST now use correct JSON format for Nextcloud Cookbook API
- Fixed: Import endpoint corrected (`/import` instead of `/recipes/url`)
- Fixed: Shopping list works via server-side proxy (no token issues)

### v1.0.0
- Initial release

---

## Architecture

```
Home Assistant
└── nc_cookbook integration
    ├── Proxy View (/api/nc_cookbook/*)
    │   Forwards requests to Nextcloud Cookbook API
    │   with authentication headers
    ├── Static Panel (/nc_cookbook_panel/)
    │   Serves the web UI (index.html)
    └── Image Cache (image_cache/)
        Local .jpg files for fast image loading
```

---

## Privacy & Security

- Your Nextcloud credentials are stored encrypted in Home Assistant's config store
- The app password is only used server-side (never exposed to the browser)
- Images are cached locally on the Home Assistant device
- No data is sent to any third-party service

---

## License

MIT License – see [LICENSE](LICENSE) for details.

---

## Contributing

Pull requests are welcome! Please open an issue first to discuss what you would like to change.
