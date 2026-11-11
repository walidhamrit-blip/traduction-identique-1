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

### Mise en forme strictement identique à l'original

La méthode a changé : le texte d'origine n'est plus **recouvert** par un rectangle,
il est **supprimé par rédaction PDF** (`Page.apply_redactions`, voir
`app/redaction.py`). Le fond du document reste alors bit pour bit intact.

| Paramètre | Effet |
| --- | --- |
| `text=PDF_REDACT_TEXT_REMOVE` | seul le texte est retiré |
| `graphics=PDF_REDACT_LINE_ART_NONE` | fonds de cellules, bandeaux et bordures conservés |
| `images=PDF_REDACT_IMAGE_NONE` | logos, trames et scans conservés |

C'est la seule méthode qui reste fidèle quand une cellule colorée est **plus
petite que son texte**, quand le fond est une **image**, ou quand la bande a une
largeur irrégulière — cas où un rectangle, même bien coloré, ne peut pas
reproduire exactement le décor.

Le texte traduit reprend la **couleur de l'encre d'origine** mesurée sur la page
(texte blanc sur bandeau gris reste blanc). Si la rédaction est indisponible, le
rendu retombe automatiquement sur les masques colorés mesurés.


Le calque de traduction recouvre le texte d'origine par un rectangle. Ce rectangle
était peint en **blanc pur**, ce qui laissait des « remplissages blancs » très
visibles sur les documents à fonds colorés : bandeaux gris, lignes alternées,
cellules surlignées, texte blanc sur fond sombre.

Le rendu mesure donc désormais les **couleurs réelles** de la page
(`app/page_colors.py`) :

- **fond** : couleur dominante autour du bloc → le masque reprend exactement cette
  couleur ; sur un fond non uniforme (photo, dégradé), rien n'est masqué plutôt que
  de peindre un rectangle visible ;
- **encre** : couleur la plus éloignée du fond dans le bloc → le texte traduit garde
  la couleur d'origine (texte blanc sur bandeau gris reste blanc, texte noir sur
  blanc reste noir).

La couleur choisie dans l'éditeur reste prioritaire si elle diffère du réglage par
défaut. Coût : un rendu à 100 dpi par page (quelques dizaines de millisecondes).

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `PDF_PAGE_COLORS` | `1` | Analyse des couleurs réelles (fond + encre) avant le rendu |

### Robustesse du rendu (plus de « pdf_processing_failed » sur un PDF valide)

ReportLab contient des `assert` internes (`pdfbase/ttfonts.py`) qui peuvent interrompre
tout l'export sur un document pourtant lisible. Le rendu passe donc par une chaîne de
secours : si un moteur échoue, le suivant prend le relais automatiquement.

1. **ReportLab + pypdf** — rendu de référence, style complet (police, taille, couleur,
   alignement, interligne, décalages).
2. **Moteur interne + pypdf** — `app/pdf_writer.py`, un écrivain PDF autonome (polices
   TrueType embarquées, table `/ToUnicode` : le texte reste sélectionnable), sans aucun
   `assert` tiers. Le style est simplifié, la mise en page et les positions sont
   conservées. L'utilisateur voit un avertissement explicite.
3. **Moteur interne + PyMuPDF** — fusion indépendante, si `pypdf` lui-même est en cause.
4. **Dernier recours** — erreur `pdf_processing_failed` avec un conseil concret
   (export TXT ou DOCX, qui ne passe pas par le rendu PDF).

`GET /api/diag` inclut désormais un auto-test `render` qui indique, sans lire les logs,
quel moteur répond réellement dans l'environnement déployé
(`reportlab`, `interne`, `merge_pymupdf`, `font_used`).

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
| 422 | `scanned_pdf_no_text` | Document numérisé (image) : aucune couche de texte, à passer à l'OCR |
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

### Documents numérisés (scans) et limite de 4,5 Mo

Un scan est un **PDF fait d'images** : il pèse vite 5 à 30 Mo, donc la plateforme
Vercel le refuse *avant* d'atteindre le service (erreur **413**).

Trois changements :

