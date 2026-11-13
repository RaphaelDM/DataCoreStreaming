# Carnet de bord : TP Olist

Ce carnet résume ce que j'ai fait, ce que j'ai observé et les décisions que j'ai prises. Le code correspondant est dans `notebooks/01_exploration.ipynb`.

---

## 1. Mise en place

### Environnement Docker

J'ai écrit un `docker-compose.yml` avec deux services :

| Service | Rôle | Détails |
|---|---|---|
| `postgres` | Base de données | Utilisateur, mot de passe et base `olist`. Volume nommé `pgdata` pour ne pas perdre les données si le conteneur est supprimé. Port 5432 ouvert pour s'y connecter depuis mon PC. Contrôle de santé avec `pg_isready`. |
| `jupyter` | Notebooks Python | Image construite avec mon propre `Dockerfile` (Python 3.12 + jupyterlab, pandas, sqlalchemy, psycopg2, matplotlib, seaborn). Port 8888. Le dépôt est monté dans `/workspace`, et `donnees/olist` en **lecture seule** pour ne pas modifier les données brutes par erreur. |

J'ai ajouté `depends_on` avec `condition: service_healthy` : Jupyter attend que Postgres soit vraiment prêt avant de démarrer.

### Données

- J'ai téléchargé l'archive Kaggle et je l'ai décompressée dans `donnees/olist` (9 fichiers CSV, environ 125 Mo).
- `donnees/` est dans le `.gitignore` : les données sont trop lourdes pour GitHub (le fichier de géolocalisation fait 61 Mo à lui seul) et elles se retéléchargent facilement.

### Chargement de `client_geo.sql`

J'ai utilisé l'extension **Database Client** de VS Code à la place de DBeaver (même principe : connexion à `localhost:5432`, base `olist`, puis exécution du script entier).

- Résultat : la table `client_geo` contient bien **99 441 lignes**, 3 colonnes (`customer_id`, `latitude`, `longitude`) et une clé primaire sur `customer_id`.
- Problème rencontré : à la fin, l'extension affiche l'erreur `TypeError: Cannot read properties of null (reading 'name')`. J'ai vérifié avec `SELECT count(*)` : toutes les lignes sont bien là. L'erreur vient de la dernière instruction du script (`ANALYZE client_geo`) qui ne renvoie aucun résultat, et l'extension n'arrive pas à l'afficher. Ce n'est donc pas un problème de données.

---

## 2. Comprendre les données

### Les 9 fichiers

| Fichier | Contenu | Lignes |
|---|---|---|
| `orders` | Les commandes (statut et dates) | 99 441 |
| `customers` | Les clients (code postal, ville, État) | 99 441 |
| `order_items` | Les articles de chaque commande (produit, vendeur, prix, port) | 112 650 |
| `order_payments` | Les paiements de chaque commande | 103 886 |
| `order_reviews` | Les avis laissés par les clients | 99 224 |
| `products` | Les produits (catégorie, poids, dimensions) | 32 951 |
| `sellers` | Les vendeurs | 3 095 |
| `geolocation` | Coordonnées GPS par code postal | 1 000 163 |
| `translation` | Traduction des catégories en anglais | 71 |

Les commandes vont du **4 septembre 2016 au 17 octobre 2018**.

### Comment les tables sont reliées

```
customers ──customer_id── orders ──order_id── order_items ──product_id── products ──category── translation
    │                        │                     └──seller_id── sellers
    └─zip_code_prefix─┐      ├──order_id── order_payments
                      │      └──order_id── order_reviews
                  geolocation (zip_code_prefix)
```

La table centrale est `orders`. Tout tourne autour de `order_id`.
La table `client_geo` (chargée dans Postgres) se relie à `customers` par `customer_id`.

### Ce qui m'a surpris

