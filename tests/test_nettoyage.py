"""Tests des règles de nettoyage : un petit DataFrame fabriqué à la main par règle."""

import pandas as pd
import pytest

import nettoyage as n

# Des identifiants au format Olist (32 caractères hexadécimaux)
ID = ["a" * 32, "b" * 32, "c" * 32, "d" * 32]


def commandes(**colonnes):
    base = {
        "order_id": [ID[0]],
        "customer_id": [ID[1]],
        "order_status": ["delivered"],
        "order_purchase_timestamp": ["2018-01-10 10:00:00"],
        "order_approved_at": ["2018-01-10 11:00:00"],
        "order_delivered_carrier_date": ["2018-01-12 09:00:00"],
        "order_delivered_customer_date": ["2018-01-20 15:00:00"],
        "order_estimated_delivery_date": ["2018-01-30 00:00:00"],
    }
    base.update(colonnes)
    return pd.DataFrame(base)


# --------------------------------------------------------------------------- outils

def test_dates_converties_de_sao_paulo_vers_utc():
    # Janvier 2018 : São Paulo est en heure d'été, UTC-2
    propre, _ = n.nettoyer_commandes(commandes())
    assert propre.order_purchase_timestamp[0] == pd.Timestamp("2018-01-10 12:00:00", tz="UTC")


def test_code_postal_garde_le_zero_initial():
    assert n.code_postal(pd.Series([1003, "1003", "01003", None])).tolist()[:3] == ["01003"] * 3


def test_ville_simplifiee():
    assert n.simplifier_texte(pd.Series(["  São   Paulo "])).tolist() == ["sao paulo"]


# --------------------------------------------------------------------------- orders

def test_commande_en_double_rejetee():
    propre, rejets = n.nettoyer_commandes(pd.concat([commandes(), commandes()], ignore_index=True))
    assert len(propre) == 1
    assert rejets.raison_rejet.tolist() == ["order_id en double"]


def test_identifiant_mal_forme_rejete():
    _, rejets = n.nettoyer_commandes(commandes(order_id=["123"]))
    assert rejets.raison_rejet.tolist() == ["order_id absent ou mal formé"]


def test_statut_inconnu_rejete():
    _, rejets = n.nettoyer_commandes(commandes(order_status=["perdue"]))
    assert rejets.raison_rejet.tolist() == ["statut de commande inconnu"]


def test_dates_incoherentes_marquees_mais_gardees():
    # Remise au transporteur AVANT la validation du paiement
    propre, rejets = n.nettoyer_commandes(commandes(order_delivered_carrier_date=["2018-01-09 09:00:00"]))
    assert len(propre) == 1 and len(rejets) == 0
    assert propre.dates_incoherentes[0]


def test_commande_non_livree_gardee():
    propre, _ = n.nettoyer_commandes(commandes(order_status=["shipped"], order_delivered_customer_date=[None]))
    assert len(propre) == 1
    assert not propre.dates_incoherentes[0]


# --------------------------------------------------------------------------- customers / sellers

def test_client_etat_inconnu_rejete():
    df = pd.DataFrame({"customer_id": [ID[0], ID[1]], "customer_unique_id": [ID[2], ID[3]],
                       "customer_zip_code_prefix": [1003, 20000], "customer_city": ["São Paulo", "rio"],
                       "customer_state": ["sp", "XX"]})
    propre, rejets = n.nettoyer_clients(df)
    assert propre.customer_state.tolist() == ["SP"]
    assert propre.customer_zip_code_prefix.tolist() == ["01003"]
    assert rejets.raison_rejet.tolist() == ["État inconnu"]


def test_vendeur_en_double_rejete():
    df = pd.DataFrame({"seller_id": [ID[0], ID[0]], "seller_zip_code_prefix": [1003, 1003],
                       "seller_city": ["sao paulo"] * 2, "seller_state": ["SP"] * 2})
    _, rejets = n.nettoyer_vendeurs(df)
    assert rejets.raison_rejet.tolist() == ["seller_id en double"]


# --------------------------------------------------------------------------- order_items

def articles(**colonnes):
    base = {"order_id": [ID[0]], "order_item_id": [1], "product_id": [ID[1]], "seller_id": [ID[2]],
            "shipping_limit_date": ["2018-01-15 10:00:00"], "price": [10.004], "freight_value": [5.0]}
    base.update(colonnes)
    return pd.DataFrame(base)


def test_prix_arrondi_au_centime():
    propre, _ = n.nettoyer_articles(articles())
    assert propre.price[0] == 10.0


@pytest.mark.parametrize("prix", [0, -5, None])
def test_prix_nul_ou_negatif_rejete(prix):
    _, rejets = n.nettoyer_articles(articles(price=[prix]))
    assert rejets.raison_rejet.tolist() == ["prix absent ou ≤ 0"]


def test_frais_de_port_a_zero_gardes():
    propre, _ = n.nettoyer_articles(articles(freight_value=[0]))
    assert len(propre) == 1


