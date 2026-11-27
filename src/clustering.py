"""Étape 7 : segmentation des clients avec KMeans.

Usage (depuis le conteneur jupyter) :
    python src/clustering.py            # entraîne la version v1 et remplit les segments
    python src/clustering.py v2         # entraîne une autre version

Ce que fait le script
1. lit gold.indicateurs_clients (une ligne par personne, étape 6) ;
2. prépare les variables : médiane pour les trous, logarithme pour les variables
   très asymétriques, puis standardisation (KMeans travaille sur des distances) ;
3. entraîne KMeans pour k = 2 à 10, mesure l'inertie (coude) et la silhouette ;
4. garde le k qui a la meilleure silhouette entre 4 et 8 ;
5. décrit et nomme chaque segment ;
6. enregistre la préparation et le modèle avec joblib dans modeles/ ;
7. écrit le segment de chaque personne dans gold.segments_clients et dans
   gold.dim_client (dimension à évolution lente de type 2).

Les fonctions sans accès à la base (preparation, evaluer_k, choisir_k, entrainer,
nommer_segments, attribuer_segments) servent aussi au DAG Airflow : il chargera le
modèle enregistré et appellera attribuer_segments sur les clients touchés, sans
réentraîner.
"""
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import silhouette_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

RACINE = Path(__file__).resolve().parent.parent
MODELES = Path(os.environ.get("MODELES", RACINE / "modeles"))

# 5 indicateurs : R, F, M, satisfaction, logistique.
# panier_moyen n'est pas retenu : 97 % des personnes n'ont qu'une commande, il est donc
# presque égal à montant_total (corrélation de Spearman ~0,99) et doublerait le poids du montant.
# Variables à queue très longue : log1p avant de standardiser.
VARIABLES_LOG = ["nb_commandes", "montant_total", "delai_livraison_moyen_jours"]
VARIABLES_BRUTES = ["recence_jours", "note_moyenne"]
# ColumnTransformer sort les colonnes transformées d'abord, puis les autres : VARIABLES
# suit ce même ordre, pour que les colonnes de X et les centres KMeans soient dans
# l'ordre de VARIABLES.
VARIABLES = VARIABLES_LOG + VARIABLES_BRUTES

K_MIN, K_MAX = 2, 10                # k testés
K_CHOIX_MIN, K_CHOIX_MAX = 4, 8     # k acceptables pour le marketing
TAILLE_ECHANTILLON = 10_000         # silhouette en O(n²) : échantillon fixe
GRAINE = 42

# Pour nommer un segment : son trait le plus marqué (centre standardisé, signe).
TRAITS = {
    ("recence_jours", +1): "endormis",
    ("recence_jours", -1): "récents",
    ("nb_commandes", +1): "fidèles",
    ("nb_commandes", -1): "acheteurs uniques",
    ("montant_total", +1): "gros paniers",
    ("montant_total", -1): "petits paniers",
    ("note_moyenne", +1): "satisfaits",
    ("note_moyenne", -1): "insatisfaits",
    ("delai_livraison_moyen_jours", +1): "livraisons longues",
    ("delai_livraison_moyen_jours", -1): "livraisons rapides",
}


# ---------------------------------------------------------------- sans base de données

def preparation():
    """Médiane pour les trous, log1p sur les variables asymétriques, standardisation.

    Tout est dans un seul Pipeline : les médianes et les moyennes/écarts-types appris
    à l'entraînement sont réutilisés tels quels pour les nouveaux clients.
    """
    log = ColumnTransformer(
        [("log", FunctionTransformer(np.log1p), list(range(len(VARIABLES_LOG))))],
        remainder="passthrough",
        verbose_feature_names_out=False,
    )
    return Pipeline([
        ("mediane", SimpleImputer(strategy="median")),
        ("log", log),
        ("standardisation", StandardScaler()),
    ])


def evaluer_k(X, ks=range(K_MIN, K_MAX + 1), taille_echantillon=TAILLE_ECHANTILLON, graine=GRAINE):
    """Inertie (méthode du coude) et silhouette pour chaque k.

    La silhouette est calculée sur le même échantillon pour tous les k, sinon les
    scores ne sont pas comparables.
    """
    rng = np.random.default_rng(graine)
    idx = rng.choice(len(X), size=min(taille_echantillon, len(X)), replace=False)
    lignes = []
    for k in ks:
        km = KMeans(n_clusters=k, n_init=10, random_state=graine).fit(X)
        sil = silhouette_score(X[idx], km.labels_[idx])
        lignes.append({"k": k, "inertie": km.inertia_, "silhouette": sil})
    return pd.DataFrame(lignes)


