"""Wallpaper-driven GTK theme resolution for Nyx."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

from nyx.config import NyxConfig, UiThemeConfig

_CACHE_DIR = Path("~/.cache/nyx/ui").expanduser()


@dataclass(slots=True)
class ResolvedTheme:
    """Resolved wallpaper/theme assets used by the GTK UI."""

    colors: dict[str, str]
    backdrop_path: Path | None
    source_wallpaper: str


_DEFAULT_THEME = {
    "text_primary": "#EEE8DB",
    "text_muted": "#A5A69E",
    "accent_cool": "#8BD8E0",
    "accent_warm": "#C98E67",
    "border_primary": "#7BBCC8",
    "border_soft": "#B67F59",
    "bg_outer": "#111518",
    "bg_panel": "#22292B",
    "bg_card": "#303434",
    "bg_card_alt": "#262B2B",
    "shadow_color": "#081014",
}


def resolve_theme(config: NyxConfig, logger: logging.Logger | None = None) -> ResolvedTheme:
    """Resolve the active wallpaper-driven UI theme."""

    log = logger or logging.getLogger("nyx.ui.theme")
    colors = dict(_DEFAULT_THEME)
    backdrop_path: Path | None = None
    wallpaper_path = config.ui.wallpaper_path.strip()

    if config.ui.theme_mode == "wallpaper" and wallpaper_path:
        try:
            resolved_colors, backdrop_path = _theme_from_wallpaper(config)
            colors.update(resolved_colors)
        except Exception as exc:
            log.warning("Nyx wallpaper theme fallback activated: %s", exc)

    _merge_theme_overrides(colors, config.ui.theme)
    return ResolvedTheme(
        colors=colors,
        backdrop_path=backdrop_path if config.ui.backdrop_enabled else None,
        source_wallpaper=wallpaper_path,
    )


def _theme_from_wallpaper(config: NyxConfig) -> tuple[dict[str, str], Path]:
    """Build palette and cached backdrop assets from one wallpaper path."""

    wallpaper = Path(config.ui.wallpaper_path).expanduser()
    if not wallpaper.exists():
        raise FileNotFoundError(f"Wallpaper path does not exist: {wallpaper}")

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = _cache_key(wallpaper, config)
    backdrop_path = _CACHE_DIR / f"{cache_key}.png"
    palette_path = _CACHE_DIR / f"{cache_key}.json"

    if backdrop_path.exists() and palette_path.exists():
        palette = json.loads(palette_path.read_text(encoding="utf-8"))
        return palette, backdrop_path

    with Image.open(wallpaper) as image:
        rgba = image.convert("RGBA")
        small = rgba.resize((96, 96))
        colors = _extract_palette(small)
        backdrop = _build_backdrop(rgba, config)
        backdrop.save(backdrop_path, format="PNG")
        palette_path.write_text(json.dumps(colors, indent=2), encoding="utf-8")
    return colors, backdrop_path


def _cache_key(wallpaper: Path, config: NyxConfig) -> str:
    """Return a stable cache key for one wallpaper and backdrop config."""

    payload = "|".join(
        [
            str(wallpaper.resolve()),
            str(wallpaper.stat().st_mtime_ns),
            str(config.ui.backdrop_blur_radius),
            str(config.ui.backdrop_dim_opacity),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _extract_palette(image: Image.Image) -> dict[str, str]:
    """Extract a compact glass-friendly theme from a wallpaper thumbnail."""

    pixels = list(image.convert("RGB").getdata())
    if not pixels:
        return dict(_DEFAULT_THEME)

    avg = _average_color(pixels)
    cool = _pick_by_hue(pixels, target="cool") or _DEFAULT_THEME["accent_cool"]
    warm = _pick_by_hue(pixels, target="warm") or _DEFAULT_THEME["accent_warm"]
    bg_outer = _mix(avg, "#080B0D", 0.76)
    bg_panel = _mix(avg, "#111619", 0.58)
    bg_card = _mix(avg, "#1A2023", 0.44)
    bg_card_alt = _mix(avg, "#101518", 0.50)
    text_primary = _best_text_for(bg_outer)
    text_muted = _mix(text_primary, bg_outer, 0.48)
    border_primary = _mix(cool, "#D8F8FF", 0.24)
    border_soft = _mix(warm, "#F0D2B7", 0.26)
    shadow_color = _mix(bg_outer, "#000000", 0.72)
    return {
        "text_primary": text_primary,
        "text_muted": text_muted,
        "accent_cool": cool,
        "accent_warm": warm,
        "border_primary": border_primary,
        "border_soft": border_soft,
        "bg_outer": bg_outer,
        "bg_panel": bg_panel,
        "bg_card": bg_card,
        "bg_card_alt": bg_card_alt,
        "shadow_color": shadow_color,
    }


def _build_backdrop(image: Image.Image, config: NyxConfig) -> Image.Image:
    """Create the cached blurred backdrop image shown behind glass panels."""

    target = image.copy()
    max_width = 1680
    if target.width > max_width:
        scale = max_width / float(target.width)
        target = target.resize((max_width, max(1, int(target.height * scale))))
    target = target.filter(ImageFilter.GaussianBlur(radius=config.ui.backdrop_blur_radius))
    dim_alpha = int(max(0.0, min(1.0, config.ui.backdrop_dim_opacity)) * 255)
    dim = Image.new("RGBA", target.size, (7, 11, 13, dim_alpha))
    return Image.alpha_composite(target, dim)


def _merge_theme_overrides(colors: dict[str, str], overrides: UiThemeConfig) -> None:
    """Merge non-empty manual color overrides over a resolved palette."""

    for field in fields(overrides):
        value = getattr(overrides, field.name)
        if value.strip():
            colors[field.name] = value.strip()


def _average_color(pixels: list[tuple[int, int, int]]) -> str:
    """Return the average RGB color from a list of pixels."""

    count = max(len(pixels), 1)
    red = sum(pixel[0] for pixel in pixels) // count
    green = sum(pixel[1] for pixel in pixels) // count
    blue = sum(pixel[2] for pixel in pixels) // count
    return _to_hex((red, green, blue))


def _pick_by_hue(pixels: list[tuple[int, int, int]], *, target: str) -> str | None:
    """Pick an accent leaning toward a warm or cool hue family."""

    best: tuple[float, tuple[int, int, int]] | None = None
    for red, green, blue in pixels:
        intensity = max(red, green, blue) / 255.0
        if intensity < 0.28:
            continue
        score = _warm_score(red, green, blue) if target == "warm" else _cool_score(red, green, blue)
        weighted = score * intensity
        if best is None or weighted > best[0]:
            best = (weighted, (red, green, blue))
    if best is None or best[0] <= 0.12:
        return None
    return _to_hex(best[1])


def _warm_score(red: int, green: int, blue: int) -> float:
    """Return a simple warm-color preference score."""

    return max(red - blue, 0) / 255.0 + (green / 255.0) * 0.35


def _cool_score(red: int, green: int, blue: int) -> float:
    """Return a simple cool-color preference score."""

    return max(blue + green - red, 0) / 510.0


def _best_text_for(background_hex: str) -> str:
    """Return a high-contrast light text color for a background."""

    red, green, blue = _from_hex(background_hex)
    luminance = (0.299 * red) + (0.587 * green) + (0.114 * blue)
    return "#EEE8DB" if luminance < 160 else "#171A1D"


def _mix(foreground_hex: str, background_hex: str, foreground_weight: float) -> str:
    """Mix two hex colors by a foreground weight."""

    foreground_weight = max(0.0, min(1.0, foreground_weight))
    fr, fg, fb = _from_hex(foreground_hex)
    br, bg, bb = _from_hex(background_hex)
    mixed = (
        int(fr * foreground_weight + br * (1.0 - foreground_weight)),
        int(fg * foreground_weight + bg * (1.0 - foreground_weight)),
        int(fb * foreground_weight + bb * (1.0 - foreground_weight)),
    )
    return _to_hex(mixed)


def _from_hex(value: str) -> tuple[int, int, int]:
    """Parse one ``#RRGGBB`` color into RGB integers."""

    normalized = value.lstrip("#")
    return int(normalized[0:2], 16), int(normalized[2:4], 16), int(normalized[4:6], 16)


def _to_hex(rgb: tuple[int, int, int]) -> str:
    """Render RGB integers into ``#RRGGBB`` form."""

    return "#{:02X}{:02X}{:02X}".format(*rgb)
