"""Icons for list rows: the program's own .exe icon when its path is known, else a lettered tile.

Paths are learned while sampling (windows.paths) and kept in data/app_paths.json so that
programs seen on earlier days still get their icon. Websites always get a lettered tile:
nothing is fetched from the network.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QFileInfo, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFileIconProvider

import windows
from theme import COLORS, TEXTS, TINTS, app_name, font

_cache: dict = {}
_provider = None
_store: Path | None = None
_seen_version = -1
site_dir: Path | None = None   # data/site_icons, filled by the browser extension


def load(path: Path, sites: Path | None = None):
    """Read remembered program paths (missing or damaged file: start empty)."""
    global _store, site_dir
    _store = path; site_dir = sites
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            windows.paths.update({k: v for k, v in saved.items() if isinstance(k, str) and isinstance(v, str)})
    except (OSError, ValueError):
        pass
    windows.paths_dirty = False


def save():
    """Write the path list only when sampling learned a new program."""
    if not windows.paths_dirty or _store is None:
        return
    try:
        temp = _store.with_suffix(".tmp")
        temp.write_text(json.dumps(dict(sorted(windows.paths.items())), ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(_store); windows.paths_dirty = False
    except OSError:
        pass


def clear():
    """Forget cached icons, e.g. after a new site icon arrived."""
    _cache.clear()


def _site_icon(host):
    """www.bilibili.com → www.bilibili.com.png, bilibili.com.png …"""
    if site_dir is None:
        return None
    parts = host.split(".")
    for i in range(len(parts)-1):
        candidate = site_dir/(".".join(parts[i:])+".png")
        if candidate.exists():
            pix = QPixmap(str(candidate))
            if not pix.isNull():
                return QIcon(pix)
    if not host.startswith("www."):
        candidate = site_dir/f"www.{host}.png"
        if candidate.exists() and not QPixmap(str(candidate)).isNull():
            return QIcon(QPixmap(str(candidate)))
    return None


def _glyph(kind, category):
    """Rounded square in the category tint with a drawn symbol (system surfaces, away time)."""
    from theme import icon
    result = QIcon(); symbol = icon(kind, TEXTS.get(category, TEXTS["unknown"]), 14)
    for scale in (1.0, 1.5, 2.0):
        size = round(20*scale); pix = QPixmap(size, size); pix.setDevicePixelRatio(scale); pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TINTS.get(category, TINTS["unknown"]))); p.drawRoundedRect(QRectF(0, 0, 20, 20), 5, 5)
        symbol.paint(p, 3, 3, 14, 14); p.end(); result.addPixmap(pix)
    return result


def _tile(text, category):
    """Rounded square in the category tint with the first character, at several pixel ratios."""
    result = QIcon()
    for scale in (1.0, 1.5, 2.0):
        size = round(20*scale); pix = QPixmap(size, size); pix.setDevicePixelRatio(scale); pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix); p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(TINTS.get(category, TINTS["unknown"]))); p.drawRoundedRect(QRectF(0, 0, 20, 20), 5, 5)
        p.setPen(QColor(TEXTS.get(category, TEXTS["unknown"]))); p.setFont(font(11, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, 0, 20, 20), Qt.AlignmentFlag.AlignCenter, text[:1].upper() or "?")
        p.end(); result.addPixmap(pix)
    return result


def ready(key: str, category: str = "unknown") -> bool:
    """Whether icon_for() would answer from the cache (no shell call)."""
    return _seen_version == windows.paths_version and (key, category) in _cache


def icon_for(key: str, category: str = "unknown") -> QIcon:
    """`key` is a program (".exe"), a system surface ("界面:…") or a website domain."""
    global _provider, _seen_version
    if _seen_version != windows.paths_version:  # a program path was learned: tiles may now become real icons
        _cache.clear(); _seen_version = windows.paths_version
    cached = _cache.get((key, category))
    if cached is not None:
        return cached
    from core import AWAY_LABEL, MANUAL_MARK
    if key.startswith("界面:") or key.endswith(MANUAL_MARK):
        result = _glyph("monitor" if key.startswith("界面:") else "idle" if key == AWAY_LABEL else "pen", category if category in COLORS else "unknown")
        _cache[(key, category)] = result
        return result
    result = None
    path = windows.find_path(key) if key.endswith(".exe") else None
    if path and Path(path).exists():
        _provider = _provider or QFileIconProvider()
        found = _provider.icon(QFileInfo(path))
        if not found.isNull():
            # A shell icon asks Windows for a fresh bitmap on every paint (2–3 ms each); keep fixed sizes instead.
            result = QIcon()
            for size in (32, 48):  # each size costs one shell call (~14 ms); smaller sizes are scaled from these
                pixmap = found.pixmap(size, size)
                if not pixmap.isNull(): result.addPixmap(pixmap)
            if result.isNull(): result = found
    if result is None and not key.endswith(".exe") and "." in key and not key.startswith("界面:"):
        result = _site_icon(key)
    if result is None:
        label = app_name(key).replace("（系统界面）", "") if key else "离"
        if not key.endswith(".exe") and "." in key:  # domain: skip "www." and similar prefixes
            parts = key.split(".")
            label = parts[-2] if len(parts) >= 2 else key
        result = _tile(label, category if category in COLORS else "unknown")
    if len(_cache) > 400:
        _cache.clear()
    _cache[(key, category)] = result
    return result