def choisir_k(scores, k_min=K_CHOIX_MIN, k_max=K_CHOIX_MAX):
    """Meilleure silhouette parmi les k acceptables pour le marketing."""
    candidats = scores[(scores["k"] >= k_min) & (scores["k"] <= k_max)]
    return int(candidats.loc[candidats["silhouette"].idxmax(), "k"])


def entrainer(X, k, graine=GRAINE):
    return KMeans(n_clusters=k, n_init=10, random_state=graine).fit(X)


def nommer_segments(centres, variables=VARIABLES):
    """Nom de chaque segment d'après ses traits les plus marqués.

    centres : centres KMeans dans l'espace standardisé (0 = moyenne, 1 = un écart-type).
    Le nom vient du trait le plus éloigné de la moyenne ; si deux segments ont le
    même, on ajoute leur deuxième trait. Les noms sont à relire sur les profils.
    """
    def traits(centre):
        ordre = np.argsort(-np.abs(centre))
        return [TRAITS[(variables[i], 1 if centre[i] > 0 else -1)] for i in ordre]

    tous = [traits(c) for c in centres]
    noms = [t[0] for t in tous]
    profondeur = 1
    while len(set(noms)) < len(noms) and profondeur < len(variables):
        profondeur += 1
        doublons = {n for n in noms if noms.count(n) > 1}
        noms = [", ".join(t[:profondeur]) if n in doublons else n for n, t in zip(noms, tous)]
    return {i: nom for i, nom in enumerate(noms)}


def attribuer_segments(df, prep, kmeans, noms):
    """Segment de chaque personne (predict, sans réentraîner)."""
    ids = kmeans.predict(prep.transform(df[VARIABLES]))
    return pd.DataFrame({
        "customer_unique_id": df["customer_unique_id"].to_numpy(),
        "segment_id": ids.astype(int),
        "segment": [noms[i] for i in ids],
    })


def profils(df, segments):
    """Moyenne de chaque indicateur (valeurs réelles) et taille de chaque segment."""
    d = df[VARIABLES].assign(segment=segments["segment"].to_numpy())
    p = d.groupby("segment").mean().round(2)
    p.insert(0, "nb_clients", d.groupby("segment").size())
    p.insert(1, "part_pct", (100 * p["nb_clients"] / len(d)).round(1))
    return p.sort_values("nb_clients", ascending=False)


# ---------------------------------------------------------------- fichiers du modèle

def enregistrer_modele(prep, kmeans, meta, version, dossier=MODELES):
    dossier.mkdir(parents=True, exist_ok=True)
    joblib.dump(prep, dossier / f"standardisation_{version}.joblib")
    joblib.dump(kmeans, dossier / f"kmeans_{version}.joblib")
    (dossier / f"segments_{version}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def charger_modele(version, dossier=MODELES):
    prep = joblib.load(dossier / f"standardisation_{version}.joblib")
    kmeans = joblib.load(dossier / f"kmeans_{version}.joblib")
    meta = json.loads((dossier / f"segments_{version}.json").read_text(encoding="utf-8"))
    noms = {int(i): n for i, n in meta["noms"].items()}
    return prep, kmeans, noms, meta


# ---------------------------------------------------------------- base de données

def connexion():
    import psycopg2
    dsn = os.environ.get("DATABASE_URL", "postgresql://olist:olist@postgres:5432/olist")
    return psycopg2.connect(dsn.replace("postgresql+psycopg2://", "postgresql://", 1))


def lire_indicateurs(conn, uids=None):
    """gold.indicateurs_clients, en entier ou pour une liste de personnes."""
    requete = f"SELECT customer_unique_id, {', '.join(VARIABLES)} FROM gold.indicateurs_clients"
    params = None
    if uids is not None:
        requete += " WHERE customer_unique_id = ANY(%s)"
        params = (list(uids),)
    with conn.cursor() as cur:
        cur.execute(requete, params)
        colonnes = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=colonnes).astype({v: float for v in VARIABLES})