1. **Message explicite.** Un 413 renvoie désormais un JSON lisible
   (`payload_too_large`) qui cite la limite réelle et une solution concrète
   (« scannez en 150 dpi noir et blanc, ou découpez le PDF »). L'interface web
   affiche le même conseil **avant** l'envoi, dès qu'un fichier dépasse 4,5 Mo.
2. **Allègement automatique.** Entre la marge interne et 4,5 Mo, le PDF est
   ré-encodé (`shrink_pdf`) : les pages **sans texte** sont rastérisées en JPEG à
   150 dpi (elles restent parfaitement lisibles), les pages contenant du texte sont
   recopiées telles quelles. Mesure : un scan de 33 Mo tombe à 0,6 Mo en moins
   d'une seconde. La sortie est aussi allégée si elle approche la limite.
3. **Plus de faux succès.** Un scan sans couche texte ne peut pas être traduit :
   le service répond `422 scanned_pdf_no_text` avec la marche à suivre (OCR) au
   lieu de renvoyer silencieusement l'original inchangé.

> Le service **ne fait pas d'OCR** : il traduit le texte présent dans le PDF.
> Pour un document numérisé, passez-le d'abord dans un OCR (Acrobat →
> « Numérisation et OCR », ou l'option OCR du scanner), puis envoyez le PDF obtenu.

Réglages associés (variables d'environnement) :

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `PDF_IMAGE_OPTIMIZE` | `1` | Active l'allègement automatique des PDF trop lourds |
| `PDF_IMAGE_TARGET_MB` | `2.6` | Poids visé après allègement |
| `PDF_IMAGE_MAX_DPI` | `150` | Résolution maximale du ré-encodage des pages scannées |

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

## Base de référence d'articles (traductions prioritaires)

L'entreprise possède une liste d'articles avec leur désignation officielle. Pour ces
articles, la traduction validée **prime toujours** sur un moteur automatique : il
serait absurde de laisser un traducteur en ligne réinventer
« Spare Part, Concealed Cistern, Drainage Group ».

### Fonctionnement

La base est consultée **avant** les fournisseurs réseau, dans cet ordre :

1. **référence article** (ex. `16000000196`) trouvée dans le segment → traduction
   officielle de cet article. C'est la clé de référence : elle fonctionne même
   quand la référence est une ligne isolée dans une grande facture ;
2. sinon, **désignation** comparée au segment (casse, accents, ponctuation et ordre
   des mots ignorés) ;
3. sinon, traduction automatique habituelle (inchangée).

Un segment hors base (« Total HT », titres, totaux) garde le comportement normal :
la base enrichit, elle ne remplace pas la traduction.

### Contenu livré

- `assets/reference/articles.json` — **369 articles** extraits de l'export
  « Liste article par Tarif » : référence, désignation anglaise, désignation arabe,
  famille, unité de vente ;
- `assets/reference/terminologie_fr.json` — terminologie métier anglais → français
  (~215 entrées : *wall hang* → suspendu, *concealed cistern* → réservoir encastré,
  *spare part* → pièce détachée…). Elle permet de traduire immédiatement les
  désignations vers le français, en conservant les marques et les dimensions ;
- `assets/reference/designations_fr_a_completer.csv` — **modèle à remplir** : une
  ligne par désignation, avec une proposition automatique. Toute valeur saisie dans
  la colonne `designation_fr` devient prioritaire.

### Alimenter ou mettre à jour la base

| Méthode | Commande |
| --- | --- |
| Nouvel export PDF de la gestion | `python tools/build_reference.py "Liste article.pdf"` |
| Modèle français à remplir | `python tools/export_french_template.py` |
| Mise à jour sans redéploiement | `POST /api/reference/import` (PDF, CSV ou JSON) |

Vérifications disponibles :

- `GET /api/reference` → nombre de références et langues couvertes ;
- `GET /api/reference/search?q=16000000196` → l'article et sa traduction ;
- `GET /api/diag` → bloc `reference` (base chargée ou non).

> Sur Vercel, le système de fichiers n'est pas partagé entre instances : un import
> via l'API s'applique à l'instance courante et renvoie le fichier `articles.json`
> à versionner. Pour une mise à jour durable, commitez
> `assets/reference/articles.json` (ou définissez `REFERENCE_ADMIN_TOKEN` pour
> protéger l'import).

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
