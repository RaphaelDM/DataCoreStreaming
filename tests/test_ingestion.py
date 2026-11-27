import pandas as pd
import pytest

import ingestion

OID = "a" * 32


def commande(nb_articles=2, **modifs):
    """Un CSV de la boutique, tel que lu par pandas (tout en texte)."""
    lignes = []
    for i in range(1, nb_articles + 1):
        lignes.append({
            "order_id": OID, "customer_id": "b" * 32, "customer_unique_id": "c" * 32,
            "customer_zip_code_prefix": "01003", "customer_city": "sao paulo", "customer_state": "SP",
            "order_status": "processing",
            "order_purchase_timestamp": "2026-11-27T10:39:01.545618+00:00",
            "order_approved_at": "2026-11-27T10:39:01.545618+00:00",
            "order_delivered_carrier_date": None, "order_delivered_customer_date": None,
            "order_estimated_delivery_date": "2026-12-11T10:39:01.545618+00:00",
            "order_item_id": str(i), "product_id": "d" * 32, "seller_id": "e" * 32,
            "shipping_limit_date": "2026-12-04T10:39:01.545618+00:00",
            "price": "10.00", "freight_value": "2.50",
            "payment_sequential": "1", "payment_type": "credit_card",
            "payment_installments": "3", "payment_value": f"{12.5 * nb_articles:.2f}",
        })
    df = pd.DataFrame(lignes, columns=ingestion.COLONNES)
    for col, val in modifs.items():
        df[col] = val
    return df


def test_fichier_valide_decoupe_en_4_tables():
    t = ingestion.preparer(commande(3))
    assert {n: len(df) for n, df in t.items()} == {"clients": 1, "commandes": 1, "articles": 3, "paiements": 1}
    # le paiement est répété sur chaque ligne mais pas additionné
    assert float(t["paiements"].payment_value[0]) == 37.5
    assert list(t["commandes"].columns) == ingestion.TABLES["commandes"]


def test_dates_en_utc():
    t = ingestion.preparer(commande())
    achat = t["commandes"].order_purchase_timestamp[0]
    assert str(achat.tz) == "UTC" and achat.hour == 10


def test_commande_prise_en_charge_passe_en_approved():
    assert ingestion.preparer(commande())["commandes"].order_status[0] == "approved"


def test_colonne_manquante_rejetee():
    with pytest.raises(ingestion.FichierInvalide, match="colonnes manquantes"):
        ingestion.preparer(commande().drop(columns=["price"]))


def test_plusieurs_commandes_dans_un_fichier_rejete():
    df = commande()
    df.loc[1, "order_id"] = "f" * 32
    with pytest.raises(ingestion.FichierInvalide, match="2 commandes"):
        ingestion.preparer(df)


def test_regle_de_nettoyage_rejette_tout_le_fichier():
    with pytest.raises(ingestion.FichierInvalide, match="clients : État inconnu"):
        ingestion.preparer(commande(customer_state="XX"))


def test_prix_negatif_rejete():
    with pytest.raises(ingestion.FichierInvalide, match="prix absent"):
        ingestion.preparer(commande(price="-5"))


def test_paiement_incoherent_rejete():
    with pytest.raises(ingestion.FichierInvalide, match="montant payé"):
        ingestion.preparer(commande(payment_value="999.00"))


def test_idempotent_sur_donnees_deja_preparees():
    une = ingestion.preparer(commande())
    deux = ingestion.preparer(commande())
    for nom in une:
        pd.testing.assert_frame_equal(une[nom], deux[nom])


def test_fichiers_temporaires_ignores(tmp_path):
    (tmp_path / "commande_20261127T103901Z_x.csv").write_text("a")
    (tmp_path / ".commande_20261127T103901Z_y.csv.123.tmp").write_text("a")
    assert [f.name for f in ingestion.fichiers_en_attente(tmp_path)] == ["commande_20261127T103901Z_x.csv"]


def test_valeurs_numpy_converties_pour_la_base():
    import numpy as np
    assert ingestion._python(np.int64(3)) == 3 and type(ingestion._python(np.int64(3))) is int
    assert ingestion._python(pd.NaT) is None and ingestion._python(float("nan")) is None