def enregistrer_segments(conn, segments, version, date=None):
    """Upsert dans gold.segments_clients, puis mise à jour de gold.dim_client (SCD 2).

    dim_client :
    - pas encore de segment (NULL) : on le renseigne sur la ligne courante (ce n'est pas
      un changement, juste la première attribution) ;
    - segment différent : on ferme la ligne courante (date_fin, est_courant = false) et
      on insère une nouvelle ligne courante. Les ventes passées restent rattachées à
      l'ancienne ligne, donc à l'ancien segment.
    Rejouable : relancé avec les mêmes segments, rien ne change.
    """
    date = date or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gold.segments_clients (
                customer_unique_id  TEXT PRIMARY KEY,
                segment_id          INTEGER     NOT NULL,
                segment             TEXT        NOT NULL,
                version_modele      TEXT        NOT NULL,
                date_attribution    TIMESTAMPTZ NOT NULL
            )""")
        cur.execute("""
            CREATE TEMP TABLE tmp_segments (customer_unique_id TEXT, segment_id INTEGER, segment TEXT)
            ON COMMIT DROP""")
        buffer = segments[["customer_unique_id", "segment_id", "segment"]].to_csv(
            index=False, header=False, sep="\t")
        cur.copy_expert("COPY tmp_segments FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t')",
                        io.StringIO(buffer))

        cur.execute("""
            INSERT INTO gold.segments_clients AS s
                (customer_unique_id, segment_id, segment, version_modele, date_attribution)
            SELECT customer_unique_id, segment_id, segment, %(v)s, %(d)s FROM tmp_segments
            ON CONFLICT (customer_unique_id) DO UPDATE
               SET segment_id = EXCLUDED.segment_id, segment = EXCLUDED.segment,
                   version_modele = EXCLUDED.version_modele,
                   date_attribution = EXCLUDED.date_attribution
             WHERE (s.segment, s.version_modele) IS DISTINCT FROM (EXCLUDED.segment, EXCLUDED.version_modele)
            """, {"v": version, "d": date})
        upserts = cur.rowcount

        # 1re attribution : on complète la ligne courante
        cur.execute("""
            UPDATE gold.dim_client d
               SET segment = t.segment, version_modele = %(v)s
              FROM tmp_segments t
             WHERE d.customer_unique_id = t.customer_unique_id
               AND d.est_courant AND d.segment IS NULL
            """, {"v": version})
        premieres = cur.rowcount

        # changement de segment : on ferme l'ancienne ligne et on en ouvre une nouvelle
        cur.execute("""
            WITH fermees AS (
                UPDATE gold.dim_client d
                   SET date_fin = %(d)s, est_courant = FALSE
                  FROM tmp_segments t
                 WHERE d.customer_unique_id = t.customer_unique_id
                   AND d.est_courant AND d.segment IS DISTINCT FROM t.segment
                RETURNING d.*, t.segment AS nouveau_segment
            )
            INSERT INTO gold.dim_client
                (customer_unique_id, code_postal, ville, etat, latitude, longitude,
                 segment, version_modele, date_debut, date_fin, est_courant)
            SELECT customer_unique_id, code_postal, ville, etat, latitude, longitude,
                   nouveau_segment, %(v)s, %(d)s, NULL, TRUE
              FROM fermees
            """, {"v": version, "d": date})
        changements = cur.rowcount
    conn.commit()
    return {"segments_clients": upserts, "dim_client_premiere_attribution": premieres,
            "dim_client_changements": changements}


# ---------------------------------------------------------------- script

def main(version="v1"):
    with connexion() as conn:
        df = lire_indicateurs(conn)
        print(f"{len(df)} personnes, variables : {', '.join(VARIABLES)}")

        prep = preparation()
        X = prep.fit_transform(df[VARIABLES])

        print("\n=== choix de k (silhouette sur un échantillon fixe de "
              f"{min(TAILLE_ECHANTILLON, len(df))} personnes)")
        scores = evaluer_k(X)
        print(scores.round(3).to_string(index=False))
        k = choisir_k(scores)
        sil = float(scores.loc[scores["k"] == k, "silhouette"].iloc[0])
        print(f"k retenu : {k} (silhouette {sil:.3f})")

        kmeans = entrainer(X, k)
        noms = nommer_segments(kmeans.cluster_centers_)
        segments = attribuer_segments(df, prep, kmeans, noms)

        print("\n=== profils (moyennes réelles par segment)")
        print(profils(df, segments).to_string())
        print("\n=== centres standardisés (0 = moyenne, en écarts-types)")
        print(pd.DataFrame(kmeans.cluster_centers_, columns=VARIABLES,
                           index=[noms[i] for i in range(k)]).round(2).to_string())

        date = datetime.now(timezone.utc)
        meta = {"version": version, "date_entrainement": date.isoformat(), "k": k,
                "silhouette": round(sil, 4), "variables": VARIABLES,
                "variables_log": VARIABLES_LOG, "noms": {str(i): n for i, n in noms.items()},
                "scores": scores.round(4).to_dict(orient="records")}
        enregistrer_modele(prep, kmeans, meta, version)
        print(f"\nmodèle enregistré dans {MODELES}")

        print(enregistrer_segments(conn, segments, version, date))


if __name__ == "__main__":
    main(*sys.argv[1:2])
