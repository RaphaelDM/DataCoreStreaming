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
- Les dossiers générés `donnees/bronze`, `donnees/silver` et `donnees/gold` sont dans le `.gitignore`. **Attention** : `donnees/olist` (les CSV Kaggle, environ 125 Mo) est actuellement commité dans le dépôt, alors qu'il devrait être ignoré lui aussi (le fichier de géolocalisation fait 61 Mo à lui seul, et les données se retéléchargent facilement).

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

Avant de nettoyer, j'ai mesuré les problèmes sans rien modifier. Tout est calculé dans l'étape 3 du notebook.

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

## 4. Nettoyage : architecture médaillon et règles

### Architecture médaillon

Je range le traitement en trois couches. Chaque couche ne lit que la couche d'avant : on sait toujours d'où vient un chiffre du dashboard, et si une règle est fausse on la corrige et on recalcule depuis le brut.

```
 🥉 BRONZE                     🥈 SILVER                          🥇 GOLD
 9 CSV bruts, tels quels  ──►  tables nettoyées             ──►  tables prêtes à l'usage
 (donnees/olist,               + rejets_<table>                  commandes, vendeurs, produits
  fichiers de la boutique)     (raison du rejet)                 (puis étoile, indicateurs_clients,
                                                                  segments_clients)
        pd.read_csv            src/nettoyage.py                  src/agregations.py
```

| Couche | Rôle | Code | PostgreSQL (étape 5) |
|---|---|---|---|
| bronze | garder le brut, sans rien modifier | `pd.read_csv` | schéma `bronze` |
| silver | mêmes lignes, types corrigés, lignes invalides mises de côté avec leur raison | `src/nettoyage.py` | schéma `silver` + `rejets_<table>` |
| gold | regroupements et colonnes calculées pour le dashboard, la boutique et le clustering | `src/agregations.py` | schéma `gold` |

Airflow suivra le même chemin avec les CSV de la boutique : fichier reçu (bronze) → `nettoyage.py` (silver) → `agregations.py` (gold). Aucune règle ne sera réécrite dans les DAG.

### Les dossiers sur le disque

Le script `src/pipeline.py` est le **seul** code qui lit et écrit des fichiers. Il enchaîne les trois couches et range le résultat en Parquet (format qui garde les types : dates en UTC, textes, nombres ; un CSV les perdrait).

```
donnees/
├── olist/                  CSV téléchargés sur Kaggle (lecture seule)
├── bronze/                 9 tables, copie exacte des CSV, TOUT en texte        (62 Mo)
├── silver/                 9 tables nettoyées (types corrigés)                   (54 Mo)
│   └── rejets/             rejets_<table>.parquet, avec la colonne raison_rejet
└── gold/                   commandes, vendeurs, produits, geo_par_code_postal    (24 Mo)
```

- **Bronze en texte** : on ne décide d'aucun type à l'entrée, c'est le rôle de silver. Exemple : le CSV écrit `"09790"` ; lu comme un nombre, pandas en faisait `9790`. En texte, la valeur brute est gardée telle quelle.
- **Lancer** : `docker compose exec jupyter python src/pipeline.py` (les trois couches, environ 35 s), ou une seule couche : `... src/pipeline.py gold`.
- **Contrôle intégré** : la couche gold s'arrête en erreur si le chiffre d'affaires de `commandes`, `vendeurs` ou `produits` diffère de celui des articles silver.
- Les dossiers `bronze/`, `silver/` et `gold/` sont dans le `.gitignore` : ils se régénèrent avec le script.

### Le module `src/nettoyage.py`

