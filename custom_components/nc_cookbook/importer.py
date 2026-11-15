"""Read Schema.org recipes from JSON-LD or HTML microdata.

Independent Python implementation of the standard Recipe format used by
Nextcloud Cookbook. No PHP runtime or Nextcloud application code is bundled.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.resolver import DefaultResolver

MAX_PAGE = 5 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}


def plain(value):
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()


def normalize(recipe):
    """Keep unknown recipe fields while normalizing fields used by the UI."""
    data = dict(recipe)
    data["@context"] = "https://schema.org"
    data["@type"] = "Recipe"
    data["name"] = plain(data.get("name"))
    if not data["name"]:
        raise ValueError("Recipe has no name")
    for field in ("description", "recipeYield"):
        value = data.get(field, "")
        data[field] = plain(value[0] if isinstance(value, list) and value else value)
    ingredients = data.get("recipeIngredient", [])
    if isinstance(ingredients, str):
        ingredients = ingredients.splitlines()
    data["recipeIngredient"] = [plain(i) for i in ingredients if plain(i)]

    def steps(value):
        if isinstance(value, str):
            return [plain(s) for s in value.splitlines() if plain(s)]
        if isinstance(value, list):
            return [s for item in value for s in steps(item)]
        if isinstance(value, dict):
            return steps(value.get("itemListElement") or value.get("text") or value.get("name", ""))
        return []

    data["recipeInstructions"] = steps(data.get("recipeInstructions", []))
    image = data.get("image", "")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl") or ""
    data["image"] = image if isinstance(image, str) else ""
    for key in ("keywords", "recipeCategory"):
        val = data.get(key, "")
        data[key] = ", ".join(map(plain, val)) if isinstance(val, list) else plain(val)
    for key in ("prepTime", "cookTime", "totalTime"):
        data[key] = str(data.get(key) or "")
    return data


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        return "".join(c.text() if isinstance(c, Node) else c for c in self.children)


class RecipeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_recipe(document, url):
    parser = RecipeHTML()
    parser.feed(document)

    def find(value):
        if isinstance(value, dict):
            kinds = value.get("@type", [])
            if isinstance(kinds, str):
                kinds = [kinds]
            if any(str(k).rstrip("/").split("/")[-1] == "Recipe" for k in kinds):
                return value
            for child in value.values():
                result = find(child)
                if result:
                    return result
        elif isinstance(value, list):
            for child in value:
                result = find(child)
                if result:
                    return result
        return None

    recipe = None
    for node in parser.nodes:
        if node.tag == "script" and node.attrs.get("type", "").split(";")[0].lower() == "application/ld+json":
            try:
                recipe = find(json.loads(node.text()))
            except (ValueError, TypeError):
                continue
            if recipe:
                break
    if not recipe:
        for root in parser.nodes:
            if any(t.rstrip("/").endswith("/Recipe") for t in root.attrs.get("itemtype", "").split()):
                recipe = {}

                def visit(node):
                    for child in node.children:
                        if not isinstance(child, Node):
                            continue
                        props = child.attrs.get("itemprop", "").split()
                        value = next((child.attrs[k] for k in ("content", "datetime", "src", "href") if k in child.attrs), child.text())
                        for prop in props:
                            if prop in {"recipeIngredient", "recipeInstructions"}:
                                recipe.setdefault(prop, []).append(value)
                            else:
                                recipe.setdefault(prop, value)
                        if "itemscope" not in child.attrs and not props:
                            visit(child)
                visit(root)
                if recipe.get("name"):
                    break
    if not recipe:
        raise ValueError("No Schema.org Recipe found on this page")
    recipe = normalize(recipe)
    recipe.setdefault("url", url)
    if recipe["image"]:
        recipe["image"] = urljoin(url, recipe["image"])
    return recipe


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Only public HTTP(S) URLs are supported")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        if parts.hostname.lower() == "localhost" or parts.hostname.lower().endswith(".local"):
            raise ValueError("Local addresses are not allowed for website import")
    else:
        if not address.is_global:
            raise ValueError("Private addresses are not allowed for website import")


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=0):
        answers = await super().resolve(host, port, family)
        if any(not ipaddress.ip_address(a["host"]).is_global for a in answers):
            raise ValueError("Website resolves to a private address")
        return answers


async def download(url, *, image=False):
    """Bounded downloads, DNS checks and validation of every redirect."""
    limit = MAX_IMAGE if image else MAX_PAGE
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=PublicResolver()),
        timeout=aiohttp.ClientTimeout(total=45),
        headers={"User-Agent": "HomeAssistant-Cookbook/2.0"},
    ) as session:
        for _ in range(6):
            validate_url(url)
            async with session.get(url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                if image and response.content_type not in IMAGE_TYPES:
                    raise ValueError("Unsupported image type")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise ValueError("Download exceeds size limit")
                if image:
                    return bytes(body), response.content_type
                return bytes(body).decode(response.charset or "utf-8", errors="replace"), url
        raise ValueError("Too many redirects")
