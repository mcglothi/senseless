"""Appearance: theme catalog, base16 gallery, and per-install settings.

Same contract as Docket's theming API, so the frontend's Appearance panel works unchanged:

    GET    /api/themes                 {"themes": [theme, ...]}   bundled + installed
    POST   /api/themes/install {id}    copy a gallery scheme into the catalog
    DELETE /api/themes/{id}            remove an installed scheme
    GET    /api/themes/gallery         every scheme in tinted-theming/schemes (cached)
    GET    /api/appearance             settings
    POST   /api/appearance {patch}     merge + validate + save, returns settings

A theme is {id, name, author, variant: dark|light, system: base16|base24, palette: {base00..base0F}, accent?}.
"""

import io
import json
import logging
import re
import sqlite3
import tarfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("senseless.appearance")

GALLERY_URL = "https://codeload.github.com/tinted-theming/schemes/tar.gz/refs/heads/spec-0.11"
GALLERY_TTL = 7 * 86400
REFRESH_MIN_S = 300       # ?refresh=1 can re-download at most this often
SLOTS = [f"base0{c}" for c in "0123456789ABCDEF"]

# The default look: Docket's console palette with an energy-amber accent.
BUNDLED = [
    {
        "id": "senseless", "name": "Senseless", "author": "Senseless", "variant": "dark", "system": "base16",
        "accent": "base0A", "bundled": True,
        "palette": {
            "base00": "#080808", "base01": "#0b0b0e", "base02": "#17171c", "base03": "#555555",
            "base04": "#8a8a8a", "base05": "#e8e8e8", "base06": "#f2f2f2", "base07": "#ffffff",
            "base08": "#ff4d4f", "base09": "#f97316", "base0A": "#ffab00", "base0B": "#39ff5a",
            "base0C": "#00fff7", "base0D": "#2979ff", "base0E": "#bf5fff", "base0F": "#ff2d6b",
        },
    },
    {
        "id": "senseless-light", "name": "Senseless Light", "author": "Senseless", "variant": "light", "system": "base16",
        "accent": "base0A", "bundled": True,
        "palette": {
            "base00": "#f7f6f2", "base01": "#efede7", "base02": "#e2dfd6", "base03": "#a19d93",
            "base04": "#6b675f", "base05": "#1d1c1a", "base06": "#111110", "base07": "#000000",
            "base08": "#d92d20", "base09": "#e0590b", "base0A": "#b7791f", "base0B": "#15803d",
            "base0C": "#0e7490", "base0D": "#1d4ed8", "base0E": "#7e22ce", "base0F": "#be185d",
        },
    },
]
BUNDLED_IDS = {t["id"] for t in BUNDLED}

DEFAULTS = {
    "mode": "dark", "dark": "senseless", "light": "senseless-light", "accent": None, "radius": 12,
    "texture": "dots", "textureStrength": 8, "glow": "corners", "glowStrength": 3,
    "density": "comfortable", "textScale": 100, "motion": "system",
}
ENUMS = {
    "mode": {"dark", "light", "system"}, "texture": {"none", "dots", "grid", "crosshair", "scanlines", "hatch"},
    "glow": {"none", "corners", "vignette", "aurora"}, "density": {"compact", "comfortable", "spacious"},
    "motion": {"system", "full", "reduced"},
}
RANGES = {"radius": (0, 24), "textureStrength": (0, 30), "glowStrength": (0, 30), "textScale": (80, 140)}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class AppearanceError(ValueError):
    pass


def parse_scheme(text: str) -> dict | None:
    """Parse one tinted-theming YAML scheme (flat enough for a regex parser; no YAML dependency)."""
    def field(name):
        m = re.search(rf'^{name}:\s*"?([^"\n]*)"?\s*$', text, re.M)
        return m.group(1).strip() if m else None
    found = re.findall(r'^\s+base0([0-9A-Fa-f]):\s*"(#[0-9a-fA-F]{6})"', text, re.M)
    palette = {"base0" + k.upper(): v.lower() for k, v in found}
    if not all(s in palette for s in SLOTS):
        return None
    return {"name": field("name") or "", "author": field("author") or "", "variant": field("variant") or "dark",
            "system": field("system") or "base16", "palette": {s: palette[s] for s in SLOTS}}


def fetch_gallery(url: str = GALLERY_URL, timeout: float = 30) -> list[dict]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        blob = r.read()
    themes = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            parts = m.name.split("/")
            if not m.isfile() or len(parts) != 3 or parts[1] not in ("base16", "base24") or not parts[2].endswith(".yaml"):
                continue
            s = parse_scheme(tar.extractfile(m).read().decode("utf-8", "replace"))
            if s:
                slug = parts[2][:-5]
                s["id"] = slug if parts[1] == "base16" else f"{slug}-b24"
                themes.append(s)
    themes.sort(key=lambda t: t["name"].lower())
    return themes


