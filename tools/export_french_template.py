"""Génère un modèle CSV pour saisir les désignations françaises validées.

Le fichier produit s'ouvre dans Excel / LibreOffice : il suffit de remplir la
colonne « designation_fr » puis de le réimporter sur le site
(POST /api/reference/import) pour que ces traductions deviennent prioritaires.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.reference import load_index  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=Path("assets/reference/articles.json"))
    parser.add_argument(
        "--out", type=Path, default=Path("assets/reference/designations_fr_a_completer.csv")
    )
    args = parser.parse_args()

    payload = json.loads(args.base.read_text(encoding="utf-8"))
    index = load_index(args.base)

    seen: set[str] = set()
    rows: list[tuple[str, str, str, str]] = []
    for record in payload.get("articles", []):
        english = (record.get("designation_en") or "").strip()
        # On ignore les lignes sans lettre (ponctuation/encodage cassé) : rien à traduire.
        if not english or not any(ch.isalpha() for ch in english) or english.casefold() in seen:
            continue
        seen.add(english.casefold())
        article = index.find_code(record["code"])
        proposé = index.french_for(article) if article else ""
        rows.append(
            (
                record["code"],
                english,
                (record.get("designation_fr") or "").strip(),
                proposé,
            )
        )
    rows.sort(key=lambda row: row[1].casefold())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["code", "designation_en", "designation_fr", "proposition_auto"])
        writer.writerows(rows)

    print(f"{len(rows)} désignations uniques écrites dans {args.out}")
    print("Remplissez la colonne 'designation_fr' puis réimportez le fichier.")


if __name__ == "__main__":
    main()