- Une fonction par table : `nettoyer_commandes`, `nettoyer_clients`, `nettoyer_articles`, `nettoyer_paiements`, `nettoyer_avis`, `nettoyer_produits`, `nettoyer_vendeurs`, `nettoyer_geolocalisation`, `nettoyer_traductions`.
- Chacune prend un DataFrame brut et renvoie `(propre, rejets)` ; `rejets` contient la colonne `raison_rejet`. Une ligne est rejetée par la **première** règle qu'elle ne respecte pas.
- Aucune lecture ni écriture de fichier dans ces fonctions.
- **Types** : dates converties de l'heure de São Paulo (`America/Sao_Paulo`) vers **UTC** ; montants arrondis au centime (stockés en `NUMERIC(12,2)` dans PostgreSQL : je n'utilise pas le type `Decimal` de Python dans pandas, qui rend tous les calculs très lents) ; codes postaux en texte de 5 chiffres ; villes en minuscules sans accents.
- **Idempotence** : relancer le nettoyage sur des données propres ne change rien (vérifié sur les 9 tables réelles et par un test).
- **Tests** : `tests/test_nettoyage.py` et `tests/test_agregations.py`, un petit DataFrame fait à la main par règle. Lancement : `docker compose exec jupyter python -m pytest` → **30 tests OK**.

### Les règles, problème par problème

Principe : je **supprime le moins possible**. Une commande réelle a fait circuler de l'argent : je la marque plutôt que de la supprimer. Je rejette seulement ce qui est impossible ou inutilisable.

| Table | Problème (audit) | Règle | Pourquoi |
|---|---|---|---|
| toutes | identifiant absent ou mal formé, doublon de clé | **rejet** | Une ligne sans clé fiable ne peut pas être reliée aux autres tables |
| orders | dates lues comme du texte, sans fuseau | conversion en **UTC** depuis `America/Sao_Paulo` | Comparer des dates de sources différentes (la boutique écrit en UTC) |
| orders | 1 382 commandes aux dates incohérentes (transporteur avant validation, etc.) | **gardées**, marquées `dates_incoherentes` ; leurs délais sont vidés en gold | La commande et son montant sont réels ; seuls les délais sont faux |
| orders | 2 965 dates de livraison vides | gardées | Commandes pas encore livrées ou annulées : c'est normal |
| orders | 8 `delivered` sans date, 6 `canceled` avec date | gardées telles quelles | Trop peu nombreuses ; le statut fait foi, les délais restent vides |
| orders | statut inconnu | **rejet** | Impossible de savoir où en est la commande |
| orders | mois incomplets (fin 2016, sept.-oct. 2018) | gardés ; à exclure des graphiques mensuels | Les commandes sont valides, c'est la période qui est incomplète |
| customers / sellers | code postal lu comme un nombre (`01003` → `1003`) | remis en **texte de 5 chiffres** | Sinon les codes de São Paulo (01xxx) ne correspondent plus |
| customers / sellers | villes écrites de plusieurs façons | minuscules, sans accents | « São Paulo » et « sao paulo » sont la même ville |
| customers / sellers | État hors des 27 États | **rejet** | Donnée fausse (aucun cas dans Olist, mais la boutique pourrait en envoyer) |
| customers / sellers | 278 clients / 7 vendeurs avec code postal inconnu de `geolocation` | gardés | Clients placés grâce à `client_geo` ; vendeurs sans point sur la carte |
| order_items | prix absent ou ≤ 0 | **rejet** | Un article vendu a forcément un prix |
| order_items | 383 frais de port à 0 | gardés | Livraison gratuite (promotion), pas une erreur |
| order_items | prix, port extrêmes (IQR) | gardés | Produits chers ou lourds, réalistes |
| order_payments | 3 paiements `not_defined` | **rejet** | 0 R$, sur des commandes annulées : aucune information |
| order_payments | 2 paiements « en 0 fois » | **corrigés en 1** | Un paiement se fait au moins en 1 fois ; le montant est bon |
| order_payments | 6 bons d'achat à 0 R$ | gardés | Un bon d'achat de 0 peut exister (bon déjà consommé) |
| order_payments | 249 écarts payé ≠ prix + port | gardés, mesurés par `ecart_paiement` | **Hypothèse vérifiée** : les 232 commandes où le client a payé plus ont 6,6 échéances en moyenne (contre 2,9) → ce sont les intérêts |
| order_reviews | commentaires vides | gardés, texte vide → manquant | Une note sans commentaire est normale |
| order_reviews | 814 `review_id` répétés | gardés ; clé = (`review_id`, `order_id`) | Un même avis pour un achat groupé de plusieurs commandes |
| order_reviews | note hors de 1 à 5 | **rejet** | Note impossible |
| products | faute `lenght` | renommée `length` | Faute de frappe |
| products | 610 produits sans catégorie | catégorie `inconnue` (`unknown`) | Ils ont été vendus : les supprimer ferait perdre des ventes |
| products | 13 produits sans traduction | 2 traductions ajoutées à la main (`pc_gamer`, `kitchen_portables_and_food_preparers`) | Plus aucune catégorie non traduite |
| products | 4 poids à 0 g, 2 sans dimensions | valeur → manquante, produit gardé | Poids impossible, mais le produit existe |
| geolocation | 279 660 lignes en double (après simplification des villes) | **rejet** | Aucune information en plus |
| geolocation | 42 points hors du Brésil | **rejet** | Coordonnées impossibles |

