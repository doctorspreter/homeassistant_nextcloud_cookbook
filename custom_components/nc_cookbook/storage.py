"""Transactional, update-safe local recipe storage; no Nextcloud dependency."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import uuid


class RecipeStore:
    """Blocking operations are called in HA's executor under a backend lock.

    Immutable image files are written before the atomic index replacement.
    A failed save/import leaves the previous collection intact. Unreferenced
    images are harmless and intentionally retained until explicit cleanup.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.index = self.root / "recipes.json"

    def load(self):
        if not self.index.exists():
            return {"version": 1, "recipes": {}, "sources": {}}
        data = json.loads(self.index.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("recipes"), dict):
            raise ValueError("Unsupported recipe collection")
        return data

    def save(self, data):
        self.root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".recipes-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.index)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def write_image(self, body, content_type):
        folder = self.root / "images"
        folder.mkdir(parents=True, exist_ok=True)
        name = uuid.uuid4().hex
        with (folder / name).open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        return {"file": name, "type": content_type}

    def read_image(self, image):
        name = image["file"]
        if len(name) != 32 or any(c not in "0123456789abcdef" for c in name):
            raise ValueError("Invalid image identifier")
        return (self.root / "images" / name).read_bytes(), image["type"]

    def commit_import(self, incoming, source):
        """Add a complete snapshot once; repeat imports never overwrite edits."""
        data = copy.deepcopy(self.load())
        known = data.setdefault("sources", {}).setdefault(source, {})
        count = 0
        for remote_id, recipe, image in incoming:
            if str(remote_id) in known:
                continue
            rid = uuid.uuid4().hex
            record = {"recipe": {**recipe, "id": rid, "recipe_id": rid}}
            if image:
                record["image"] = self.write_image(*image)
            data["recipes"][rid] = record
            known[str(remote_id)] = rid
            count += 1
        self.save(data)
        return count
