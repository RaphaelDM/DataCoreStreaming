"""Pipeline médaillon : bronze -> silver -> gold, écrit dans donnees/bronze, donnees/silver, donnees/gold.

C'est le seul fichier qui lit et écrit sur le disque. Les règles sont dans nettoyage.py (silver)
et agregations.py (gold), qui ne font que transformer des DataFrames.

Utilisation (dans le conteneur jupyter) :
    python src/pipeline.py            # les trois couches
    python src/pipeline.py silver     # une seule couche (la couche d'avant doit exister)
"""

import argparse
import os
import time
from pathlib import Path

import pandas as pd

import agregations
import nettoyage

DONNEES = Path(os.environ.get("DONNEES", "/workspace/donnees"))
SOURCE = DONNEES / "olist"          # les CSV téléchargés sur Kaggle (lecture seule)
BRONZE = DONNEES / "bronze"
SILVER = DONNEES / "silver"
GOLD = DONNEES / "gold"

# nom de la table -> fichier CSV d'origine
CSV_OLIST = {
    "commandes": "olist_orders_dataset.csv",
    "clients": "olist_customers_dataset.csv",
    "articles": "olist_order_items_dataset.csv",
    "paiements": "olist_order_payments_dataset.csv",
    "avis": "olist_order_reviews_dataset.csv",
    "produits": "olist_products_dataset.csv",
    "vendeurs": "olist_sellers_dataset.csv",
    "geolocalisation": "olist_geolocation_dataset.csv",
    "traductions": "product_category_name_translation.csv",
}


def lire(dossier, table):
    return pd.read_parquet(dossier / f"{table}.parquet")


def ecrire(df, dossier, table):
    dossier.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dossier / f"{table}.parquet", index=False)


# --------------------------------------------------------------------------- bronze

def couche_bronze():
    """Les CSV tels quels : toutes les colonnes en texte, aucune règle appliquée.
    (En texte, « 01003 » reste « 01003 » ; c'est la couche silver qui décide des types.)"""
    bilan = []
    for table, fichier in CSV_OLIST.items():
        df = pd.read_csv(SOURCE / fichier, dtype=str)
        ecrire(df, BRONZE, table)
        bilan.append({"table": table, "lignes": len(df), "colonnes": df.shape[1]})
    return pd.DataFrame(bilan)


# --------------------------------------------------------------------------- silver

def couche_silver():
    """Applique nettoyage.py à chaque table bronze. Écrit les tables propres et les rejets."""
    b = {table: lire(BRONZE, table) for table in CSV_OLIST}
    resultats = {}
    resultats["traductions"] = nettoyage.nettoyer_traductions(b["traductions"])
    resultats["commandes"] = nettoyage.nettoyer_commandes(b["commandes"])
    resultats["clients"] = nettoyage.nettoyer_clients(b["clients"])
    resultats["articles"] = nettoyage.nettoyer_articles(b["articles"])
    resultats["paiements"] = nettoyage.nettoyer_paiements(b["paiements"])
    resultats["avis"] = nettoyage.nettoyer_avis(b["avis"])
    resultats["produits"] = nettoyage.nettoyer_produits(b["produits"], resultats["traductions"][0])
    resultats["vendeurs"] = nettoyage.nettoyer_vendeurs(b["vendeurs"])
    resultats["geolocalisation"] = nettoyage.nettoyer_geolocalisation(b["geolocalisation"])

    bilan = []
    for table, (propre, rejets) in resultats.items():
        ecrire(propre, SILVER, table)
        ecrire(rejets, SILVER / "rejets", f"rejets_{table}")
        bilan.append({"table": table, "bronze": len(b[table]), "silver": len(propre), "rejetées": len(rejets)})
    return pd.DataFrame(bilan)


# --------------------------------------------------------------------------- gold

def lire_client_geo():
    """La table client_geo fournie (PostgreSQL)."""
    return pd.read_sql("SELECT customer_id, latitude, longitude FROM client_geo", os.environ["DATABASE_URL"])


def couche_gold():
    """Applique agregations.py aux tables silver : commandes, vendeurs, produits, geo_par_code_postal,
    indicateurs_clients (étape 6)."""
    s = {table: lire(SILVER, table) for table in CSV_OLIST}

    commandes = agregations.enrichir_commandes(s["commandes"])
    geo_cp = agregations.geo_par_code_postal(s["geolocalisation"])
    clients = s["clients"].merge(lire_client_geo(), on="customer_id", how="left")

    positions_clients = (commandes[["order_id", "customer_id"]]
                         .merge(clients[["customer_id", "latitude", "longitude", "customer_state"]], on="customer_id")
                         .drop(columns="customer_id"))
    positions_vendeurs = (s["vendeurs"]
                          .merge(geo_cp, left_on="seller_zip_code_prefix", right_on="geolocation_zip_code_prefix", how="left")
                          [["seller_id", "lat", "lng", "seller_state"]])

    avis = agregations.avis_par_commande(s["avis"])
    gold = {}
    gold["commandes"] = agregations.table_commandes(
        commandes, clients,
        agregations.articles_par_commande(s["articles"], s["produits"]),
        agregations.paiements_par_commande(s["paiements"]),
        avis,
        agregations.distance_par_commande(s["articles"], positions_clients, positions_vendeurs))
    gold["vendeurs"] = agregations.table_vendeurs(s["vendeurs"], s["articles"], s["produits"],
                                                  gold["commandes"], avis, geo_cp)
    gold["produits"] = agregations.table_produits(s["produits"], s["articles"], avis)
    gold["geo_par_code_postal"] = geo_cp
    gold["indicateurs_clients"] = agregations.indicateurs_clients(gold["commandes"])   # étape 6

    # Contrôle : les regroupements ne doivent rien perdre
    ca = s["articles"].price.sum()
    for table, colonne in [("commandes", "montant_produits"), ("vendeurs", "chiffre_affaires"),
                           ("produits", "chiffre_affaires")]:
        assert abs(gold[table][colonne].sum() - ca) < 0.01, f"le chiffre d'affaires de {table} ne correspond pas"
    # indicateurs_clients : une ligne par personne qui a vraiment acheté, et le même total dépensé
    c = gold["commandes"]
    achats = c[~c.est_annulee & c.montant_total.notna()]
    assert len(gold["indicateurs_clients"]) == achats.customer_unique_id.nunique()
    assert abs(gold["indicateurs_clients"].montant_total.sum() - achats.montant_total.sum()) < 0.01

    for table, df in gold.items():
        ecrire(df, GOLD, table)
    return pd.DataFrame([{"table": t, "lignes": len(df), "colonnes": df.shape[1]} for t, df in gold.items()])


COUCHES = {"bronze": couche_bronze, "silver": couche_silver, "gold": couche_gold}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("couches", nargs="*", help="bronze, silver et/ou gold (par défaut : les trois)")
    couches = parser.parse_args().couches or list(COUCHES)
    for nom in couches:
        if nom not in COUCHES:
            parser.error(f"couche inconnue : {nom} (choisir parmi {', '.join(COUCHES)})")
    for nom in couches:
        debut = time.time()
        bilan = COUCHES[nom]()
        print(f"\n=== {nom} ({time.time() - debut:.1f} s) -> {DONNEES / nom}")
        print(bilan.to_string(index=False))