### Bilan bronze → silver

| Table | Bronze | Silver | Rejetées |
|---|---:|---:|---|
| orders | 99 441 | 99 441 | 0 (1 382 marquées) |
| customers | 99 441 | 99 441 | 0 |
| order_items | 112 650 | 112 650 | 0 |
| order_payments | 103 886 | 103 883 | 3 (`not_defined`) |
| order_reviews | 99 224 | 99 224 | 0 |
| products | 32 951 | 32 951 | 0 |
| sellers | 3 095 | 3 095 | 0 |
| geolocation | 1 000 163 | 720 461 | 279 702 (doublons + hors Brésil) |
| translation | 71 | 74 | 0 (3 ajoutées) |

Les données Olist sont propres sur les clés : presque aucun rejet. Les règles de rejet serviront surtout pour les fichiers de la boutique (robot avec comportements anormaux).

---

## 5. Gold : ce que je garde de chaque CSV pour le dashboard

Pour chaque CSV, je me pose la question : **qu'est-ce que l'utilisateur final (l'administrateur, le client de la boutique) veut lire ?** Les tables qui ont plusieurs lignes par commande sont **résumées** avec `groupby()` + `agg()` dans `src/agregations.py`. Deux niveaux :
- **par commande** → table `commandes` (une ligne par commande) ;
- **par vendeur** et **par produit** → tables `vendeurs` et `produits` (tableaux du dashboard, catalogue de la boutique).

Le résumé **par personne** (`customer_unique_id`, RFM) est l'étape 6.

### Tableau comparatif : avant / après, CSV par CSV

| CSV | Avant | Regroupé par | Colonnes ajoutées | Question de l'utilisateur |
|---|---|---|---|---|
| `orders` | 8 colonnes | — (1 ligne / commande) | `delai_livraison_jours`, **`delai_promis_jours`**, `retard_jours`, `en_retard`, `delai_validation_heures`, `delai_preparation_jours`, `delai_transport_jours`, `mois_achat`, `jour_semaine`, `heure_achat` (heure **locale**), **`statut`** (en français), `est_livree`, **`est_annulee`** | Combien de commandes par période ? Promet-on des délais réalistes ? Où la livraison perd-elle du temps ? |
| `order_items` | 7 colonnes, plusieurs lignes / commande | commande | `nb_articles`, `nb_produits`, `nb_vendeurs`, `nb_categories`, `montant_produits`, `frais_port`, `montant_total`, `part_frais_port`, `prix_article_max`, `poids_total_g`, `date_limite_expedition`, `categorie_principale` | Panier moyen ? Quelles catégories se vendent ? Le port pèse-t-il trop ? |
| `order_payments` | 5 colonnes, plusieurs lignes / commande | commande | `montant_paye`, `nb_paiements`, **`nb_moyens_paiement`**, **`nb_credit_card`, `nb_boleto`, `nb_voucher`, `nb_debit_card`**, **`montant_credit_card`, `montant_boleto`, `montant_voucher`, `montant_debit_card`**, `moyen_principal`, `nb_echeances_max`, `paye_en_plusieurs_fois` | Comment paient les clients ? Combien de R$ par moyen ? Qui combine carte + bon d'achat ? |
| `order_reviews` | 7 colonnes, parfois plusieurs avis / commande | commande | `note` (avis le plus récent), `note_min`, `nb_avis`, `a_laisse_un_commentaire`, **`titre_avis`, `commentaire`** (texte), `date_avis`, **`delai_reponse_jours`**, `avis_negatif` | Quelle satisfaction ? **Que disent les clients mécontents ?** Olist répond-il vite ? |
| `customers` | 5 colonnes | — (1 ligne / `customer_id`) | code postal corrigé, ville harmonisée, `latitude`, `longitude` (`client_geo`) ; dans `commandes` : `numero_commande_client`, `client_revenu` | Où sont les clients (carte) ? Nouveaux clients ou clients fidèles ? |
| `sellers` | 4 colonnes | **vendeur** (table `vendeurs`) | `nb_commandes`, `nb_clients`, `nb_articles`, `chiffre_affaires`, `panier_moyen`, `note_moyenne`, **`part_avis_negatifs`**, `part_en_retard`, `nb_categories`, `premiere_vente`, `derniere_vente`, `lat`, `lng` | Quels vendeurs rapportent le plus ? Lesquels sont mal notés ou en retard ? |
| `sellers` × `customers` | — | commande | **`distance_vendeur_km`**, **`vendeur_meme_etat`** | Le port et le délai dépendent-ils de la distance ? (oui : 13 R$ / 6,6 j sous 100 km, 40 R$ / 21 j au-delà de 2 000 km) |
| `products` | 9 colonnes | **produit** (table `produits`) | catégorie traduite, `nb_ventes`, `nb_commandes`, `prix_median` (prix du catalogue), `prix_min`, `prix_max`, `chiffre_affaires`, `frais_port_moyen`, **`note_moyenne`, `nb_avis`** (étoiles du catalogue), **`volume_cm3`** | Produits les plus vendus ? Quel prix afficher ? Quelle note montrer ? |
| `orders` × `payments` × `items` | — | commande | **`ecart_paiement`** (payé − prix − port) | Combien rapportent les intérêts du paiement en plusieurs fois ? |
| `geolocation` | 5 colonnes, 1 M lignes | code postal | `lat`, `lng` (médiane), `ville`, `etat` (les plus fréquents) | Placer les vendeurs sur la carte |
| `translation` | 2 colonnes | — | 3 catégories ajoutées | — |

En **gras** : les informations ajoutées après relecture « utilisateur final ». Résultat : `commandes` passe de 12 colonnes (`orders` + `customers`) à **68 colonnes** pour 99 441 lignes ; `vendeurs` : 3 095 lignes, 17 colonnes ; `produits` : 32 951 lignes, 20 colonnes.

### Choix à retenir

- **Une colonne par moyen de paiement** (`pd.crosstab` pour le nombre, `pivot_table` pour le montant) : 2 246 commandes sont payées avec 2 moyens. Avec une seule colonne « moyen de paiement », on perdait le bon d'achat.
- **Moyen principal = celui du plus gros montant**, pas `mode()` qui choisit au hasard en cas d'égalité.
- **Texte des avis gardé** : avant, on ne gardait que « a commenté oui/non » ; l'administrateur a besoin de lire les plaintes.
- **Vendeurs : regroupement en deux temps** (vendeur × commande, puis vendeur), sinon une commande de 5 articles compte 5 fois dans la note et le retard. `nb_clients` compte les `customer_unique_id` (la vraie personne).
- **Périodes en heure locale** : les dates sont stockées en UTC, mais le mois, le jour et l'heure d'achat sont calculés à l'heure de São Paulo (une commande à 22 h n'est pas « le lendemain »).
- **Délais vidés pour les dates incohérentes** : la commande reste dans les totaux, mais ne fausse pas les délais moyens.
- **Position des clients** : `client_geo` (fournie, 0 client sans position) plutôt que `geolocation` (un point par code postal, 278 codes clients absents).

