import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")

import clustering as cl


def indicateurs(n=200, graine=0):
    rng = np.random.default_rng(graine)
    df = pd.DataFrame({
        "customer_unique_id": [f"{i:032x}" for i in range(n)],
        "nb_commandes": rng.integers(1, 4, n).astype(float),
        "montant_total": rng.lognormal(4.5, 1, n),
        "delai_livraison_moyen_jours": rng.uniform(2, 40, n),
        "recence_jours": rng.uniform(0, 700, n),
        "note_moyenne": rng.integers(1, 6, n).astype(float),
    })
    df.loc[:4, "note_moyenne"] = np.nan                     # pas d'avis
    df.loc[5:9, "delai_livraison_moyen_jours"] = np.nan     # rien de livré
    return df


def test_preparation_bouche_les_trous_et_standardise():
    df = indicateurs()
    X = cl.preparation().fit_transform(df[cl.VARIABLES])
    assert not np.isnan(X).any()
    assert np.allclose(X.mean(axis=0), 0, atol=1e-9)
    assert np.allclose(X.std(axis=0), 1, atol=1e-9)


def test_colonnes_dans_l_ordre_de_variables():
    """Une variable non transformée (recence) doit rester à sa place dans X."""
    df = indicateurs()
    X = cl.preparation().fit_transform(df[cl.VARIABLES])
    i = cl.VARIABLES.index("recence_jours")
    assert np.corrcoef(X[:, i], df["recence_jours"])[0, 1] > 0.999


def test_choisir_k_reste_entre_4_et_8():
    scores = pd.DataFrame({"k": range(2, 11),
                           "silhouette": [0.9, 0.8, 0.3, 0.5, 0.4, 0.2, 0.1, 0.6, 0.7]})
    assert cl.choisir_k(scores) == 5


def test_noms_uniques_et_traits_lisibles():
    centres = np.zeros((3, len(cl.VARIABLES)))
    i_note = cl.VARIABLES.index("note_moyenne")
    i_rec = cl.VARIABLES.index("recence_jours")
    centres[0, i_note] = -2                      # insatisfaits
    centres[1, i_rec], centres[1, i_note] = 1.5, 0.5   # endormis
    centres[2, i_rec], centres[2, i_note] = 1.0, -0.5  # endormis aussi -> 2e trait
    noms = cl.nommer_segments(centres)
    assert noms[0] == "insatisfaits"
    assert len(set(noms.values())) == 3
    assert noms[1].startswith("endormis") and noms[2].startswith("endormis")


def test_attribution_reproductible_sans_reentrainer():
    df = indicateurs()
    prep = cl.preparation()
    km = cl.entrainer(prep.fit_transform(df[cl.VARIABLES]), 4)
    noms = cl.nommer_segments(km.cluster_centers_)
    a = cl.attribuer_segments(df, prep, km, noms)
    b = cl.attribuer_segments(df.sample(frac=1, random_state=1), prep, km, noms)
    assert len(a) == len(df)
    fusion = a.merge(b, on="customer_unique_id")
    assert (fusion["segment_x"] == fusion["segment_y"]).all()