def test_article_en_double_rejete():
    _, rejets = n.nettoyer_articles(pd.concat([articles(), articles()], ignore_index=True))
    assert rejets.raison_rejet.tolist() == ["article en double"]


# --------------------------------------------------------------------------- order_payments

def paiements(**colonnes):
    base = {"order_id": [ID[0]], "payment_sequential": [1], "payment_type": ["credit_card"],
            "payment_installments": [3], "payment_value": [100.0]}
    base.update(colonnes)
    return pd.DataFrame(base)


def test_moyen_de_paiement_not_defined_rejete():
    _, rejets = n.nettoyer_paiements(paiements(payment_type=["not_defined"]))
    assert rejets.raison_rejet.tolist() == ["moyen de paiement inconnu"]


def test_zero_echeance_corrige_en_une():
    propre, rejets = n.nettoyer_paiements(paiements(payment_installments=[0]))
    assert propre.payment_installments[0] == 1 and len(rejets) == 0


def test_montant_negatif_rejete():
    _, rejets = n.nettoyer_paiements(paiements(payment_value=[-1]))
    assert rejets.raison_rejet.tolist() == ["montant absent ou négatif"]


# --------------------------------------------------------------------------- order_reviews

def avis(**colonnes):
    base = {"review_id": [ID[0]], "order_id": [ID[1]], "review_score": [5],
            "review_comment_title": [None], "review_comment_message": ["  "],
            "review_creation_date": ["2018-01-21 00:00:00"], "review_answer_timestamp": ["2018-01-22 10:00:00"]}
    base.update(colonnes)
    return pd.DataFrame(base)


def test_commentaire_vide_devient_manquant():
    propre, _ = n.nettoyer_avis(avis())
    assert pd.isna(propre.review_comment_message[0])


def test_note_hors_limites_rejetee():
    _, rejets = n.nettoyer_avis(avis(review_score=[6]))
    assert rejets.raison_rejet.tolist() == ["note absente ou hors de 1 à 5"]


def test_meme_avis_sur_deux_commandes_garde():
    df = pd.concat([avis(), avis(order_id=[ID[2]])], ignore_index=True)
    propre, rejets = n.nettoyer_avis(df)
    assert len(propre) == 2 and len(rejets) == 0


# --------------------------------------------------------------------------- products / translation

def test_produit_categorie_vide_traduite_et_poids_zero():
    traductions, _ = n.nettoyer_traductions(pd.DataFrame({"product_category_name": ["beleza_saude"],
                                                          "product_category_name_english": ["health_beauty"]}))
    df = pd.DataFrame({"product_id": [ID[0], ID[1], ID[2]],
                       "product_category_name": ["beleza_saude", None, "pc_gamer"],
                       "product_name_lenght": [10, 10, 10], "product_description_lenght": [100, 100, 100],
                       "product_photos_qty": [1, 1, 1], "product_weight_g": [500, 0, 800],
                       "product_length_cm": [10, 10, 10], "product_height_cm": [10, 10, 10],
                       "product_width_cm": [10, 10, 10]})
    propre, _ = n.nettoyer_produits(df, traductions)
    assert "product_name_length" in propre.columns
    assert propre.product_category_name.tolist() == ["beleza_saude", "inconnue", "pc_gamer"]
    assert propre.product_category_name_english.tolist() == ["health_beauty", "unknown", "pc_gamer"]
    assert pd.isna(propre.product_weight_g[1])


# --------------------------------------------------------------------------- geolocation

def test_geolocalisation_doublons_et_hors_bresil():
    df = pd.DataFrame({"geolocation_zip_code_prefix": [1003, 1003, 1003],
                       "geolocation_lat": [-23.5, -23.5, 40.0], "geolocation_lng": [-46.6, -46.6, -3.0],
                       "geolocation_city": ["São Paulo", "sao paulo", "madrid"], "geolocation_state": ["SP"] * 3})
    propre, rejets = n.nettoyer_geolocalisation(df)
    assert len(propre) == 1
    # « São Paulo » et « sao paulo » sont la même ligne une fois la ville simplifiée
    assert sorted(rejets.raison_rejet) == ["coordonnées hors du Brésil", "ligne en double"]


# --------------------------------------------------------------------------- données bronze (tout en texte)

def test_bronze_en_texte_donne_les_bons_types():
    df = articles(order_item_id=["1"], price=["10.50"], freight_value=["0"]).astype(str)
    propre, rejets = n.nettoyer_articles(df)
    assert len(rejets) == 0
    assert propre.price[0] == 10.5 and propre.order_item_id[0] == 1
    assert str(propre.shipping_limit_date.dt.tz) == "UTC"


# --------------------------------------------------------------------------- idempotence

def test_nettoyer_deux_fois_ne_change_rien():
    df = pd.concat([commandes(), commandes(order_id=[ID[3]], order_delivered_carrier_date=["2018-01-09"])],
                   ignore_index=True)
    une_fois, _ = n.nettoyer_commandes(df)
    deux_fois, rejets = n.nettoyer_commandes(une_fois)
    pd.testing.assert_frame_equal(une_fois, deux_fois)
    assert len(rejets) == 0
