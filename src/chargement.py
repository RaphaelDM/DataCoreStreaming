"""Charge les Parquet (bronze / silver / rejets / gold) dans PostgreSQL.

Usage (depuis le conteneur jupyter) :
    python src/chargement.py            # schema.sql + bronze + silver + rejets + gold + etoile.sql
    python src/chargement.py silver     # une seule étape : bronze | silver | gold | etoile

Principes
- Les tables bronze et silver sont créées par sql/schema.sql (types, PK, FK).
- On vide (TRUNCATE) puis on recharge avec COPY : le script est rejouable sans doublon.
- Ordre silver imposé par les clés étrangères : parents avant enfants.
- Les tables gold sont dérivées : elles sont recréées à chaque fois à partir des Parquet.
- La table public.client_geo (fournie par le TP) n'est jamais modifiée.
"""
import io
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2

RACINE = Path(__file__).resolve().parent.parent
DONNEES = Path(os.environ.get("DONNEES", RACINE / "donnees"))
SQL = RACINE / "sql"
DSN = os.environ.get("DATABASE_URL", "postgresql://olist:olist@postgres:5432/olist")

# parents avant enfants (clés étrangères)
ORDRE_SILVER = ["traductions", "clients", "vendeurs", "produits", "commandes",
                "articles", "paiements", "avis", "geolocalisation"]
CLES_GOLD = {"commandes": ["order_id"], "vendeurs": ["seller_id"], "produits": ["product_id"],
             "geo_par_code_postal": ["geolocation_zip_code_prefix"]}


def connexion():
    # DATABASE_URL est au format SQLAlchemy (postgresql+psycopg2://...) : psycopg2 veut postgresql://
    return psycopg2.connect(DSN.replace("postgresql+psycopg2://", "postgresql://", 1))