### Vérification : bronze et gold ont les mêmes totaux

| Contrôle | Bronze (CSV) | Gold |
|---|---:|---:|
| Total payé | 16 008 872,12 R$ | 16 008 872,12 R$ |
| Total des produits | 13 591 643,70 R$ | 13 591 643,70 R$ |
| Total des frais de port | 2 251 909,54 R$ | 2 251 909,54 R$ |
| Nombre d'articles | 112 650 | 112 650 |
| Nombre de paiements | 103 886 | 103 883 + 3 rejetés |
| CA de la table `vendeurs` | 13 591 643,70 R$ | 13 591 643,70 R$ |
| CA de la table `produits` | 13 591 643,70 R$ | 13 591 643,70 R$ |

La somme des 4 colonnes `montant_<moyen>` redonne bien `montant_paye` pour chaque commande.

### Étape 4 au sens strict

Au sens du TP, l'étape 4 est la partie **silver** (les règles de nettoyage). La partie **gold** (tables `commandes`, `vendeurs`, `produits`) est de l'avance sur l'étape 5 (« choisir les données finales ») et sur le dashboard.

### Ratios de livraison

Calculés à partir de la colonne vrai/faux `en_retard` : la moyenne d'une Series vrai/faux donne la part de vrai (vrai = 1, faux = 0).

