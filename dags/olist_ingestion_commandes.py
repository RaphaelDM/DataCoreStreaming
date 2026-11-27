"""Ingestion des commandes de la boutique.

Toutes les 2 minutes :
1. `lister_fichiers` : les CSV en attente dans donnees/entrantes/ (fichiers temporaires ignorés) ;
2. `traiter_fichier` : UNE tâche par fichier, créée dynamiquement (.expand) : lecture,
   vérification des colonnes, nettoyage (nettoyage.py), chargement dans silver en une
   transaction, puis rangement dans traitees/AAAA-MM-JJ/ ou rejetees/ (+ raison .txt) ;
3. `compte_rendu` : fichiers traités et rejetés, lignes chargées, durée ; écrit dans les
   logs et dans la table public.journal_ingestion.

Toute la logique est dans src/ingestion.py : le DAG ne fait que l'orchestrer.
"""
from datetime import datetime, timezone
from pathlib import Path

from airflow.sdk import dag, get_current_context, task

import ingestion

DONNEES = Path("/opt/airflow/donnees")
CONNEXION = "olist_postgres"


@dag(
    schedule="*/2 * * * *",          # toutes les 2 minutes
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,                   # pas de rattrapage des passages manqués
    max_active_runs=1,               # une exécution à la fois
    tags=["olist", "ingestion"],
)
def olist_ingestion_commandes():

    @task
    def lister_fichiers():
        fichiers = ingestion.fichiers_en_attente(DONNEES / "entrantes")
        print(f"{len(fichiers)} fichier(s) en attente")
        return [str(f) for f in fichiers]

    @task(map_index_template="{{ chemin.split('/')[-1] }}")   # le nom du fichier dans l'interface
    def traiter_fichier(chemin):
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        conn = PostgresHook(postgres_conn_id=CONNEXION).get_conn()
        try:
            resultat = ingestion.traiter_fichier(Path(chemin), conn, DONNEES)
        finally:
            conn.close()
        print(resultat)
        return resultat

    @task(trigger_rule="all_done")   # s'exécute même s'il n'y avait aucun fichier ou si un fichier a planté
    def compte_rendu(fichiers, resultats):
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        resultats = [r for r in (resultats or []) if r]
        traites = [r for r in resultats if r["statut"] == "traité"]
        rejetes = [r for r in resultats if r["statut"] == "rejeté"]
        # une tâche qui a planté (base injoignable, droits…) ne renvoie rien : son
        # fichier est resté dans entrantes/ et sera repris au prochain passage
        en_erreur = len(fichiers) - len(resultats)
        lignes = sum(r["lignes"] for r in resultats)
        debut = get_current_context()["dag_run"].start_date
        fin = datetime.now(timezone.utc)
        duree = (fin - debut).total_seconds()

        print(f"fichiers traités : {len(traites)} (dont déjà chargés : "
              f"{sum(r.get('deja_chargee', False) for r in traites)})")
        print(f"fichiers rejetés : {len(rejetes)}")
        for r in rejetes:
            print(f"  - {r['fichier']} : {r['raison']}")
        print(f"fichiers en erreur (repris au prochain passage) : {en_erreur}")
        print(f"lignes chargées  : {lignes}")
        print(f"durée            : {duree:.1f} s")

        hook = PostgresHook(postgres_conn_id=CONNEXION)
        hook.run("""
            CREATE TABLE IF NOT EXISTS public.journal_ingestion (
                run_id            TEXT PRIMARY KEY,
                debut             TIMESTAMPTZ NOT NULL,
                fin               TIMESTAMPTZ NOT NULL,
                fichiers_traites  INTEGER NOT NULL,
                fichiers_rejetes  INTEGER NOT NULL,
                fichiers_erreur   INTEGER NOT NULL,
                lignes_chargees   INTEGER NOT NULL,
                duree_s           NUMERIC(8,1) NOT NULL
            )""")
        hook.run("""
            INSERT INTO public.journal_ingestion
                (run_id, debut, fin, fichiers_traites, fichiers_rejetes, fichiers_erreur,
                 lignes_chargees, duree_s)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO NOTHING""",
                 parameters=(get_current_context()["run_id"], debut, fin, len(traites),
                             len(rejetes), en_erreur, lignes, round(duree, 1)))

    fichiers = lister_fichiers()
    compte_rendu(fichiers, traiter_fichier.expand(chemin=fichiers))


olist_ingestion_commandes()
