# Microservice Python de Traduction PDF (`FastAPI` & `Vercel Serverless`)

Microservice Python prêt pour la production permettant de traduire un document PDF vers l'anglais (`en`), le français (`fr`) ou l'arabe (`ar`) tout en **préservant la mise en page vectorielle originale** (logos, tableaux, bordures, graphiques).

## Architecture & Pipeline en 4 étapes

1. **Extraction (`app/extractor.py` — `pdfminer.six`)** :
   - Parcours récursif de l'arbre `LTPage` / `LTTextContainer` / `LTTextLine` / `LTChar`.
   - Récupération des coordonnées exactes `bbox = (x0, y0, x1, y1)` (origine PDF `(0, 0)` en bas à gauche) ainsi que de la taille médiane de police (`LTChar.size`).
   - Distinction intelligente entre paragraphes continus et lignes indépendantes (titres, paires `Clé : Valeur`, cellules de tableaux).

2. **Traduction Mock (`app/translator.py`)** :
   - Fonction `mock_translate(text, target_lang)` déterministe, facilement remplaçable par un appel LLM / DeepL / Google Cloud Translation.

3. **Traitement Arabe RTL (`app/arabic_utils.py` — `arabic-reshaper` + `python-bidi`)** :
   - Si `target_lang == 'ar'`, découpage multi-lignes dans l'ordre **logique**, puis application ligne par ligne de `arabic_reshaper.reshape()` (liaison cursive Presentation Forms) et `bidi.algorithm.get_display()` (réordonnancement visuel RTL UAX #9 préservant les nombres et acronymes latins).
   - Enregistrement et embarquement de la police TTF Unicode **`Amiri-Regular.ttf`** (`assets/fonts/`).

4. **Calque & Fusion (`app/renderer.py` — `reportlab` + `pypdf`)** :
   - Génération d'un calque `reportlab` dessinant un rectangle blanc opaque (`fill=1, stroke=0`) aux coordonnées exactes `(x0, y0, x1, y1)` puis le texte traduit ajusté dynamiquement par-dessus (`drawRightString` sur `x1` pour `ar`, `drawString` sur `x0` pour `en`/`fr`).
   - Fusion page par page sur le document original avec `pypdf` (`PdfReader` / `PdfWriter.add_page()` + `merge_page()`).

---

## Arborescence du projet

```text
.
├── api/
│   └── index.py               # Point d'entrée FastAPI & Vercel Serverless Function
├── app/
│   ├── schemas.py             # Modèles TargetLang (en, fr, ar), TextBlock, ExtractedPage
│   ├── extractor.py           # Étape 1 : Extraction texte + Bounding Boxes (pdfminer.six)
│   ├── translator.py          # Étape 2 : Fonction mock de traduction
│   ├── arabic_utils.py        # Étape 3 : arabic-reshaper + bidi.algorithm.get_display
│   ├── renderer.py            # Étape 4 : Calque ReportLab (fond blanc + TTF) & fusion pypdf
│   ├── pipeline.py            # Orchestration de bout en bout
│   └── sample_generator.py    # Générateur d'un PDF de démonstration
├── assets/fonts/
│   ├── Amiri-Regular.ttf      # Police TTF arabe & latine
│   └── DejaVuSans.ttf         # Police TTF latine Unicode
├── samples/                   # Exemples générés (original + ar, fr, en)
├── tests/
│   └── test_service.py        # Suite de tests pytest (unitaires & API)
├── main.py                    # Lancement local Uvicorn
├── requirements.txt           # Dépendances Python
└── vercel.json                # Configuration Vercel Serverless (inclut assets/fonts/**)
```

---

## Exécution locale

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- **Console web interactive** : `http://localhost:8000/`
- **Documentation Swagger OpenAPI** : `http://localhost:8000/docs`

### Exemple d'appel `cURL`

```bash
curl -X POST "http://localhost:8000/translate" \
  -F "file=@samples/sample_invoice.pdf;type=application/pdf" \
  -F "target_lang=ar" \
  --output translated_ar.pdf
```

---

## Déploiement Vercel Serverless