**Règle de calcul : toujours sur les commandes livrées** (`retard_jours` rempli : 95 103 commandes sur 99 441). Une commande pas encore livrée a `en_retard = faux` mais elle n'est pas « à l'heure » ; les commandes aux dates incohérentes sont exclues (délais vidés).

| Ratio | Valeur |
|---|---|
| Taux de retard global | **8,1 %** (retard moyen quand en retard : 9,6 jours) |
| Note moyenne : à l'heure / en retard | 4,29 / **2,55** |
| Avis négatifs (note ≤ 2) : à l'heure / en retard | 9 % / **55 %** |
| Par État (≥ 500 commandes) | MA 19,9 %, CE 15,5 %, BA 14,0 %, RJ 13,5 % … MG 5,6 %, PR 5,1 % |
| Par mois | pics en novembre 2017 (14,2 %, Black Friday) et mars 2018 (21,3 %) ; creux en juin 2017 (3,8 %) et juin 2018 (1,4 %) |
| Par distance client – vendeur | 6,4 % sous 100 km → 11,8 % au-delà de 1 000 km |

Conclusion pour le dashboard : le retard est **la** cause principale des avis négatifs. Le taux de retard (global, par État, par vendeur) doit y figurer.

---

## 6. Étape 5 : choisir les données finales

Trois usages : la **boutique** (catalogue, clients, commandes), le **dashboard** administrateur et le **clustering**.

### 6.1 Colonnes gardées

| Table source | Colonne | Gardée ? | Forme | Usage | Justification |
|---|---|---|---|---|---|
| customers | `customer_id` | oui | PK de `silver.clients` | Boutique, Dashboard | Lien commande ↔ client |
| customers | `customer_unique_id` | oui | `CHAR(32)`, indexé | Clustering, Dashboard | Identifie la **personne** (un `customer_id` par commande) |
| customers | `zip_code_prefix` | oui | texte de 5 chiffres | Boutique | Adresse ; le zéro initial est conservé |
| customers | `city`, `state` | oui | minuscules sans accents ; 27 États | Boutique, Dashboard | Validation à l'inscription, CA par État |
| client_geo | `latitude`, `longitude` | oui | jointes aux clients | Dashboard | Carte (0 client sans position) |
| orders | `order_id`, `customer_id` | oui | PK / FK | tous | Clés |
| orders | `order_status` | oui | 8 statuts autorisés (`CHECK`) | Boutique, Dashboard, Clustering | Exclure les annulées des indicateurs |
| orders | 5 dates | oui | `TIMESTAMPTZ` en UTC | Boutique, Dashboard, Clustering | Récence, délai de livraison, retard |
| orders | `dates_incoherentes` | créée | booléen | Dashboard | Délais ignorés pour ces 1 382 commandes |
| order_items | `order_item_id`, `product_id`, `seller_id` | oui | PK composite avec `order_id` / FK | Boutique, Dashboard | Grain de l'étoile |
| order_items | `price`, `freight_value` | oui | `NUMERIC(12,2)` | Boutique, Dashboard, Clustering | CA, panier, port |
| order_items | `shipping_limit_date` | oui | UTC | gold (`date_limite_expedition`) | Gardée par fidélité |
| order_payments | `type`, `installments`, `value`, `sequential` | oui | `CHECK` sur le type ; échéances ≥ 1 | Boutique, Clustering | Choix du paiement à la commande ; hors étoile |
| order_reviews | `review_score` | oui | `SMALLINT` 1 à 5 | Dashboard, Clustering | Satisfaction ; avis le plus récent par commande |
| order_reviews | `title`, `message` | oui | texte, `NULL` si vide | Dashboard (gold) | L'administrateur lit les plaintes |
| order_reviews | dates | oui | UTC | Dashboard (gold) | `delai_reponse_jours` |
| products | catégorie + traduction | oui | `categorie`, `categorie_en` | Boutique, Dashboard | Catalogue par catégorie |
| products | poids, dimensions, nb photos | oui | entiers ; poids > 0 | Boutique, Dashboard | Fiche produit, `volume_cm3` |
| products | longueurs du nom et de la description | silver et gold oui, **étoile non** | entiers | — | Aucun usage dans le dashboard ni le clustering |
| — | `prix_median` (prix catalogue) | créée | médiane des prix vendus | Boutique | Olist n'a pas de prix catalogue |
| sellers | id, zip, ville, État | oui | `silver.vendeurs` | Dashboard | CA par vendeur |
| geolocation | toutes | **gardée** en silver | 720 461 lignes, sans clé | Dashboard | Sert à placer les **vendeurs** (point médian par code postal). Les clients utilisent `client_geo` |

