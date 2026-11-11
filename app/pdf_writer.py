"""Écrivain PDF minimaliste, sans dépendance à ReportLab.

Pourquoi ce module existe
------------------------
ReportLab contient des `assert` internes dans son sous-système de polices
TrueType (ex. ``assert uchar<=0xF800`` dans ``pdfbase/ttfonts.py``). Sur certains
PDF, la génération du calque lève donc une ``AssertionError`` brute — sans
message exploitable — et tout l'export échoue alors que le document, lui, est
parfaitement valide.

Ce module construit le PDF « à la main » (spécification PDF 1.7, sous-ensemble
nécessaire : pages, polices TrueType embarquées avec ``/ToUnicode``, texte
positionné, rectangles). Il sert de **repli automatique** : si ReportLab échoue,
le document est tout de même exporté.

Encodage des glyphes
--------------------
Les polices sont écrites en ``Identity-H`` : chaque glyphe est référencé par son
*Glyph ID* obtenu via ``reportlab.pdfbase.ttfonts.TTFontFace.charToGlyph`` (lecture
de la table ``cmap``), et une table ``/ToUnicode`` garantit que le texte reste
sélectionnable et copiable dans le lecteur PDF.
"""

from __future__ import annotations

import logging
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


@dataclass
class GlyphRun:
    """Une suite de glyphes positionnés sur une même ligne de base."""

    text: str  # texte logique (pour /ToUnicode et les tests)
    x: float  # abscisse du premier glyphe (points PDF, origine en bas à gauche)
    y: float  # ordonnée de la ligne de base
    font_size: float
    font_key: str  # clé de police (voir PdfWriter.fonts)


@dataclass
class PageContent:
    """Contenu d'une page : rectangle(s) de masquage + texte."""

    width: float
    height: float
    masks: list[tuple[float, float, float, float]] = field(default_factory=list)
    runs: list[GlyphRun] = field(default_factory=list)


@dataclass
class FontResource:
    """Police TrueType embarquée."""

    key: str
    path: Path
    ref: str = ""  # référence PDF de l'objet police
    width_ref: str = ""  # référence de la table de largeurs
    to_unicode_ref: str = ""  # référence de la table /ToUnicode


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------


def _pdf_name(text: str) -> str:
    """Nettoie un nom pour l'utiliser comme nom de police PDF."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", text)
    return cleaned or "Font"


def _escape_pdf_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _FontData:
    """Accès en lecture aux métriques d'une police TrueType (via ReportLab, sans rendu)."""

    def __init__(self, path: Path) -> None:
        from reportlab.pdfbase.ttfonts import TTFontFace

        self.path = path
        self.face = TTFontFace(str(path), validate=0)
        self.cmap: dict[int, int] = self.face.charToGlyph
        self._advance_cache: dict[int, int] = {}

    def glyph_id(self, char: str) -> int:
        """Glyph ID d'un caractère ; 0 (.notdef) si absent de la police."""
        return int(self.cmap.get(ord(char), 0))

    def advance(self, glyph_id: int) -> int:
        """Largeur du glyphe en unités de police (1/1000 em)."""
        cached = self._advance_cache.get(glyph_id)
        if cached is not None:
            return cached
        width = 0
        for uchar in self.face.glyphToChar.get(glyph_id, []):
            try:
                width = int(self.face.getCharWidth(uchar))
                break
            except Exception:
                continue
        self._advance_cache[glyph_id] = width
        return width


# ---------------------------------------------------------------------------
# Écrivain
# ---------------------------------------------------------------------------


