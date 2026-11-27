"""Ingestion des commandes de la boutique (fichiers CSV de donnees/entrantes/).

Utilisé par le DAG Airflow `olist_ingestion_commandes`, une fois par fichier :

    lire  ->  vérifier les colonnes  ->  découper en 4 tables  ->  nettoyer  ->  charger
                                                                              (une transaction)
    puis le fichier part dans donnees/traitees/AAAA-MM-JJ/ ou donnees/rejetees/ (+ raison.txt).

Les règles de nettoyage ne sont PAS réécrites ici : on appelle celles de nettoyage.py,
les mêmes que pour l'historique Olist.

Garanties
- atomicité : commande, client, articles et paiements sont chargés dans une seule
  transaction ; si une ligne pose problème, rien n'est chargé ;
- idempotence : INSERT … ON CONFLICT DO NOTHING ; recharger un fichier déjà chargé
  (copié deux fois par erreur) ne crée aucun doublon ;
- un fichier invalide est rejeté avec une raison lisible ; une panne passagère (base
  injoignable…) fait échouer la tâche et laisse le fichier en place pour le passage suivant.
"""
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import nettoyage

# Colonnes du CSV écrit par la boutique (voir docs/format_csv_commande.md)
COLONNES = [
    "order_id", "customer_id", "customer_unique_id", "customer_zip_code_prefix",
    "customer_city", "customer_state", "order_status", "order_purchase_timestamp",
    "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date",
    "order_estimated_delivery_date", "order_item_id", "product_id", "seller_id",
    "shipping_limit_date", "price", "freight_value", "payment_sequential",
    "payment_type", "payment_installments", "payment_value",
]

# Colonnes de chaque table silver, dans l'ordre de sql/schema.sql
TABLES = {
    "clients": ["customer_id", "customer_unique_id", "customer_zip_code_prefix",
                "customer_city", "customer_state"],
    "commandes": ["order_id", "customer_id", "order_status", *nettoyage.DATES_COMMANDE,
                  "dates_incoherentes"],
    "articles": ["order_id", "order_item_id", "product_id", "seller_id",
                 "shipping_limit_date", "price", "freight_value"],
    "paiements": ["order_id", "payment_sequential", "payment_type",
                  "payment_installments", "payment_value"],
}
# Parents avant enfants (clés étrangères)
ORDRE_CHARGEMENT = ["clients", "commandes", "articles", "paiements"]
CLES = {"clients": "customer_id", "commandes": "order_id",
        "articles": "order_id, order_item_id", "paiements": "order_id, payment_sequential"}

TOLERANCE_MONTANT = 0.01   # R$ : écart accepté entre le paiement et le total des articles
MAX_FICHIERS_PAR_PASSAGE = 500


class FichierInvalide(Exception):
    """Le fichier ne pourra jamais être chargé tel quel : on le rejette."""


# ---------------------------------------------------------------- sans base ni disque

def verifier_colonnes(df):
    manquantes = [c for c in COLONNES if c not in df.columns]
    en_trop = [c for c in df.columns if c not in COLONNES]
    if manquantes or en_trop:
        raise FichierInvalide(f"colonnes manquantes : {manquantes or 'aucune'} ; "
                              f"colonnes inattendues : {en_trop or 'aucune'}")
    if df.empty:
        raise FichierInvalide("fichier sans aucune ligne d'article")
    if df["order_id"].nunique() != 1:
        raise FichierInvalide(f"{df['order_id'].nunique()} commandes dans le fichier (une seule attendue)")


def decouper(df):
    """Le CSV à plat -> les 4 tables brutes.

    Client, commande et paiement sont répétés sur chaque ligne d'article : on garde une
    seule ligne par clé (le montant du paiement n'est PAS additionné).
    """
    brut = {
        "clients": df[TABLES["clients"]].drop_duplicates(),
        "commandes": df[[c for c in TABLES["commandes"] if c != "dates_incoherentes"]].drop_duplicates(),
        "articles": df[TABLES["articles"]],
        "paiements": df[TABLES["paiements"]].drop_duplicates(),
    }
    # Des lignes qui se contredisent (deux clients ou deux paiements pour une commande)
    # resteraient en double : nettoyage.py les rejettera comme doublons.
    return {nom: t.reset_index(drop=True) for nom, t in brut.items()}


def prendre_en_charge(commandes):
    """La boutique écrit `processing` (« pas encore pris en charge par la chaîne »).
    Une fois chargée, la commande est `approved` : le paiement a été validé à l'achat
    (order_approved_at est renseigné). C'est ce statut que verra le client."""
    commandes = commandes.copy()
    pris = (commandes["order_status"] == "processing") & commandes["order_approved_at"].notna()
    commandes.loc[pris, "order_status"] = "approved"
    return commandes