### 6.2 `sql/schema.sql` : bronze, silver, gold

| Schéma | Contenu | Clés |
|---|---|---|
| `bronze` | 9 tables, **tout en `TEXT`**, mêmes noms de colonnes que les CSV (la faute `lenght` est gardée) | aucune |
| `silver` | 9 tables aux types corrects + 9 tables `rejets_<table>` | PK et FK, voir ci-dessous |
| `gold` | `commandes`, `vendeurs`, `produits`, `geo_par_code_postal` + l'étoile (`sql/etoile.sql`) | PK |

Clés de silver :
- **PK** : `commandes(order_id)`, `clients(customer_id)`, `produits(product_id)`, `vendeurs(seller_id)`, `traductions(product_category_name)`, `articles(order_id, order_item_id)`, `paiements(order_id, payment_sequential)`, `avis(review_id, order_id)` (clé composée : un même `review_id` sert plusieurs commandes).
- **FK** : `commandes → clients`, `articles → commandes, produits, vendeurs`, `paiements → commandes`, `avis → commandes`.
- **`geolocalisation`** : pas de PK (plusieurs points par code postal), un index sur le code postal.
- **Règles de nettoyage reprises en `CHECK`** : prix > 0, port ≥ 0, paiement ≥ 0, échéances ≥ 1, note de 1 à 5, statut parmi 8 valeurs, poids > 0. La base refuse ce que `nettoyage.py` aurait dû rejeter.
- **`rejets_<table>`** : colonnes de la table d'origine en `TEXT` (un rejet peut venir d'une valeur illisible) plus `raison_rejet`. Pas de clé.
- **Gold** : tables recréées à chaque chargement à partir des Parquet (types déduits, PK ajoutée), parce qu'elles sont dérivées et que `commandes` a 68 colonnes.

### 6.3 Chargement : `src/chargement.py`

- `python src/chargement.py` enchaîne `schema.sql`, bronze, silver (+ rejets), gold et `etoile.sql` ; une étape seule est possible : `python src/chargement.py silver`.
- `COPY` depuis les Parquet (un `INSERT` ligne à ligne serait trop lent pour 720 000 points de géolocalisation).
- **Rejouable sans doublon** : `TRUNCATE` puis rechargement ; ordre silver imposé par les FK (traductions, clients, vendeurs, produits, commandes, articles, paiements, avis, géolocalisation).
- Les entiers arrivés en `float` dans le Parquet (`44.0`) sont reconvertis avant le `COPY`.
- `public.client_geo` n'est jamais modifiée.

## 7. Le modèle en étoile du dashboard (`sql/etoile.sql`)

La boutique écrit les commandes une par une ; le dashboard lit et agrège des centaines de milliers de lignes. Il lui faut une table de faits entourée de dimensions.

![[Schéma en étoile 1.png]]