Le fichier `vercel.json` déclare le preset `fastapi` et exclut les fichiers inutiles du bundle ;
le point d'entrée est `api/index.py` (objet `app`), et les polices TTF de `assets/fonts/`
sont incluses automatiquement dans le bundle de la fonction (Vercel embarque tous les fichiers
du projet pour une app Python ; rien n'est « tree-shaké »).

```bash
vercel deploy --prod
```

Configuration associée :

- `.python-version` → `3.12` (versions Vercel : 3.12 / 3.13 / 3.14).
- `requirements.txt` → versions **épinglées** (une montée de version majeure de `python-bidi`,
  `pypdf` ou `reportlab` peut casser l'import → 500 au runtime).
- `maxDuration: 300` → le plan **Hobby autorise 300 s** par invocation (fluid compute),
  ce qui laisse le temps à la traduction réseau et aux PDF de plusieurs pages.
- **Limite de 4,5 Mo sur la requête ET sur la réponse** : au-delà, Vercel renvoie
  un `413 FUNCTION_PAYLOAD_TOO_LARGE` avant même d'appeler la fonction. Le service :
  1. refuse en amont les fichiers > 4 Mo avec un message clair ;
  2. **compresse automatiquement** le PDF de sortie s'il approche 4,5 Mo ;
  3. renvoie un `413` explicite (avec conseil : découper le PDF ou choisir TXT/DOCX)
     si le fichier reste trop lourd.

### Diagnostic des erreurs HTTP 500

`GET /api/diag` renvoie l'environnement d'exécution (chemins, présence des polices TTF,
versions réellement installées). C'est le premier appel à faire en cas d'erreur.

Toutes les erreurs sont désormais renvoyées en JSON structuré — plus aucun 500 muet :

| Statut | `detail.code` | Cause |
| --- | --- | --- |
| 400 | `invalid_pdf` | Fichier non-PDF, tronqué ou corrompu |
| 400 | `encrypted_pdf` | PDF protégé par mot de passe |
| 400 | `empty_pdf` | Fichier vide / aucune page |
| 413 | `payload_too_large` | Fichier > `PDF_MAX_UPLOAD_MB` (ou > 4,5 Mo sur Vercel) |
| 413 | `too_many_pages` | Plus de `PDF_MAX_PAGES` pages |
| 422 | `pdf_processing_failed` | Rendu/fusion impossible (PDF exotique) |
| 503 | `font_unavailable` | `assets/fonts/*.ttf` absent du bundle déployé |

```json
{
  "detail": {
    "code": "encrypted_pdf",
    "message": "Le PDF est chiffré ou protégé par un mot de passe...",
    "hint": "Retirez la protection par mot de passe du PDF puis réessayez."
  }
}
```

### Variables d'environnement (optionnelles)

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `PDF_MAX_UPLOAD_MB` | `4` | Taille maximale du PDF (Mo) |
| `PDF_MAX_PAGES` | `40` | Nombre maximal de pages par requête |
| `PDF_MAX_BLOCKS_PER_PAGE` | `4000` | Garde-fou sur les PDF pathologiques |
| `TRANSLATOR_PROVIDER` | `auto` | `auto`, `google_web`, `mymemory`, `libretranslate` ou `offline` |
| `PDF_SOURCE_LANG` | `auto` | Langue source (`tr`, `en`, `fr`… ou `auto`) |
| `PDF_TRANSLATE` | `1` | `0` = pas d'appel réseau (PDF nettoyé, sans traduction) |
| `PDF_TRANSLATE_BUDGET_S` | `35` | Budget temps réseau avant assemblage |
| `LIBRETRANSLATE_URL` | `https://libretranslate.com` | Instance LibreTranslate (utile en auto-hébergé) |

---

## Traduction **gratuite**, sans clé API

Trois fournisseurs gratuits sont essayés dans l'ordre, sans carte bancaire :

1. **`google_web`** — point d'entrée public de Google Traduction (`client=gtx`), gratuit et sans clé.
2. **`mymemory`** — API gratuite sans clé (quota quotidien par IP).
3. **`libretranslate`** — instance publique ou **auto-hébergée** (gratuit et sans limite si vous l'hébergez).
4. **`offline`** — glossaire local, dernier recours.

Garanties du pipeline :

- **Aucune fausse traduction** : si aucun fournisseur ne répond, le texte d'origine est
  conservé tel quel (fin du texte de remplissage « نص مترجم »).
- **Codes et références intacts** : conteneurs (`CAAU2973244`), références (`I000503614`),
  e-mails, téléphones, montants, coordonnées et artefacts CSS/JS ne sont **jamais** envoyés
  au moteur ni masqués.
- **Masquage sélectif** : seuls les blocs réellement traduits sont recouverts ; les autres
  gardent leur texte et leur position d'origine (mise en page préservée).
- **Cache de libellés** : un en-tête de tableau répété 50 fois n'est traduit qu'une fois.
- **Traçabilité** : chaque réponse expose `X-Translation-Provider`, `X-Translation-Degraded`,
  `X-Blocks-Translated`, `X-Blocks-Kept` ; `/api/diag` détaille l'ordre des fournisseurs.

### Limites à connaître

Les points d'entrée gratuits ne sont pas contractuels : Google peut limiter l'usage intensif,
et MyMemory plafonne à quelques milliers de caractères par jour. Pour un usage régulier ou
professionnel, **auto-hébergez LibreTranslate** (gratuit, illimité, qualité correcte) et
pointez `LIBRETRANSLATE_URL` dessus.

---

## Éditeur avant export (police, taille, position, corrections)

Ouvrez **`/editor`** (lien « ✎ Éditeur avant export » depuis la page d'accueil).

1. Importez un fichier (PDF, DOCX, TXT/Markdown, image PNG/JPG/WEBP).
2. Cliquez sur **« Préparer l'aperçu »** : la page est rendue en image et chaque
   bloc de texte est listé avec sa traduction.
3. Réglez ce que vous voulez :
   - **Police** : Amiri, Noto Naskh Arabic, Noto Sans Arabic, IBM Plex Sans Arabic,
     DejaVu Sans (multi-script), Roboto / Open Sans / Lato (latin) ;
   - **Taille** : automatique (taille d'origine), imposée en points, ou échelle en % ;
   - **Alignement** : horizontal (droite/centre/gauche) et vertical (haut/milieu/bas) ;
   - **Couleur**, **interligne**, **décalage global** X/Y en points ;
   - **Correction bloc par bloc** : nouveau texte, taille, déplacement, masquage.
4. Choisissez le **format de sortie** puis cliquez sur **« Exporter le document »**.

L'aperçu et l'export passent par le **même code** (`/preview` et `/translate`
partagent le rendu) : ce que vous voyez est ce que vous obtenez.

### Formats

| Entrée | Sortie |
| --- | --- |
| PDF (vectoriel) | PDF (mise en page préservée) |
| Word `.docx` | Word `.docx` |
| TXT / Markdown / CSV | Texte `.txt` |
| Image PNG / JPG / WEBP (OCR si `pytesseract` installé) | Image `.png` |

### API correspondante

```bash
# 1) Préparer l'édition (blocs + aperçu PNG base64)
curl -X POST http://localhost:8000/preview \
  -F "file=@facture.pdf" -F "target_lang=ar" -F "page=1"

# 2) Exporter avec le style et les corrections
curl -X POST http://localhost:8000/translate \
  -F "file=@facture.pdf" -F "target_lang=ar" \
  -F 'style={"font_id":"noto-naskh","font_size":12,"align":"center","color":"#B3121B","offset_x":4}' \
  -F 'edits=[{"block_id":"p0-b3-100.0_700.0_200.0_720.0","text":"Texte corrigé","x0":110,"font_size":13}]' \
  -F "output_format=docx" --output facture_ar.docx
```

Polices proposées par `GET /api/fonts`, formats supportés par `GET /api/formats`.

---

## Annexe — Alternative : déploiement Render (gratuit, sans carte bancaire)

Render héberge le service sans plafond de 4,5 Mo et sans configuration exotique.
Le fichier **`render.yaml`** décrit le service ; il suffit de l'utiliser comme Blueprint.

### Champs exacts à saisir dans Render (New → Web Service)

| Champ Render | Valeur à saisir | Erreur fréquente |
| --- | --- | --- |
| **Name** | `traduction-pdf` | — |
| **Language / Runtime** | `Python 3` | choisir *Node* ou *Docker* |
| **Branch** | `main` | brancher une branche de travail |
| **Root Directory** | *(vide)* | mettre `src` ou `/` alors que le projet est à la racine |
| **Build Command** | `pip install -r requirements.txt` | `npm install`, ou commande vide |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` | **`npm start`** (c'est l'erreur visible sur votre capture), ou un port `8000` codé en dur |
| **Instance Type** | `Free` | — |
| **Python version** | via `.python-version` (3.12) | — |

> ⚠️ Sur Render, le port est fourni par la variable `$PORT`. Une commande
> `uvicorn ... --port 8000` échoue au health check : le service démarre puis est
> marqué « unhealthy ».

### ⚠️ Erreur « Publish Directory » (visible sur votre capture)

Render n'a **pas** de champ « Publish Directory » : ce champ appartient à un
service **Static Site** (site statique). Pour une application FastAPI, le
réglage correct est le *Start Command* ci-dessus. Si Render insiste sur un
répertoire de publication, c'est que le service a été créé en *Static Site* :
supprimez-le et recréez un **Web Service** (ou importez le Blueprint `render.yaml`).

### Variables d'environnement recommandées

`render.yaml` les configure automatiquement pour le plan gratuit :

| Variable | Valeur | Pourquoi |
| --- | --- | --- |
| `PDF_MAX_UPLOAD_MB` | `15` | 512 Mo de RAM : au-delà, risque d'OOM |
| `PDF_MAX_PAGES` | `30` | limite le temps de traitement |
| `PDF_TRANSLATE_BUDGET_S` | `45` | sous la limite Render de 100 s |
| `TRANSLATOR_PROVIDER` | `auto` | chaîne gratuite sans clé |

### Bon à savoir sur le plan gratuit Render

- Le service **s'endort** après 15 min d'inactivité : la première requête peut
  prendre 30 à 50 s (le temps du réveil). C'est normal.
- **Aucun disque persistant** : le service n'écrit rien sur le disque, tout est
  traité en mémoire — c'est déjà le cas.
- `healthCheckPath: /api/health` permet à Render de vérifier le service.

---

## Réponse à l'erreur « 413 » / fichiers volumineux sur Vercel

Vercel plafonne le corps **et** la réponse à 4,5 Mo. Trois cas :

| Situation | Comportement du service |
| --- | --- |
| Fichier source > 4 Mo | `413 payload_too_large` avec message clair, avant tout traitement |
| PDF traduit proche de 4,5 Mo | compression automatique des flux (`compress_pdf`) |
| PDF traduit encore trop lourd | `413` explicite, conseil : découper le PDF, choisir TXT/DOCX, ou passer en Docker |

Pour traiter des documents plus lourds sans changer d'hébergeur, la documentation
Vercel recommande de contourner la limite (upload direct vers un stockage objet),
ce qui sort du cadre de ce microservice.