def colonnes_table(cur, schema, table):
    """Colonnes de la table avec leur type SQL, dans l'ordre."""
    cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                   WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position""",
                (schema, table))
    return cur.fetchall()


# Dans silver (et ses rejets) la faute "lenght" est corrigée en "length" ;
# les tables bronze et rejets_<table> gardent le nom d'origine.
RENOMMER_REJETS = {"product_name_length": "product_name_lenght",
                   "product_description_length": "product_description_lenght"}


def copier(cur, schema, table, df, colonnes, strict=True):
    """COPY d'un DataFrame vers schema.table (colonnes = [(nom, type_sql)]).

    strict=True : une colonne absente du Parquet est une erreur.
    strict=False (rejets) : colonne absente -> NULL, avec un avertissement.
    """
    noms = [c for c, _ in colonnes]
    manquantes = [c for c in noms if c not in df.columns and c != "raison_rejet"]
    if manquantes:
        if strict:
            raise ValueError(f"{schema}.{table} : colonnes absentes du Parquet : {manquantes}")
        print(f"  attention {schema}.{table} : colonnes absentes du Parquet (mises à NULL) : {manquantes}")
    df = df.reindex(columns=noms)          # colonne en trop ignorée, colonne absente -> NULL
    for nom, type_sql in colonnes:         # les entiers arrivent parfois en float (44.0)
        if type_sql in ("integer", "smallint", "bigint"):
            df[nom] = pd.to_numeric(df[nom], errors="coerce").round().astype("Int64")
    tampon = io.StringIO()
    df.to_csv(tampon, index=False, header=False, na_rep="")
    tampon.seek(0)
    cur.copy_expert(f"COPY {schema}.{table} ({', '.join(noms)}) FROM STDIN WITH (FORMAT csv, NULL '')", tampon)
    return len(df)


def appliquer_sql(cur, fichier):
    cur.execute((SQL / fichier).read_text(encoding="utf-8"))


def charger_bronze(conn):
    with conn.cursor() as cur:
        appliquer_sql(cur, "schema.sql")   # recrée bronze + silver vides
        for table in ORDRE_SILVER:
            fichier = DONNEES / "bronze" / f"{table}.parquet"
            df = pd.read_parquet(fichier).astype("string")
            n = copier(cur, "bronze", table, df, colonnes_table(cur, "bronze", table))
            print(f"bronze.{table:<20} {n:>9} lignes")
    conn.commit()


def charger_silver(conn):
    with conn.cursor() as cur:
        for table in reversed(ORDRE_SILVER):
            cur.execute(f"TRUNCATE silver.{table} CASCADE")
        for table in ORDRE_SILVER:
            df = pd.read_parquet(DONNEES / "silver" / f"{table}.parquet")
            n = copier(cur, "silver", table, df, colonnes_table(cur, "silver", table))
            print(f"silver.{table:<20} {n:>9} lignes")
        # lignes rejetées : tout en texte + raison_rejet
        for table in ORDRE_SILVER:
            cur.execute(f"TRUNCATE silver.rejets_{table}")
            fichier = DONNEES / "silver" / "rejets" / f"rejets_{table}.parquet"
            if not fichier.exists():
                continue
            df = pd.read_parquet(fichier).rename(columns=RENOMMER_REJETS).astype("string")
            n = copier(cur, "silver", f"rejets_{table}", df,
                       colonnes_table(cur, "silver", f"rejets_{table}"), strict=False)
            print(f"silver.rejets_{table:<13} {n:>9} lignes")
        # copie des commandes annulées avec leur raison (lit bronze.paiements et silver)
        appliquer_sql(cur, "orders_cancelled.sql")
        cur.execute("SELECT raison_annulation, count(*) FROM silver.orders_cancelled GROUP BY 1 ORDER BY 2 DESC")
        for raison, n in cur.fetchall():
            print(f"silver.orders_cancelled  {n:>9}  {raison}")
    conn.commit()


TYPES_PANDAS = {"object": "TEXT", "string": "TEXT", "bool": "BOOLEAN", "boolean": "BOOLEAN",
                "int64": "BIGINT", "Int64": "BIGINT", "int32": "INTEGER",
                "float64": "DOUBLE PRECISION", "Float64": "DOUBLE PRECISION"}


def type_sql(serie):
    nom = str(serie.dtype)
    if nom.startswith("datetime64"):
        return "TIMESTAMPTZ" if getattr(serie.dt, "tz", None) is not None else "TIMESTAMP"
    return TYPES_PANDAS.get(nom, "TEXT")


def charger_gold(conn):
    """Tables gold : recréées à partir des Parquet, puis clé primaire ajoutée."""
    with conn.cursor() as cur:
        for table, cle in CLES_GOLD.items():
            fichier = DONNEES / "gold" / f"{table}.parquet"
            df = pd.read_parquet(fichier)
            cur.execute(f"DROP TABLE IF EXISTS gold.{table} CASCADE")
            definition = ", ".join(f'"{c}" {type_sql(df[c])}' for c in df.columns)
            cur.execute(f"CREATE TABLE gold.{table} ({definition})")
            cols = colonnes_table(cur, "gold", table)
            n = copier(cur, "gold", table, df, cols)
            cur.execute(f"ALTER TABLE gold.{table} ADD PRIMARY KEY ({', '.join(cle)})")
            print(f"gold.{table:<22} {n:>9} lignes")
    conn.commit()


def charger_etoile(conn):
    with conn.cursor() as cur:
        appliquer_sql(cur, "etoile.sql")
        for notice in conn.notices:
            print(notice.strip())
    conn.commit()


ETAPES = {"bronze": charger_bronze, "silver": charger_silver, "gold": charger_gold, "etoile": charger_etoile}

if __name__ == "__main__":
    demandees = sys.argv[1:] or list(ETAPES)
    with connexion() as conn:
        for etape in demandees:
            print(f"=== {etape}")
            ETAPES[etape](conn)