class Appearance:
    def __init__(self, db_path: Path, cache_dir: Path, fetch=fetch_gallery):
        # Its own file (settings.db): the collector holds a near-continuous write transaction on
        # senseless.db, so settings writes there would hit "database is locked".
        # A connection per call: handlers run some of these on worker threads (asyncio.to_thread).
        self.db_path = db_path
        with self._conn() as db:
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        self.cache = cache_dir / "themes-gallery.json"
        self.fetch = fetch
        self._fetch_lock = threading.Lock()   # one gallery download at a time
        self._write_lock = threading.Lock()   # catalog/settings read-modify-write is atomic

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            with db:  # commits on success
                yield db
        finally:
            db.close()

    def _get(self, key, default):
        with self._conn() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _put(self, key, value):
        with self._conn() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))

    # ── catalog ──────────────────────────────────────────────────────────
    def themes(self) -> list[dict]:
        installed = [{**t, "installed": True} for t in self._get("themes_installed", [])]
        return BUNDLED + installed

    def gallery(self, refresh: bool = False) -> dict:
        with self._fetch_lock:
            return self._gallery(refresh)

    def _gallery(self, refresh: bool) -> dict:
        cached = None
        if self.cache.exists():
            try:
                cached = json.loads(self.cache.read_text())
            except ValueError:
                cached = None
        age = time.time() - cached.get("fetched_at", 0) if cached else None
        stale = age is None or age >= GALLERY_TTL
        if stale or (refresh and age >= REFRESH_MIN_S):
            try:
                themes = self.fetch()
                cached = {"fetched_at": time.time(), "themes": themes}
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_text(json.dumps(cached))
            except Exception as e:  # network down: serve the stale copy if we have one
                log.warning("gallery fetch failed: %r", e)
                if cached:
                    return {**cached, "error": f"couldn't refresh the gallery ({type(e).__name__}); showing the cached copy"}
                return {"themes": [], "error": f"couldn't load the gallery ({type(e).__name__})"}
        return cached

    def install(self, theme_id: str) -> list[dict]:
        gallery = self.gallery().get("themes", [])  # may download: outside the write lock
        with self._write_lock:
            return self._install(theme_id, gallery)

    def _install(self, theme_id: str, gallery: list[dict]) -> list[dict]:
        if theme_id in BUNDLED_IDS or any(t["id"] == theme_id for t in self._get("themes_installed", [])):
            return self.themes()
        match = next((t for t in gallery if t["id"] == theme_id), None)
        if not match:
            raise AppearanceError(f"no gallery theme {theme_id!r}")
        self._put("themes_installed", self._get("themes_installed", []) + [match])
        return self.themes()

    def remove(self, theme_id: str) -> list[dict]:
        with self._write_lock:
            return self._remove(theme_id)

    def _remove(self, theme_id: str) -> list[dict]:
        if theme_id in BUNDLED_IDS:
            raise AppearanceError("built-in themes can't be removed")
        self._put("themes_installed", [t for t in self._get("themes_installed", []) if t["id"] != theme_id])
        # settings pointing at it fall back to the defaults
        s = self.settings()
        changed = {k: DEFAULTS[k] for k in ("dark", "light") if s.get(k) == theme_id}
        if changed:
            self._put("appearance", {**s, **changed})
        return self.themes()

    # ── settings ─────────────────────────────────────────────────────────
    def settings(self) -> dict:
        return {**DEFAULTS, **self._get("appearance", {})}

    def update(self, patch: dict) -> dict:
        with self._write_lock:
            return self._update(patch)

    def _update(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise AppearanceError("expected an object")
        ids = {t["id"] for t in self.themes()}
        clean = {}
        for k, v in patch.items():
            if k not in DEFAULTS:
                raise AppearanceError(f"unknown setting {k!r}")
            if k in ENUMS and v not in ENUMS[k]:
                raise AppearanceError(f"{k} must be one of {sorted(ENUMS[k])}")
            if k in RANGES:
                lo, hi = RANGES[k]
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi:
                    raise AppearanceError(f"{k} must be a number in {lo}..{hi}")
            if k in ("dark", "light") and v not in ids:
                raise AppearanceError(f"unknown theme {v!r}")
            if k == "accent" and v is not None and not (isinstance(v, str) and (HEX.match(v) or v in SLOTS)):
                raise AppearanceError("accent must be null, a base16 slot (base08..base0F) or #rrggbb")
            clean[k] = v
        merged = {**self.settings(), **clean}
        self._put("appearance", {k: merged[k] for k in DEFAULTS})
        return self.settings()
