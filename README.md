# DataCoreStreaming

## Boutique Olist (jours 2 à 5)

Le service web FastAPI/Jinja suit les cinq étapes du TP : parcours et comptes, catalogue et panier, commande atomique en CSV, espace administrateur, robot HTTP et tests de sécurité.

- Conception, démarrage et choix des données : [docs/boutique.md](docs/boutique.md)
- Colonnes des commandes entrantes : [docs/format_csv_commande.md](docs/format_csv_commande.md)
- Préparer les couches de données, copier `.env.example` vers `.env`, puis lancer `docker compose up --build boutique`.
- Le service écoute sur `http://localhost:8000` et écrit les commandes dans `donnees/entrantes/`.