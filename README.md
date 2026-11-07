# Europe demand tracker

Tracker de l'intérêt de recherche Google (Google Trends) pour 13 produits de consommation courante, dans 5 marchés européens (France, Italie, Allemagne, Espagne, Royaume-Uni), plus une moyenne Europe. Un script tourne chaque jour sur GitHub, enregistre les données dans `data/trends.json`, et la page `index.html` les affiche.

Google Trends mesure ce que les gens cherchent, pas ce qu'ils achètent. C'est un signal de demande, pas un chiffre de ventes.

## Mise en route

1. **Déposer les fichiers** dans ton dépôt public (Add file > Upload files). Le dossier `.github` est caché dans le Finder du Mac (Cmd + Maj + point pour l'afficher). S'il ne part pas avec le reste : sur GitHub, Add file > Create new file, tape `.github/workflows/update.yml` comme nom, et colle le contenu du fichier `update.yml` fourni.
2. **Activer la page web** : Settings > Pages > Source : Deploy from a branch > Branch : `main`, dossier `/ (root)` > Save. L'adresse sera `https://TON-PSEUDO.github.io/NOM-DU-DEPOT/`.
3. **Lancer la première collecte** : onglet Actions > Update trends > Run workflow. Compte quelques minutes, le script fait des pauses entre les requêtes. Ensuite il tourne seul chaque jour.
4. **Ouvrir la page** et la recharger quand le workflow est vert.

## Modifier les produits ou les pays

Tout se règle dans `config.json`.

- `products` : chaque produit a une `key` (identifiant), un `label` (nom affiché) et un terme de recherche par pays dans `terms`. Les termes sont dans la langue du pays, sinon Google renvoie peu de données. Un terme `"default"` sert de repli pour les pays non listés.
- `anchor` : le produit de référence. Google ne compare que 5 termes par requête et met chaque requête à l'échelle de son propre pic. Le produit de référence figure dans toutes les requêtes et sert à recoller les échelles. Choisis un produit de volume moyen et stable toute l'année. S'il est trop petit ou trop grand par rapport aux autres, la page signale que certaines courbes sont approximatives.
- Nombre de produits : 1 de référence + un multiple de 4 est le plus économe. Avec 13 produits, il y a 3 requêtes par marché, donc 15 par collecte.
- `countries` : ajoute un code pays (ISO à 2 lettres, `GB` pour le Royaume-Uni) et son nom, puis un terme par produit.
- `timeframe` et `recent_weeks` : période analysée (`today 12-m`) et nombre de semaines utilisées pour le niveau et la variation.

## Si Google bloque la collecte

La collecte par défaut utilise `pytrends`, un outil non officiel dont le dépôt est archivé. Les scripts de ce type finissent tôt ou tard par être bloqués par Google (erreur 429). Quand un marché échoue, la page garde ses anciennes données et l'indique. Quand tous échouent, le workflow passe au rouge.

Alternative stable : SerpApi, un service payant avec un petit quota gratuit (vérifie le quota actuel sur serpapi.com).

1. Crée un compte SerpApi et copie ta clé API.
2. Sur GitHub : Settings > Secrets and variables > Actions > New repository secret, nom `SERPAPI_KEY`.
3. Rien d'autre à changer : `collect.py` passe seul sur SerpApi quand la clé existe.
4. Pour rester dans le quota, passe la collecte en hebdomadaire dans `.github/workflows/update.yml` : `cron: "17 5 * * 1"` (chaque lundi). Les données Google Trends sont hebdomadaires, une collecte par semaine suffit.

## Comprendre le code

- `config.json` : les **données d'entrée** (quoi suivre, où).
- `collect.py` : la **logique**. `fetch` va chercher les chiffres, `normalize` les remet sur une même échelle, `compute_stats` calcule le niveau et la variation, `build_europe` fait la moyenne.
- `index.html` : l'**affichage**. Le JavaScript lit `data/trends.json`, dessine le classement, les petites courbes et le graphique.
- `.github/workflows/update.yml` : l'**automatisation** (quand lancer le script, et comment enregistrer le résultat).
- `vendor/chart.umd.js` : la bibliothèque Chart.js (MIT), copiée telle quelle.

Pour tester la page chez toi : lance `python3 -m http.server` dans le dossier, puis ouvre `http://localhost:8000` (il faut un `data/trends.json`, donc une collecte déjà faite).

## Limites

- GitHub désactive les workflows planifiés d'un dépôt public après 60 jours sans activité. Si cela arrive, réactive-le dans l'onglet Actions.
- Chaque marché est mis à l'échelle de son propre produit le plus recherché (100). On ne peut donc pas comparer les volumes entre pays. L'indice Europe est la moyenne des cinq échelles.
- Les variations incluent la saisonnalité (les manteaux montent chaque automne).
