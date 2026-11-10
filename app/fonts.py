"""Registre des polices proposées à l'utilisateur dans l'éditeur.

Chaque famille déclare ses fichiers TTF (regular / bold) et les scripts qu'elle
couvre réellement. Plusieurs polices Google (Cairo, Tajawal, Almarai, Readex Pro)
ont été écartées : testées, elles ne couvrent pas les *Arabic Presentation Forms*
utilisées après `arabic-reshaper` et cassent les ligatures (« ﻟ » remplacé par un
carré). Seules les familles vérifiées sont exposées.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.config import PROJECT_ROOT

FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"


@dataclass(frozen=True)
class FontFamily:
    """Une famille de polices proposée à l'utilisateur."""

    id: str
    label: str
    regular: str
    bold: str | None = None
    arabic: bool = False
    latin: bool = True
    fallback: str = "DejaVuSans-Regular"

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(f for f in (self.regular, self.bold) if f)  # type: ignore[arg-type]

    def path(self, bold: bool = False) -> Path:
        filename = self.bold if (bold and self.bold) else self.regular
        return FONTS_DIR / filename

    def available(self) -> bool:
        return self.path(False).is_file()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "arabic": self.arabic,
            "latin": self.latin,
            "bold": bool(self.bold),
            "available": self.available(),
        }


# Ordre = ordre d'affichage dans l'interface.
FONT_FAMILIES: tuple[FontFamily, ...] = (
    FontFamily(
        id="amiri",
        label="Amiri — naskh classique",
        regular="Amiri-Regular.ttf",
        arabic=True,
    ),
    FontFamily(
        id="noto-naskh",
        label="Noto Naskh Arabic",
        regular="NotoNaskhArabic-Regular.ttf",
        bold="NotoNaskhArabic-Bold.ttf",
        arabic=True,
    ),
    FontFamily(
        id="noto-sans-arabic",
        label="Noto Sans Arabic — sans-serif",
        regular="NotoSansArabic-Regular.ttf",
        bold="NotoSansArabic-Bold.ttf",
        arabic=True,
    ),
    FontFamily(
        id="ibm-plex-arabic",
        label="IBM Plex Sans Arabic",
        regular="IBMPlexSansArabic-Regular.ttf",
        bold="IBMPlexSansArabic-Bold.ttf",
        arabic=True,
    ),
    FontFamily(
        id="dejavu",
        label="DejaVu Sans — couverture Unicode",
        regular="DejaVuSans.ttf",
        arabic=True,
    ),
    FontFamily(
        id="roboto",
        label="Roboto — latin",
        regular="Roboto-Regular.ttf",
        bold="Roboto-Bold.ttf",
        arabic=False,
    ),
    FontFamily(
        id="open-sans",
        label="Open Sans — latin",
        regular="OpenSans-Regular.ttf",
        bold="OpenSans-Bold.ttf",
        arabic=False,
    ),
    FontFamily(
        id="lato",
        label="Lato — latin",
        regular="Lato-Regular.ttf",
        bold="Lato-Bold.ttf",
        arabic=False,
    ),
)

FONTS_BY_ID: dict[str, FontFamily] = {font.id: font for font in FONT_FAMILIES}

DEFAULT_ARABIC_FONT = "amiri"
DEFAULT_LATIN_FONT = "dejavu"


def get_font_family(font_id: str | None, *, is_arabic: bool) -> FontFamily:
    """Retourne la famille demandée, ou une police par défaut compatible script.

    Une police latine (Roboto, Lato…) ne peut pas rendre l'arabe : on bascule
    automatiquement sur la police arabe par défaut pour ne jamais produire de
    carrés vides dans le PDF.
    """
    family = FONTS_BY_ID.get((font_id or "").strip().lower())
    if family is None or not family.available():
        family = FONTS_BY_ID[DEFAULT_ARABIC_FONT if is_arabic else DEFAULT_LATIN_FONT]
    if is_arabic and not family.arabic:
        family = FONTS_BY_ID[DEFAULT_ARABIC_FONT]
    return family


def font_catalog() -> list[dict[str, object]]:
    """Catalogue sérialisable pour l'interface et `/api/fonts`."""
    return [font.to_dict() for font in FONT_FAMILIES if font.available()]


# ---------------------------------------------------------------------------
# Enregistrement des polices auprès de ReportLab
# ---------------------------------------------------------------------------

_REGISTERED: dict[str, str] = {}
LatinOrArabic = str


def _register_path(name: str, path: Path) -> str:
    """Enregistre un TTF (une seule fois) et retourne le nom ReportLab."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if name in pdfmetrics.getRegisteredFontNames():
        _REGISTERED[name] = str(path)
        return name
    pdfmetrics.registerFont(TTFont(name, str(path)))
    _REGISTERED[name] = str(path)
    return name


def ensure_family_registered(family: FontFamily, *, bold: bool = False) -> str:
    """Enregistre (ou réutilise) la police d'une famille et retourne son nom ReportLab."""
    use_bold = bool(bold and family.bold)
    filename = family.bold if use_bold else family.regular
    path = FONTS_DIR / filename  # type: ignore[operator]
    if not path.is_file():
        path = FONTS_DIR / family.regular
        use_bold = False
    name = f"F_{path.stem}"
    return _register_path(name, path)


def registered_fonts() -> dict[str, str]:
    """Polices réellement enregistrées (diagnostic)."""
    return dict(_REGISTERED)