class PdfWriter:
    """Construit un PDF minimal : pages A4 quelconques, masques blancs, texte TrueType."""

    def __init__(self) -> None:
        self.pages: list[PageContent] = []
        self.fonts: dict[str, FontResource] = {}
        self._font_data: dict[str, _FontData] = {}

    # -- API ---------------------------------------------------------------
    def register_font(self, key: str, path: Path) -> None:
        """Déclare une police à embarquer (lecture des métriques différée)."""
        if key in self.fonts:
            return
        self.fonts[key] = FontResource(key=key, path=path)
        self._font_data[key] = _FontData(path)

    def add_page(self, page: PageContent) -> None:
        self.pages.append(page)

    def font_width(self, font_key: str, text: str, font_size: float) -> float:
        """Largeur (points PDF) d'un texte dans une police enregistrée."""
        data = self._font_data.get(font_key)
        if data is None:
            return 0.0
        total = sum(data.advance(data.glyph_id(ch)) for ch in text)
        return total * font_size / 1000.0

    def build(self) -> bytes:
        """Sérialise le document complet."""
        if not self.pages:
            raise ValueError("Aucune page à écrire.")

        objects: list[bytes] = [b""]  # l'index 0 n'est jamais utilisé (réf. 1 = catalogue)

        def add_object(body: bytes) -> str:
            # Convention : le numéro d'objet PDF est l'index dans `objects`.
            objects.append(body)
            return f"{len(objects) - 1} 0 R"

        def set_object(ref: str, body: bytes) -> None:
            """Remplace le corps d'un objet déjà référencé."""
            objects[int(ref.split()[0])] = body

        catalog_ref = add_object(b"")  # rempli à la fin
        pages_ref = add_object(b"")  # rempli à la fin

        # --- polices ---
        for resource in self.fonts.values():
            self._write_font(resource, add_object, set_object)

        # --- pages ---
        page_refs: list[str] = []
        for page in self.pages:
            content = zlib.compress(self._page_stream(page), 6)
            content_ref = add_object(
                b"<< /Length " + str(len(content)).encode() + b" /Filter /FlateDecode >>\nstream\n"
                + content + b"\nendstream"
            )

            font_entries = b" ".join(
                f"/{_pdf_name(key)} {font.ref}".encode() for key, font in self.fonts.items()
            )
            page_ref = add_object(
                b"<< /Type /Page /Parent " + pages_ref.encode()
                + b" /MediaBox [0 0 " + f"{page.width:.2f} {page.height:.2f}".encode() + b"]"
                + b" /Resources << /Font << " + font_entries + b" >> >>"
                + b" /Contents " + content_ref.encode()
                + b" >>"
            )
            page_refs.append(page_ref)

        # --- arbre de pages et catalogue ---
        kids = b" ".join(ref.encode() for ref in page_refs)
        set_object(
            pages_ref,
            b"<< /Type /Pages /Count " + str(len(page_refs)).encode()
            + b" /Kids [" + kids + b"] >>",
        )
        set_object(catalog_ref, b"<< /Type /Catalog /Pages " + pages_ref.encode() + b" >>")

        # --- assemblage (table des références croisées) ---
        output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = [0]
        for index in range(1, len(objects)):
            offsets.append(len(output))
            output += f"{index} 0 obj\n".encode() + objects[index] + b"\nendobj\n"

        xref_offset = len(output)
        output += f"xref\n0 {len(objects)}\n".encode()
        output += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            output += f"{offset:010d} 00000 n \n".encode()
        output += (
            b"trailer\n<< /Size " + str(len(objects)).encode()
            + b" /Root " + catalog_ref.encode() + b" >>\n"
            + b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
        )
        return bytes(output)

    # -- interne -----------------------------------------------------------
    def _write_font(self, resource: FontResource, add_object, set_object) -> None:
        """Embarque le TTF, la table de largeurs (/W) et la table /ToUnicode."""
        data = self._font_data[resource.key]
        font_bytes = resource.path.read_bytes()
        font_name = _pdf_name(resource.path.stem)

        # /W : tableau `c [w1 ... wn]`, en groupant les CID consécutifs.
        gids = sorted({g for g in data.cmap.values() if g})
        groups: list[str] = []
        index = 0
        while index < len(gids):
            start = gids[index]
            run = [data.advance(start)]
            index += 1
            while index < len(gids) and gids[index] == gids[index - 1] + 1:
                run.append(data.advance(gids[index]))
                index += 1
            groups.append(f"{start} [" + " ".join(str(w) for w in run) + "]")
        widths = ("[" + " ".join(groups) + "]").encode()

        # /ToUnicode : CID (glyph id) -> caractère Unicode
        to_unicode = self._build_to_unicode(data)

        descriptor_ref = add_object(b"")  # réservé, rempli ci-dessous
        compressed_font = zlib.compress(font_bytes, 6)
        font_file_ref = add_object(
            b"<< /Length " + str(len(compressed_font)).encode()
            + b" /Length1 " + str(len(font_bytes)).encode()
            + b" /Filter /FlateDecode >>\nstream\n" + compressed_font + b"\nendstream"
        )
        set_object(
            descriptor_ref,
            b"<< /Type /FontDescriptor /FontName /" + font_name.encode()
            + b" /Flags 4 /FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 800"
            + b" /Descent -200 /CapHeight 700 /StemV 80 /FontFile2 " + font_file_ref.encode()
            + b" >>",
        )

        cid_ref = add_object(
            b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /" + font_name.encode()
            + b" /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >>"
            + b" /FontDescriptor " + descriptor_ref.encode()
            + b" /DW 1000 /W " + widths + b" >>"
        )
        packed_cmap = zlib.compress(to_unicode, 6)
        to_unicode_ref = add_object(
            b"<< /Length " + str(len(packed_cmap)).encode() + b" /Filter /FlateDecode >>\nstream\n"
            + packed_cmap + b"\nendstream"
        )
        resource.width_ref = cid_ref
        resource.to_unicode_ref = to_unicode_ref
        resource.ref = add_object(
            b"<< /Type /Font /Subtype /Type0 /BaseFont /" + font_name.encode()
            + b" /Encoding /Identity-H /DescendantFonts [" + cid_ref.encode() + b"]"
            + b" /ToUnicode " + to_unicode_ref.encode() + b" >>"
        )

    def _build_to_unicode(self, data: _FontData) -> bytes:
        """Table CMap minimale : glyph id -> point de code Unicode."""
        lines: list[str] = []
        for codepoint, gid in sorted(data.cmap.items(), key=lambda item: item[1]):
            if not gid or codepoint > 0xFFFF:
                continue
            lines.append(f"<{gid:04X}> <{codepoint:04X}>")
        # Le format CMap limite chaque bloc `beginbfchar` à 100 entrées.
        blocks: list[str] = []
        for start in range(0, len(lines), 100):
            chunk = lines[start : start + 100]
            blocks.append(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar")
        cmap = (
            "/CIDInit /ProcSet findresource begin\n"
            "12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
            + "\n".join(blocks)
            + "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend"
        )
        return cmap.encode("latin-1", "replace")

    def _page_stream(self, page: PageContent) -> bytes:
        """Instructions de contenu : masques blancs puis texte positionné."""
        parts: list[bytes] = [b"q"]
        if page.masks:
            parts.append(b"1 1 1 rg 1 1 1 RG")
            for x0, y0, x1, y1 in page.masks:
                width = max(0.5, x1 - x0)
                height = max(0.5, y1 - y0)
                parts.append(f"{x0:.2f} {y0:.2f} {width:.2f} {height:.2f} re f".encode())
        parts.append(b"Q")

        for run in page.runs:
            if not run.text:
                continue
            parts.append(b"q 0 0 0 rg")
            parts.append(
                f"BT /{_pdf_name(self.fonts[run.font_key].key)} {run.font_size:.2f} Tf "
                f"1 0 0 1 {run.x:.2f} {run.y:.2f} Tm".encode()
            )
            parts.append(b"<" + self._glyph_hex(run) + b"> Tj ET Q")
        return b"\n".join(parts)

    def _glyph_hex(self, run: GlyphRun) -> bytes:
        """Encode le texte en CIDs (glyph ids) hexadécimaux, format Identity-H."""
        data = self._font_data[run.font_key]
        cids = [data.glyph_id(ch) for ch in run.text]
        return b"".join(f"{cid:04X}".encode() for cid in cids)