| Table | Grain | Colonnes principales |
|---|---|---|
| `fait_ventes` | **un article d'une commande** (PK `order_id`, `order_item_id`) | 4 clés vers les dimensions, `prix`, `frais_port`, `statut`, `delai_livraison_jours`, `en_retard`, `note` |
| `dim_date` | un jour (heure locale de São Paulo) | `date_key` AAAAMMJJ, année, trimestre, mois, `annee_mois`, jour de semaine, **`mois_complet`** |
| `dim_client` | une **personne** pendant une période | `customer_unique_id`, ville, État, lat/lng, `segment`, `version_modele`, `date_debut`, `date_fin`, `est_courant` |
| `dim_produit` | un produit | catégorie (pt et en), poids, dimensions, volume, nb photos |
| `dim_vendeur` | un vendeur | ville, État, lat/lng (médiane du code postal) |

Décisions :
- **Grain = l'article.** Le CA de `fait_ventes` doit donc égaler `SUM(silver.articles.price)` = 13 591 643,70 R$. Le script s'arrête en erreur (`RAISE EXCEPTION`) si le CA ou le nombre de lignes diffère : c'est le signe qu'une jointure duplique ou perd des lignes.
- **Paiements hors de l'étoile.** Un paiement est au niveau de la commande : avec le grain « article », il serait répété sur chaque article et fausserait le CA. S'il faut un jour le CA par moyen de paiement, on ajoutera une seconde table de faits `fait_paiements` (grain : commande × paiement).
- **`dim_client` au niveau de la personne** (`customer_unique_id`) et non du `customer_id`, sinon une même personne apparaît plusieurs fois. Ses attributs viennent de sa commande la plus récente.
- **Dimensions avant les faits** : un fait a besoin des clés de substitution des dimensions ; sans elles les FK échouent. Les nouveaux clients et produits doivent donc être ajoutés avant les nouvelles ventes (Airflow, demi-journée 3).
- **Évolution du segment (SCD type 2).** Quand le segment d'un client change, on **ferme** l'ancienne ligne (`date_fin`, `est_courant = FALSE`) et on en ouvre une nouvelle. Le CA par segment du mois dernier reste ainsi juste ; en écrasant l'ancien segment, il changerait avec le recalcul. `segment` reste `NULL` tant que l'étape 7 n'a pas tourné. Un index unique partiel garantit une seule ligne courante par personne.
- **Délais et retard** calculés en SQL à partir de silver, `NULL` si la commande n'est pas livrée ou si ses dates sont incohérentes (même règle que gold). À comparer avec le taux de 8,1 % de la partie 5 après chargement des vraies données.
- **Note** : avis le plus récent de la commande.
- **Rejouable** : `ON CONFLICT DO NOTHING` / `WHERE NOT EXISTS` ; Airflow réutilisera les mêmes `INSERT` pour les nouvelles commandes.
- **Mois incomplets** : `dim_date.mois_complet` est `FALSE` hors de janvier 2017 à août 2018, pour les exclure facilement des graphiques mensuels.
- **775 commandes sans article** : absentes de `fait_ventes`, sans effet sur le CA.

Schéma dessiné : *à insérer ici (image « Schéma en étoile »)*.

**À mesurer et noter (demi-journée 2)** : `EXPLAIN ANALYZE` du CA par mois et par État sur silver, puis sur l'étoile (les deux requêtes sont en commentaire à la fin de `etoile.sql`). Temps silver : `À REMPLIR` ; temps étoile : `À REMPLIR`.

## 8. Étape 6 : les indicateurs par client

Le clustering se fait à l'échelle de la **personne** : une ligne par `customer_unique_id` dans `indicateurs_clients` (96 096 lignes attendues). Passage de « une ligne par article / paiement / avis » à « une ligne par commande » puis « par personne » avec `groupby().agg()` : somme de `prix + frais_port`, `count` des commandes et des articles, `max` des échéances, moyenne des notes, `max` de la date d'achat.

