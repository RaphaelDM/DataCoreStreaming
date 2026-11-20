"""Tests des regroupements : on vérifie qu'ils ne perdent ni ne dupliquent rien."""

import pandas as pd

import agregations as ag

A, B = "a" * 32, "b" * 32


def test_une_colonne_par_moyen_de_paiement():
    p = pd.DataFrame({"order_id": [A, A, B], "payment_sequential": [1, 2, 1],
                      "payment_type": ["credit_card", "voucher", "boleto"],
                      "payment_installments": [4, 1, 1], "payment_value": [80.0, 20.0, 50.0]})
    r = ag.paiements_par_commande(p).set_index("order_id")
    assert r.loc[A, "nb_moyens_paiement"] == 2
    assert r.loc[A, "nb_voucher"] == 1 and r.loc[A, "montant_voucher"] == 20.0
    assert r.loc[A, "moyen_principal"] == "credit_card"
    assert r.loc[A, "paye_en_plusieurs_fois"]
    assert r.loc[B, "nb_credit_card"] == 0 and r.loc[B, "montant_boleto"] == 50.0
    # La somme des montants par moyen redonne le montant payé
    assert (r[[f"montant_{m}" for m in ag.MOYENS]].sum(axis=1) == r.montant_paye).all()


def test_avis_garde_le_plus_recent_et_son_texte():
    a = pd.DataFrame({"review_id": [A, B], "order_id": [A, A], "review_score": [1, 4],
                      "review_comment_title": [None, None], "review_comment_message": ["nul", None],
                      "review_creation_date": pd.to_datetime(["2018-01-01", "2018-01-05"], utc=True),
                      "review_answer_timestamp": pd.to_datetime(["2018-01-02", "2018-01-06"], utc=True)})
    r = ag.avis_par_commande(a).iloc[0]
    assert r.note == 4 and r.note_min == 1 and r.nb_avis == 2
    assert r.commentaire == "nul"         # le dernier texte écrit
    assert not r.avis_negatif


def test_note_vendeur_comptee_une_fois_par_commande():
    articles = pd.DataFrame({"order_id": [A] * 5 + [B], "order_item_id": [1, 2, 3, 4, 5, 1],
                             "product_id": [A] * 6, "seller_id": [A] * 6, "price": [10.0] * 6,
                             "freight_value": [1.0] * 6})
    produits = pd.DataFrame({"product_id": [A], "product_category_name_english": ["toys"]})
    commandes = pd.DataFrame({"order_id": [A, B], "customer_unique_id": [A, A], "retard_jours": [2.0, -3.0],
                              "order_purchase_timestamp": pd.to_datetime(["2018-01-01", "2018-02-01"], utc=True)})
    avis = pd.DataFrame({"order_id": [A, B], "note": [1, 5], "avis_negatif": [True, False]})
    vendeurs = pd.DataFrame({"seller_id": [A], "seller_zip_code_prefix": ["01003"]})
    geo = pd.DataFrame({"geolocation_zip_code_prefix": ["01003"], "lat": [-23.5], "lng": [-46.6]})

    v = ag.table_vendeurs(vendeurs, articles, produits, commandes, avis, geo).iloc[0]
    assert v.nb_commandes == 2 and v.nb_articles == 6
    assert v.nb_clients == 1            # la même personne a commandé deux fois
    assert v.note_moyenne == 3          # (1 + 5) / 2, et pas (1*5 + 5) / 6
    assert v.part_en_retard == 0.5
    assert v.chiffre_affaires == 60


def test_distance_sao_paulo_rio():
    d = ag.distance_km(pd.Series([-23.55]), pd.Series([-46.63]), pd.Series([-22.91]), pd.Series([-43.17]))
    assert 350 < d[0] < 370
