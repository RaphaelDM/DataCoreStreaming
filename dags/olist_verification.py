"""DAG de vérification de l'installation (étape 1 d'Airflow).

À lancer à la main depuis l'interface. Il vérifie les trois choses dont les vrais
DAG auront besoin :
1. la connexion « olist_postgres » vers notre base ;
2. l'accès à notre code (src/) et au modèle de clustering (modeles/) ;
3. l'accès au dossier des commandes de la boutique (donnees/entrantes/).

Les trois vérifications sont indépendantes (elles tournent en parallèle) ; une
dernière tâche les réunit dans un compte rendu.
"""
from pathlib import Path

from airflow.sdk import dag, task

DONNEES = Path("/opt/airflow/donnees")


@dag(schedule=None, catchup=False, tags=["olist", "verification"])
def olist_verification():

    @task
    def verifier_base():
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        hook = PostgresHook(postgres_conn_id="olist_postgres")
        commandes = hook.get_first("SELECT count(*) FROM gold.commandes")[0]
        segments = hook.get_first("SELECT count(*) FROM gold.segments_clients")[0]
        print(f"gold.commandes : {commandes} lignes, gold.segments_clients : {segments} lignes")
        return {"commandes": commandes, "segments": segments}

    @task
    def verifier_code_et_modele():
        import clustering
        import nettoyage  # noqa: F401  (on vérifie seulement que l'import marche)
        _, kmeans, noms, meta = clustering.charger_modele("v1")
        print(f"modèle {meta['version']} : k = {meta['k']}, segments = {list(noms.values())}")
        return {"k": meta["k"], "segments": list(noms.values())}

    @task
    def compter_entrantes():
        fichiers = sorted((DONNEES / "entrantes").glob("commande_*.csv"))
        print(f"{len(fichiers)} fichier(s) en attente dans donnees/entrantes/")
        return len(fichiers)

    @task
    def compte_rendu(base, modele, entrantes):
        print("=== Installation OK ===")
        print(f"base     : {base}")
        print(f"modèle   : {modele}")
        print(f"entrantes: {entrantes} fichier(s) en attente")

    compte_rendu(verifier_base(), verifier_code_et_modele(), compter_entrantes())


olist_verification()