| Famille | Indicateur |
|---|---|
| Récence | `recence_jours` (jours depuis la dernière commande, par rapport à la **date la plus récente du jeu**, pas à aujourd'hui) |
| Fréquence | `frequence` (nombre de commandes) |
| Montant | `montant_total` (prix + port), `panier_moyen`, `articles_par_commande` |
| Satisfaction | `note_moyenne` (`NULL` si aucun avis) |
| Livraison | `delai_livraison_j`, `part_retard` (calculés sur les commandes livrées, dates cohérentes) |
| Comportement | `nb_categories`, `part_carte`, `echeances_moyennes` |

Points d'attention :
- Compter les personnes avec `customer_unique_id`, jamais avec `customer_id`.
- Les commandes `canceled` et `unavailable` sont exclues du montant et de la fréquence.
- Une personne sans avis a une note vide : elle sera imputée à l'étape 7 (médiane), pas supprimée.

## 9. Étape 7 : le clustering

**Variables retenues (6)** : récence, fréquence, montant total, panier moyen, note moyenne, délai de livraison.

**Transformations**
- imputation par la **médiane** (note et délai manquants) ;
- `log1p` sur fréquence, montant, panier et délai (queues très asymétriques) ;
- standardisation (`StandardScaler`), indispensable car KMeans utilise des distances.

**Choix de k**
- KMeans pour k = 2 à 10 ; courbe du coude (inertie) et score de silhouette sur un **échantillon fixe** (même échantillon pour tous les k, sinon les scores ne sont pas comparables).
- `choisir_k` prend la meilleure silhouette **entre 4 et 8** : en dessous de 4, la segmentation est trop pauvre pour le marketing.
- k retenu : `À REMPLIR` (silhouette : `À REMPLIR`).

**À discuter** : environ 97 % des clients Olist n'ont acheté qu'une fois. La fréquence discrimine donc peu et le segment « fidèles » sera petit : c'est un **résultat**, pas un bug.

**Noms des segments** : `nommer_segments` propose des noms par règles sur les moyennes (fidèles, gros dépensiers, insatisfaits, endormis, nouveaux, occasionnels). Ils sont **à relire** sur les vrais profils et à renommer si besoin : l'heuristique ne remplace pas la lecture. Tableau des profils (moyenne de chaque indicateur par segment) : `À REMPLIR`.

**Enregistrement**
- `modeles/standardisation_v1.joblib`, `modeles/kmeans_v1.joblib`, `modeles/segments_v1.json` ;
- table `segments_clients` (client, segment, version du modèle, date), qui alimente aussi `dim_client.segment` (SCD 2).

**Préparation pour Airflow (demi-journée 3)**
- `attribuer_segments(df, version)` : `predict` sans réentraîner ;
- `calculer_indicateurs(engine, uids=[…])` : recalcule seulement les clients touchés ;
- les fonctions `enregistrer_*` sont des **upserts** (idempotents).

## 10. Ce qu'il reste à faire et mes doutes

- **Lancer le chargement sur les vraies données** : `docker compose exec jupyter python src/chargement.py`, puis noter dans la partie 7 le CA de `fait_ventes` (attendu : 13 591 643,70 R$), le nombre de lignes (112 650) et le nombre de personnes dans `dim_client` (96 096).
- **Schéma SQL à valider sur les vraies données** : les `CHECK` et les FK sont stricts. Si un `COPY` échoue, c'est qu'une règle de `nettoyage.py` laisse passer une ligne que le schéma refuse : corriger la règle, pas le schéma.
- **Noms de colonnes de silver** : `schema.sql` suppose les noms du notebook (`geolocation_lat`, `geolocation_lng`…). `chargement.py` s'arrête avec la liste des colonnes manquantes s'il y a un écart.
- **Étapes 6 et 7 à exécuter** : remplir `k`, la silhouette et le tableau des profils (marqués `À REMPLIR`).
- **Mois incomplets** (fin 2016, septembre–octobre 2018) : à exclure des graphiques mensuels du dashboard (`dim_date.mois_complet`).
- **`donnees/olist` est commité** (125 Mo) alors qu'il devrait être dans le `.gitignore`.
- **Image Jupyter** : `pytest` et `pyarrow` ont été ajoutés au `Dockerfile`. Il faut reconstruire l'image (`docker compose build jupyter`) ; ajouter aussi `psycopg2-binary` s'il n'y est pas.
