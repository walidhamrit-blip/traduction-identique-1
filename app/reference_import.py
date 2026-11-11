"""Import d'une base d'articles depuis un export PDF, un CSV ou un JSON.

L'export « Liste article par Tarif » est un tableau à cinq colonnes :
Référence | Désignation (anglais) | Famille | Unité de vente | Désignation arabe.
On s'appuie sur les coordonnées du texte : la référence sert d'ancre et chaque
valeur rejoint la référence la plus proche verticalement.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{2,}$")

# Colonnes de l'export, en points PDF (origine en haut à gauche).
CODES_WIDTH = 100.0
DESIGNATION_MAX_X = 425.0
FAMILY_MAX_X = 480.0
BAND_TOLERANCE = 11.0

# En-têtes reconnus dans un CSV/JSON (français ou anglais).
HEADER_ALIASES = {
    "code": ("code", "reference", "référence", "ref", "item", "itemcode", "article"),
    "designation_en": (
        "designation_en", "designation", "désignation", "designation anglaise",
        "description", "name", "libelle", "libellé",
    ),
    "designation_fr": ("designation_fr", "designation française", "designation_fr_fr", "fr"),
    "designation_ar": ("designation_ar", "désignation arabe", "arabic", "ar", "designation arabe"),
    "family": ("family", "famille", "famille article"),
    "unit": ("unit", "unite", "unité", "unité vente", "unite vente", "um"),
}


def normalize(value: str) -> str:
    """Normalise un libellé pour la comparaison (casse, accents, ponctuation)."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("\u2019", "'").replace("\u2014", "-").replace("\u2013", "-")
    text = re.sub(r"[^\w\s\u0600-\u06FF-]", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _band(x0: float, text: str) -> str | None:
    """Classe un fragment de texte dans sa colonne (l'arabe est reconnu par son alphabet)."""
    if ARABIC_RE.search(text):
        return "designation_ar"
    if x0 < CODES_WIDTH:
        return "code"
    if x0 < DESIGNATION_MAX_X:
        return "designation_en"
    if x0 < FAMILY_MAX_X:
        return "family"
    return "unit"


def parse_pdf_export(content: bytes) -> list[dict[str, str]]:
    """Extrait les articles d'un export PDF « Liste article par Tarif »."""
    import pymupdf

    document = pymupdf.open(stream=content, filetype="pdf")
    records: list[dict[str, str]] = []
    try:
        for page in document:
            spans: list[tuple[float, float, str]] = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if text:
                            spans.append((span["bbox"][1], span["bbox"][0], text))
            spans.sort()

            anchors = [(y, x, t) for y, x, t in spans if x < CODES_WIDTH and CODE_RE.match(t)]
            if not anchors:
                continue
            cells: list[dict[str, list[tuple[float, str]]]] = [{} for _ in anchors]
            for y, x, text in spans:
                column = _band(x, text)
                if column is None or column == "code":
                    continue
                nearest = min(range(len(anchors)), key=lambda i: abs(anchors[i][0] - y))
                if abs(anchors[nearest][0] - y) > BAND_TOLERANCE:
                    continue
                cells[nearest].setdefault(column, []).append((x, text))

            for index, (_y, _x, code) in enumerate(anchors):
                def joined(column: str, _index: int = index) -> str:
                    return " ".join(
                        t for _x, t in sorted(cells[_index].get(column, []))
                    ).strip()

                records.append(
                    {
                        "code": code,
                        "designation_en": joined("designation_en"),
                        "designation_ar": joined("designation_ar"),
                        "family": joined("family"),
                        "unit": joined("unit"),
                    }
                )
    finally:
        document.close()
    return records


def _pick(row: dict[str, Any], field: str) -> str:
    lowered = {normalize(str(k)): v for k, v in row.items()}
    for alias in HEADER_ALIASES[field]:
        value = lowered.get(normalize(alias))
        if value not in (None, ""):
            return str(value).strip()
    return ""


def parse_csv(content: bytes) -> list[dict[str, str]]:
    """Extrait les articles d'un CSV (séparateur , ou ; — Excel FR inclus)."""
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    records: list[dict[str, str]] = []
    for row in reader:
        code = _pick(row, "code")
        if not code:
            continue
        records.append(
            {
                "code": code,
                "designation_en": _pick(row, "designation_en"),
                "designation_fr": _pick(row, "designation_fr"),
                "designation_ar": _pick(row, "designation_ar"),
                "family": _pick(row, "family"),
                "unit": _pick(row, "unit"),
            }
        )
    return records


def parse_json_reference(content: bytes) -> list[dict[str, str]]:
    """Lit une base déjà au format interne ({"articles": [...]}) ou une liste simple."""
    payload = json.loads(content.decode("utf-8"))
    rows = payload.get("articles") if isinstance(payload, dict) else payload
    records: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        records.append(
            {
                "code": code,
                "designation_en": str(row.get("designation_en") or "").strip(),
                "designation_fr": str(row.get("designation_fr") or "").strip(),
                "designation_ar": str(row.get("designation_ar") or "").strip(),
                "family": str(row.get("family") or "").strip(),
                "unit": str(row.get("unit") or "").strip(),
            }
        )
    return records


def parse_reference_file(filename: str, content: bytes) -> list[dict[str, str]]:
    """Choisit le lecteur adapté au fichier envoyé (PDF, CSV, JSON)."""
    name = (filename or "").lower()
    if name.endswith(".json"):
        return parse_json_reference(content)
    if name.endswith((".csv", ".txt", ".tsv")):
        return parse_csv(content)
    if name.endswith(".pdf") or content.lstrip()[:5] == b"%PDF-":
        return parse_pdf_export(content)
    raise ValueError(f"Format de base non pris en charge : {filename!r} (PDF, CSV ou JSON).")
