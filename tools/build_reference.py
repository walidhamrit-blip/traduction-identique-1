"""Construit la base de référence d'articles (assets/reference/articles.json).

Usage :
    python tools/build_reference.py "articles trad 2.pdf"
    python tools/build_reference.py articles.csv --out assets/reference/articles.json

Le fichier produit est déployé avec l'application : la base est alors consultée
avant les moteurs de traduction (référence article = clé).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.reference import normalize  # noqa: E402
from app.reference_import import parse_reference_file  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Export PDF/CSV/JSON de la base articles")
    parser.add_argument("--out", type=Path, default=Path("assets/reference/articles.json"))
    args = parser.parse_args()

    content = args.source.read_bytes()
    records = parse_reference_file(args.source.name, content)

    # On conserve les traductions françaises déjà saisies (elles sont précieuses).
    existing: dict[str, dict[str, str]] = {}
    if args.out.is_file():
        try:
            payload = json.loads(args.out.read_text(encoding="utf-8"))
            existing = {str(a.get("code")): a for a in payload.get("articles", [])}
        except (OSError, ValueError):
            existing = {}

    for record in records:
        previous = existing.get(record["code"], {})
        if not record.get("designation_fr"):
            record["designation_fr"] = previous.get("designation_fr", "")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"source": args.source.name, "count": len(records), "articles": records},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    with_fr = sum(1 for r in records if r.get("designation_fr"))
    with_ar = sum(1 for r in records if r.get("designation_ar"))
    print(f"{len(records)} articles écrits dans {args.out}")
    print(f"  avec désignation française : {with_fr}")
    print(f"  avec désignation arabe     : {with_ar}")
    print(f"  désignations anglaises uniques : {len({normalize(r['designation_en']) for r in records if r['designation_en']})}")


if __name__ == "__main__":
    main()