1. **`customer_id` ne représente pas un client.** Il y a exactement autant de `customer_id` que de commandes (99 441) : un nouvel identifiant est créé à chaque commande. Le vrai client est `customer_unique_id` (96 096 clients différents). Pour savoir si un client revient, il faut donc utiliser `customer_unique_id`.
2. **Certaines tables ont plusieurs lignes par commande :**
   - `order_items` : une ligne par article (9 803 commandes ont plusieurs articles, jusqu'à 21) ;
   - `order_payments` : 2 961 commandes ont été payées en plusieurs fois (par exemple carte + bon d'achat) ;
   - `order_reviews` : 551 commandes ont plusieurs avis ;
   - `geolocation` : un même code postal a plusieurs coordonnées GPS.

   Si je fusionne ces tables directement avec `orders`, les commandes se retrouvent en double. Il faut d'abord les **regrouper** (voir partie 5).

---

## 3. Audit de qualité

Avant de nettoyer, j'ai mesuré les problèmes sans rien modifier. Tout est calculé dans la partie 2 du notebook.

### Bilan

| Fichier | Famille | Problème | Lignes | % |
|---|---|---|---:|---:|
| customers | Cohérence entre fichiers | code postal inconnu dans geolocation | 278 | 0,28 |
| geolocation | Doublons | code postal avec plusieurs coordonnées GPS | 719 317 | 71,92 |
| geolocation | Doublons | lignes complètement identiques | 261 831 | 26,18 |
| geolocation | Valeurs aberrantes | coordonnées en dehors du Brésil | 42 | < 0,01 |
| order_items | Valeurs aberrantes | frais de port > 33 R$ (IQR) | 11 613 | 10,31 |
| order_items | Valeurs aberrantes | prix > 277 R$ (IQR) | 8 427 | 7,48 |
| order_items | Valeurs aberrantes | frais de port = 0 | 383 | 0,34 |
| order_payments | Cohérence entre fichiers | montant payé ≠ prix + port (écart > 1 R$) | 249 | 0,25 |
| order_payments | Valeurs aberrantes | paiement > 344 R$ (IQR) | 7 981 | 7,68 |
| order_payments | Valeurs aberrantes | paiement de 0 | 9 | 0,01 |
| order_payments | Valeurs aberrantes | type de paiement `not_defined` | 3 | < 0,01 |
| order_payments | Valeurs aberrantes | paiement en 0 fois | 2 | < 0,01 |
| order_reviews | Doublons | `review_id` qui apparaît plusieurs fois | 814 | 0,82 |
| order_reviews | Doublons | commande avec plusieurs avis | 551 | 0,56 |
| order_reviews | Valeurs manquantes | titre du commentaire vide | 87 656 | 88,34 |
| order_reviews | Valeurs manquantes | message du commentaire vide | 58 247 | 58,70 |
| orders | Cohérence entre fichiers | commande sans aucun article | 775 | 0,78 |
| orders | Cohérence entre fichiers | commande sans avis | 768 | 0,77 |
| orders | Cohérence entre fichiers | statut `delivered` sans date de livraison | 8 | 0,01 |
| orders | Cohérence entre fichiers | statut `canceled` avec une date de livraison | 6 | 0,01 |
| orders | Cohérence entre fichiers | commande sans paiement | 1 | < 0,01 |
| orders | Cohérence temporelle | remise au transporteur avant la validation | 1 359 | 1,37 |
| orders | Cohérence temporelle | remise au transporteur avant l'achat | 166 | 0,17 |
| orders | Cohérence temporelle | livraison avant la remise au transporteur | 23 | 0,02 |
| orders | Valeurs manquantes | date de livraison client vide | 2 965 | 2,98 |
| orders | Valeurs manquantes | date de remise au transporteur vide | 1 783 | 1,79 |
| orders | Valeurs manquantes | date de validation vide | 160 | 0,16 |
| products | Cohérence entre fichiers | catégorie sans traduction | 13 | 0,04 |
| products | Valeurs aberrantes | poids > 4 300 g (IQR) | 4 551 | 13,81 |
| products | Valeurs aberrantes | poids de 0 g | 4 | 0,01 |
| products | Valeurs manquantes | catégorie, longueur du nom, longueur de la description, nb de photos vides | 610 | 1,85 |
| products | Valeurs manquantes | poids et dimensions vides | 2 | 0,01 |
| sellers | Cohérence entre fichiers | code postal inconnu dans geolocation | 7 | 0,23 |

Aucun article, paiement ou avis ne pointe vers une commande inexistante, et tous les produits et vendeurs de `order_items` existent bien. Les clés principales sont donc fiables.

### Valeurs manquantes

- **Dates de livraison vides (2 965 commandes).** En croisant avec le statut, je vois que presque toutes ces commandes ne sont pas livrées : `shipped` (1 107), `canceled` (619), `unavailable` (609), `invoiced` (314), `processing` (301)… C'est donc **normal**. Seules **8 commandes `delivered`** n'ont pas de date : là c'est une vraie erreur.
- **Commentaires d'avis vides (59 % pour le message, 88 % pour le titre).** Ce n'est pas une erreur : le client peut donner une note sans écrire de commentaire. Ces valeurs vides sont une **information** (« le client n'a pas commenté »).
- **610 produits sans catégorie.** Il manque en même temps la catégorie, la longueur du nom, la longueur de la description et le nombre de photos. On dirait que la fiche produit n'a jamais été complétée.

### Doublons

- **261 831 lignes identiques dans `geolocation` (26 %).** C'est énorme. Je pense que le fichier a été construit en relevant plusieurs adresses par code postal, et que certaines adresses sont tombées sur exactement le même point.
- **Plusieurs coordonnées par code postal.** Il n'y a que 19 015 codes postaux pour plus d'un million de lignes. C'est logique : un code postal couvre une zone, pas un point précis.
- **`review_id` répétés (814).** En regardant les cas, le même avis (même identifiant, même note) est rattaché à **plusieurs commandes différentes**. Mon hypothèse : un client a passé plusieurs commandes en même temps et n'a laissé qu'un seul avis pour l'ensemble.

### Cohérence des dates

Une commande devrait suivre l'ordre **achat → validation → remise au transporteur → livraison**.

- **1 359 commandes** ont été remises au transporteur **avant** la validation du paiement, et **166 avant même l'achat**. Ce n'est pas possible physiquement. Je pense à des erreurs de saisie ou de fuseau horaire côté transporteur.
- **23 commandes** ont été livrées avant d'être remises au transporteur.
- Aucune commande n'est livrée avant l'achat, et aucun avis n'a de réponse avant sa création.
- **Période incomplète :** il n'y a que 4 commandes en septembre 2016, aucune en novembre 2016, 1 en décembre 2016, puis 16 en septembre 2018 et 4 en octobre 2018 (contre plus de 6 000 par mois en 2018). Ces mois ne sont pas complets et vont fausser les analyses par mois.

Ces erreurs sont peu nombreuses (moins de 1,5 %), mais elles peuvent fausser les calculs de délai.

### Cohérence entre fichiers

- **775 commandes sans aucun article.** Elles sont presque toutes `unavailable` (603) ou `canceled` (164) : la commande a été créée mais le produit n'a jamais été envoyé. C'est cohérent.
- **249 commandes où le montant payé ≠ prix + port.** Dans 232 cas, le client a payé **plus** que le total. Mon hypothèse : les **intérêts** du paiement en plusieurs fois, fréquent au Brésil. Dans 17 cas, il a payé **moins**, peut-être à cause d'une réduction.
- **285 codes postaux** (278 clients, 7 vendeurs) n'existent pas dans `geolocation` : on ne pourra pas les placer sur une carte.
- **13 produits** ont une catégorie qui n'est pas dans la table de traduction (`pc_gamer` et `portateis_cozinha_e_preparadores_de_alimentos`).

### Valeurs aberrantes

- **Valeurs impossibles** : 9 paiements de 0 R$, 2 paiements en 0 fois, 3 paiements de type `not_defined`, 4 produits de 0 g, 42 coordonnées GPS en dehors du Brésil. Ce sont clairement des erreurs, mais très peu nombreuses.
- **Frais de port à 0 (383 articles).** Pas forcément une erreur : ça peut être une livraison gratuite (promotion).
- **Valeurs extrêmes (méthode IQR).** 7 à 14 % des prix, frais de port, paiements et poids sont au-dessus du seuil. Mais le prix maximum est de 6 735 R$ et le poids maximum de 40 kg : c'est réaliste pour de l'électroménager ou des meubles. **Ce ne sont pas des erreurs**, juste des produits chers ou lourds. Je ne les supprime pas.
- **Villes** : une même ville peut être écrite de plusieurs façons (avec ou sans accent, majuscules…). Il y a 8 011 villes différentes, mais seulement 5 968 une fois tout mis en minuscules sans accents : environ 2 000 doublons d'écriture.

---

## 4. Nettoyage : mes décisions

Principe : je travaille sur des **copies** pour garder les données d'origine, et je **supprime le moins possible**, car supprimer, c'est perdre de l'information.

| Problème | Ce que j'ai fait | Pourquoi |
|---|---|---|
| Dates lues comme du texte | Lecture avec `parse_dates` | Pour pouvoir comparer les dates et calculer des durées |
| Analyse des délais | Création de `delai_livraison_jours`, `retard_jours`, `en_retard` | Ce sont les indicateurs utiles pour l'analyse (délai médian : 10 jours ; 8 % des commandes livrées sont en retard) |
| Dates de livraison vides | Je garde les lignes | Les commandes non livrées comptent dans le total des commandes ; elles sont simplement exclues des calculs de délai |
| Noms de colonnes `lenght` | Renommées en `length` | Faute de frappe dans le fichier d'origine |
| 610 produits sans catégorie | Catégorie remplacée par `"inconnue"` | Ces produits ont été vendus : les supprimer ferait perdre des ventes |
| Catégories en portugais | Ajout de la traduction anglaise ; si absente, je garde le nom portugais | Plus lisible, et je ne perds pas les 13 produits non traduits |
| 261 831 doublons dans geolocation | `drop_duplicates()` | Des lignes identiques n'apportent aucune information |
| 42 points hors du Brésil | Supprimés | Ce sont des coordonnées impossibles |
| Commentaires vides | Je ne touche à rien | Ce n'est pas une erreur |
| Valeurs extrêmes (IQR) | Je ne touche à rien | Ce sont des produits chers ou lourds, pas des erreurs |

---

## 5. Agrégations

Pour les tables qui ont plusieurs lignes par commande, je ne supprime pas de lignes : je les **résume** avec `groupby()` + `agg()` pour obtenir une seule ligne par commande, sans perdre l'information importante.

| Table | Regroupée par | Ce que je garde | Pourquoi |
|---|---|---|---|
| `order_items` | commande | nombre d'articles, nombre de produits différents, nombre de vendeurs, prix total, frais de port totaux | Le panier de la commande est résumé |
| `order_payments` | commande | montant total payé, nombre de paiements, moyen de paiement le plus utilisé (`mode`), nombre maximum d'échéances | On sait combien et comment le client a payé |
| `order_reviews` | commande | note de l'avis **le plus récent**, nombre d'avis, a laissé un commentaire ou non | Le dernier avis reflète l'avis final du client |
| `geolocation` | code postal | **médiane** de la latitude et de la longitude, ville et État les plus fréquents | La médiane est moins sensible aux points mal placés que la moyenne |

Ensuite, j'ai fusionné ces tables avec `orders` et `customers` avec `merge(how="left")` pour garder toutes les commandes. J'obtiens la table `commandes` : **une ligne par commande** (j'ai vérifié qu'il n'y a pas de doublon d'`order_id`).

---

## 6. Ce qu'il reste à faire et mes doutes

- **Commandes aux dates incohérentes (1 359)** : je ne les ai pas corrigées. Faut-il les exclure des calculs de délai ? Je pense que oui, mais je voudrais vérifier si l'écart est de quelques heures (fuseau horaire) ou de plusieurs jours.
- **Mois incomplets** (fin 2016, septembre–octobre 2018) : à exclure pour les analyses par mois.
- **Écarts de paiement** : je n'ai pas vérifié mon hypothèse des intérêts. Il faudrait regarder si ces commandes ont bien un nombre d'échéances élevé.
- **Noms de villes** : pas encore harmonisés (minuscules, sans accents).
- **Produits à 0 g et paiements à 0** : très peu nombreux, je les ai laissés pour l'instant.
- **Prochaine étape** : charger la table `commandes` dans PostgreSQL.
