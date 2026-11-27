# Boutique Olist

## Conception et parcours

Le projet suit les cinq étapes du TP, dans l'ordre :

1. **Pages et comptes** — visiteur : catalogue, recherche, fiche produit, inscription et connexion ; client : panier, commande, historique et déconnexion ; administrateur : connexion dédiée, tableau de bord, liste des clients/segments et déconnexion.
2. **Inscription, connexion, catalogue, panier** — FastAPI, gabarits Jinja, formulaire validé, mot de passe Argon2, cookie de session signé, protection CSRF et requêtes SQL paramétrées. Le panier, limité à 20 références et 10 unités par référence, reste dans la session.
3. **Commande et CSV** — une commande validée écrit un fichier plat par article dans `donnees/entrantes/`. Il est préparé sous un nom temporaire ; la ligne d'historique est enregistrée et le CSV publié par `os.replace` au sein de la même transaction applicative, avec nettoyage du fichier si l'enregistrement échoue. Les comptes et leur historique sont dans le schéma `boutique`, séparé des tables d'analyse.
4. **Espace administrateur** — seuls les comptes `admin` atteignent les routes `/admin`. Le tableau affiche les 20 dernières commandes de `gold.commandes` ; la liste client récupère les segments de `gold.dim_client`.
5. **Robot et sécurité** — `src/robot_clients.py` simule les clients par HTTP ; les fréquences de villes, catégories et paiements sont récupérées des tables nettoyées via une route HTTP. Les tests de `tests/test_boutique_securite.py` couvrent hachage, validation, contrôle d'accès, CSRF et format de commande.

## Prix et données affichées

Le catalogue lit `gold.produits`, préparée par `src/pipeline.py` à partir des données nettoyées. Le prix retenu est `prix_median`, représentatif du prix historique Olist ; les frais sont `frais_port_moyen`. Les produits sans ventes et sans prix/frais disponibles ne sont pas proposés. Les images du TP sont des visuels factices générés en CSS. Les commandes choisissent un vendeur connu dans `silver.articles`.

Le chargement gold actualise `gold.etat_donnees.updated_at` à la fin de la transaction. Le tableau de bord affiche cet horodatage réel de mise à jour.

## Démarrage

1. Préparer les couches et PostgreSQL comme dans le reste du TP (`python src/pipeline.py`, puis `python src/chargement.py` dans l'environnement Jupyter).
2. Copier `.env.example` vers `.env`, remplacer les valeurs d'exemple par une clé de session aléatoire et un mot de passe administrateur d'au moins 12 caractères. Activer `SESSION_HTTPS_ONLY=true` si la boutique est servie derrière HTTPS.
3. Lancer `docker compose up --build boutique`.
4. Ouvrir `http://localhost:8000`. Les tables `boutique.accounts` et `boutique.orders` sont créées au démarrage ; l'administrateur initial est créé une seule fois depuis les variables d'environnement.

Pour les tests locaux : `pip install -r boutique/requirements-dev.txt`, puis `pytest tests/test_boutique_securite.py`.

## Trafic HTTP

Exemple : `python src/robot_clients.py --clients 10 --orders-per-minute 10 --duration-minutes 2 --anomaly-rate 0.05`. Chaque client utilise sa propre session HTTP. Les anomalies comprennent l'inscription avec une ville inconnue, les quantités hors limites et les paniers abandonnés.