def nettoyer(tables):
    """Applique les règles de nettoyage.py à chaque table. Une seule ligne rejetée
    suffit à rejeter tout le fichier (une commande est chargée entière ou pas du tout)."""
    propres, raisons = {}, []
    fonctions = {"clients": nettoyage.nettoyer_clients, "commandes": nettoyage.nettoyer_commandes,
                 "articles": nettoyage.nettoyer_articles, "paiements": nettoyage.nettoyer_paiements}
    for nom, fonction in fonctions.items():
        propre, rejets = fonction(tables[nom])
        propres[nom] = propre
        raisons += [f"{nom} : {r}" for r in rejets["raison_rejet"].unique()]
    if raisons:
        raise FichierInvalide(" ; ".join(raisons))

    total_articles = float((propres["articles"]["price"] + propres["articles"]["freight_value"]).sum())
    total_paye = float(propres["paiements"]["payment_value"].sum())
    if abs(total_articles - total_paye) > TOLERANCE_MONTANT:
        raise FichierInvalide(f"montant payé ({total_paye:.2f}) différent du total des articles "
                              f"({total_articles:.2f})")

    propres["commandes"] = prendre_en_charge(propres["commandes"])
    return {nom: propres[nom][TABLES[nom]] for nom in TABLES}


def preparer(df):
    """Toutes les étapes sans base de données. Lève FichierInvalide si le fichier est rejeté."""
    verifier_colonnes(df)
    return nettoyer(decouper(df))


# ---------------------------------------------------------------- base de données

def _python(valeur):
    """Valeur pandas/numpy -> valeur que psycopg2 sait envoyer (None pour les manquants)."""
    if valeur is None or valeur is pd.NA or valeur is pd.NaT:
        return None
    if isinstance(valeur, float) and np.isnan(valeur):
        return None
    if isinstance(valeur, np.generic):
        return valeur.item()
    if isinstance(valeur, pd.Timestamp):
        return valeur.to_pydatetime()
    return valeur


def charger(conn, propres):
    """Charge les 4 tables dans silver en UNE transaction. Renvoie le nombre de lignes
    réellement insérées par table (0 partout si la commande était déjà chargée)."""
    from psycopg2.extras import execute_values

    inseres = {}
    with conn:                       # commit si tout passe, rollback sinon
        with conn.cursor() as cur:
            for nom in ORDRE_CHARGEMENT:
                colonnes = TABLES[nom]
                lignes = [tuple(_python(v) for v in ligne)
                          for ligne in propres[nom].itertuples(index=False, name=None)]
                execute_values(cur, f"""
                    INSERT INTO silver.{nom} ({', '.join(colonnes)}) VALUES %s
                    ON CONFLICT ({CLES[nom]}) DO NOTHING""", lignes)
                inseres[nom] = cur.rowcount
    return inseres


# ---------------------------------------------------------------- fichiers

def fichiers_en_attente(entrantes, limite=MAX_FICHIERS_PAR_PASSAGE):
    """Les CSV finaux, du plus ancien au plus récent. Les fichiers temporaires de la
    boutique (« .commande_….tmp ») sont ignorés : ils peuvent être en cours d'écriture."""
    return sorted(Path(entrantes).glob("commande_*.csv"))[:limite]


def ranger(chemin, donnees, raison=None):
    """Déplace le fichier traité (traitees/AAAA-MM-JJ/) ou rejeté (rejetees/ + .txt)."""
    if raison is None:
        dossier = Path(donnees) / "traitees" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    else:
        dossier = Path(donnees) / "rejetees"
    dossier.mkdir(parents=True, exist_ok=True)
    destination = dossier / chemin.name
    shutil.move(str(chemin), destination)
    if raison is not None:
        (dossier / f"{chemin.name}.txt").write_text(raison + "\n", encoding="utf-8")
    return destination


def traiter_fichier(chemin, conn, donnees):
    """Traite un fichier de bout en bout. Renvoie un petit compte rendu."""
    import psycopg2

    chemin = Path(chemin)
    try:
        df = pd.read_csv(chemin, dtype=str)
        propres = preparer(df)
        try:
            inseres = charger(conn, propres)
        except (psycopg2.IntegrityError, psycopg2.DataError) as erreur:
            # contrainte refusée (produit inconnu, valeur hors limites…) : le fichier ne
            # passera jamais, on le rejette. Les autres erreurs (base injoignable) remontent.
            raise FichierInvalide(f"refusé par la base : {str(erreur).splitlines()[0]}")
    except (FichierInvalide, pd.errors.ParserError, pd.errors.EmptyDataError) as erreur:
        ranger(chemin, donnees, raison=str(erreur))
        return {"fichier": chemin.name, "statut": "rejeté", "raison": str(erreur), "lignes": 0}

    ranger(chemin, donnees)
    return {"fichier": chemin.name, "statut": "traité", "order_id": propres["commandes"].order_id[0],
            "lignes": sum(inseres.values()), "deja_chargee": inseres["commandes"] == 0}
