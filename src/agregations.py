"""Colonnes calculées et regroupements pour le dashboard, la boutique et le clustering.

Ces fonctions prennent les tables déjà nettoyées (sorties de nettoyage.py) et ne lisent ni n'écrivent
aucun fichier. Deux niveaux de résumé :
- par commande (order_id) -> table `commandes`, une ligne par commande ;
- par vendeur et par produit -> tables `vendeurs` et `produits`.
"""

import numpy as np
import pandas as pd

from nettoyage import FUSEAU_OLIST

MOYENS = ["credit_card", "boleto", "voucher", "debit_card"]

STATUTS_FR = {
    "created": "créée", "approved": "validée", "invoiced": "facturée", "processing": "en préparation",
    "shipped": "expédiée", "delivered": "livrée", "canceled": "annulée", "unavailable": "indisponible",
}


def jours(fin, debut):
    """Durée en jours (avec décimales) entre deux colonnes de dates."""
    return ((fin - debut).dt.total_seconds() / 86400).round(1)


def distance_km(lat1, lng1, lat2, lng2):
    """Distance à vol d'oiseau entre deux points (formule de haversine)."""
    lat1, lng1, lat2, lng2 = (np.radians(pd.to_numeric(x, errors="coerce").astype(float)) for x in (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return (2 * 6371 * np.arcsin(np.sqrt(a))).round(1)


# --------------------------------------------------------------------------- orders

def enrichir_commandes(commandes):
    """Ajoute les délais, la période d'achat (heure de São Paulo) et des marqueurs. Une ligne par commande."""
    c = commandes.copy()
    achat, validation = c.order_purchase_timestamp, c.order_approved_at
    transporteur, livraison, prevue = (c.order_delivered_carrier_date, c.order_delivered_customer_date,
                                       c.order_estimated_delivery_date)

    c["delai_livraison_jours"] = jours(livraison, achat)          # ce que le client a attendu
    c["delai_promis_jours"] = jours(prevue, achat)                # ce qu'on lui avait promis
    c["retard_jours"] = jours(livraison, prevue)                  # > 0 : en retard
    c["en_retard"] = c.retard_jours > 0
    # Les étapes du délai : où la commande perd-elle du temps ?
    c["delai_validation_heures"] = (jours(validation, achat) * 24).round(1)
    c["delai_preparation_jours"] = jours(transporteur, validation)
    c["delai_transport_jours"] = jours(livraison, transporteur)
    # Délais faux si les dates sont incohérentes : on les vide plutôt que de fausser les moyennes
    cols_delais = ["delai_livraison_jours", "retard_jours", "delai_validation_heures",
                   "delai_preparation_jours", "delai_transport_jours"]
    c.loc[c.dates_incoherentes, cols_delais] = np.nan
    c.loc[c.dates_incoherentes, "en_retard"] = False

    # Période d'achat : à l'heure locale du client, pas en UTC (une commande à 22 h n'est pas « le lendemain »)
    locale = achat.dt.tz_convert(FUSEAU_OLIST)
    c["mois_achat"] = locale.dt.strftime("%Y-%m")
    c["jour_semaine"] = locale.dt.dayofweek          # 0 = lundi
    c["heure_achat"] = locale.dt.hour

    c["statut"] = c.order_status.map(STATUTS_FR)     # pour l'affichage (historique client, dashboard)
    c["est_livree"] = c.order_status == "delivered"
    c["est_annulee"] = c.order_status.isin(["canceled", "unavailable"])
    return c


# --------------------------------------------------------------------------- order_items

def articles_par_commande(articles, produits):
    """Le panier de chaque commande : quantités, montants, poids, catégorie principale."""
    a = articles.merge(produits[["product_id", "product_category_name_english", "product_weight_g"]],
                       on="product_id", how="left")
    resume = a.groupby("order_id").agg(
        nb_articles=("order_item_id", "count"),
        nb_produits=("product_id", "nunique"),
        nb_vendeurs=("seller_id", "nunique"),
        nb_categories=("product_category_name_english", "nunique"),
        montant_produits=("price", "sum"),
        frais_port=("freight_value", "sum"),
        prix_article_max=("price", "max"),
        poids_total_g=("product_weight_g", "sum"),
        date_limite_expedition=("shipping_limit_date", "max"),
    ).reset_index()
    resume["montant_produits"] = resume.montant_produits.round(2)
    resume["frais_port"] = resume.frais_port.round(2)
    resume["montant_total"] = (resume.montant_produits + resume.frais_port).round(2)
    resume["part_frais_port"] = (resume.frais_port / resume.montant_total).round(3)

    # Catégorie principale : celle qui pèse le plus en R$ dans la commande
    par_categorie = a.groupby(["order_id", "product_category_name_english"]).price.sum().reset_index()
    principale = (par_categorie.sort_values(["price", "product_category_name_english"])
                  .drop_duplicates("order_id", keep="last")
                  [["order_id", "product_category_name_english"]]
                  .rename(columns={"product_category_name_english": "categorie_principale"}))
    return resume.merge(principale, on="order_id", how="left")


# --------------------------------------------------------------------------- order_payments

def paiements_par_commande(paiements):
    """Comment la commande a été payée : total, moyens utilisés, une colonne par moyen (nombre et montant)."""
    p = paiements
    resume = p.groupby("order_id").agg(
        montant_paye=("payment_value", "sum"),
        nb_paiements=("payment_sequential", "count"),
        nb_moyens_paiement=("payment_type", "nunique"),
        nb_echeances_max=("payment_installments", "max"),
    ).reset_index()
    resume["montant_paye"] = resume.montant_paye.round(2)
    resume["paye_en_plusieurs_fois"] = resume.nb_echeances_max > 1

    nb = (pd.crosstab(p.order_id, p.payment_type).reindex(columns=MOYENS, fill_value=0)
          .add_prefix("nb_").reset_index())
    montants = (p.pivot_table(index="order_id", columns="payment_type", values="payment_value",
                              aggfunc="sum", fill_value=0)
                .reindex(columns=MOYENS, fill_value=0).round(2).add_prefix("montant_").reset_index())
    principal = (p.sort_values(["payment_value", "payment_sequential"])
                 .drop_duplicates("order_id", keep="last")[["order_id", "payment_type"]]
                 .rename(columns={"payment_type": "moyen_principal"}))
    return (resume.merge(nb, on="order_id").merge(montants, on="order_id")
            .merge(principal, on="order_id"))


# --------------------------------------------------------------------------- order_reviews

def avis_par_commande(avis):
    """La satisfaction : note du dernier avis, pire note, texte du dernier commentaire, délai de réponse d'Olist."""
    a = avis.sort_values(["review_creation_date", "review_answer_timestamp", "review_id"]).copy()
    a["a_commentaire"] = a.review_comment_message.notna()
    a["delai_reponse_jours"] = jours(a.review_answer_timestamp, a.review_creation_date)
    resume = a.groupby("order_id").agg(
        note=("review_score", "last"),
        note_min=("review_score", "min"),
        nb_avis=("review_id", "count"),
        a_laisse_un_commentaire=("a_commentaire", "max"),
        titre_avis=("review_comment_title", "last"),             # last ignore les vides : dernier texte écrit
        commentaire=("review_comment_message", "last"),
        date_avis=("review_creation_date", "last"),
        delai_reponse_jours=("delai_reponse_jours", "last"),
    ).reset_index()
    resume["avis_negatif"] = resume.note <= 2
    return resume


# --------------------------------------------------------------------------- geolocation

def geo_par_code_postal(geo):
    """Un point par code postal : médiane des coordonnées, ville et État les plus fréquents."""
    def plus_frequent(s):
        return s.value_counts().index[0]
    return geo.groupby("geolocation_zip_code_prefix").agg(
        lat=("geolocation_lat", "median"),
        lng=("geolocation_lng", "median"),
        ville=("geolocation_city", plus_frequent),
        etat=("geolocation_state", plus_frequent),
    ).reset_index()


# --------------------------------------------------------------------------- distance client - vendeur

def distance_par_commande(articles, commandes_clients, vendeurs_geo):
    """Distance entre le client et ses vendeurs, et si tout est dans le même État.

    commandes_clients : order_id, latitude, longitude, customer_state (position du client de la commande)
    vendeurs_geo      : seller_id, lat, lng, seller_state
    """
    a = (articles[["order_id", "seller_id"]].drop_duplicates()
         .merge(commandes_clients, on="order_id", how="left")
         .merge(vendeurs_geo, on="seller_id", how="left"))
    a["distance_km"] = distance_km(a.latitude, a.longitude, a.lat, a.lng)
    a["meme_etat"] = a.customer_state == a.seller_state
    return a.groupby("order_id").agg(
        distance_vendeur_km=("distance_km", "max"),       # le vendeur le plus loin fixe le délai
        vendeur_meme_etat=("meme_etat", "all"),
    ).reset_index()


# --------------------------------------------------------------------------- table finale

def table_commandes(commandes, clients, articles_resume, paiements_resume, avis_resume, distances):
    """Une ligne par commande, avec tout ce qu'il faut pour le dashboard."""
    t = (commandes
         .merge(clients, on="customer_id", how="left")
         .merge(articles_resume, on="order_id", how="left")
         .merge(paiements_resume, on="order_id", how="left")
         .merge(avis_resume, on="order_id", how="left")
         .merge(distances, on="order_id", how="left"))

    # Différence entre ce qui a été payé et le prix + port (intérêts du paiement en plusieurs fois ?)
    t["ecart_paiement"] = (t.montant_paye - t.montant_total).round(2)

    # Nouveau client ou client qui revient ?
    t = t.sort_values(["order_purchase_timestamp", "order_id"]).reset_index(drop=True)
    t["numero_commande_client"] = t.groupby("customer_unique_id").cumcount() + 1
    t["client_revenu"] = t.numero_commande_client > 1

    assert t.order_id.is_unique, "des commandes sont en double après les fusions"
    return t


# --------------------------------------------------------------------------- vendeurs

def table_vendeurs(vendeurs, articles, produits, commandes, avis_resume, geo_cp):
    """Une ligne par vendeur (commandes : la table gold `commandes`, qui contient customer_unique_id). On regroupe d'abord par (vendeur, commande), sinon une commande
    de 5 articles compterait 5 fois dans la note moyenne et le taux de retard."""
    a = articles.merge(produits[["product_id", "product_category_name_english"]], on="product_id", how="left")
    vc = (a.groupby(["seller_id", "order_id"]).agg(ca=("price", "sum"), nb_articles=("order_item_id", "count"))
          .reset_index()
          .merge(commandes[["order_id", "customer_unique_id", "retard_jours", "order_purchase_timestamp"]],
                 on="order_id", how="left")
          .merge(avis_resume[["order_id", "note", "avis_negatif"]], on="order_id", how="left"))
    vc["en_retard"] = (vc.retard_jours > 0).where(vc.retard_jours.notna())   # vide si pas livrée

    resume = vc.groupby("seller_id").agg(
        nb_commandes=("order_id", "count"),
        nb_clients=("customer_unique_id", "nunique"),     # la vraie personne, pas customer_id
        nb_articles=("nb_articles", "sum"),
        chiffre_affaires=("ca", "sum"),
        panier_moyen=("ca", "mean"),
        note_moyenne=("note", "mean"),
        part_avis_negatifs=("avis_negatif", "mean"),
        part_en_retard=("en_retard", "mean"),
        premiere_vente=("order_purchase_timestamp", "min"),
        derniere_vente=("order_purchase_timestamp", "max"),
    ).reset_index()
    categories = a.groupby("seller_id").product_category_name_english.nunique().rename("nb_categories").reset_index()
    resume = resume.merge(categories, on="seller_id", how="left")
    for col in ["chiffre_affaires", "panier_moyen"]:
        resume[col] = resume[col].round(2)
    for col in ["note_moyenne", "part_avis_negatifs", "part_en_retard"]:
        resume[col] = pd.to_numeric(resume[col]).round(3)

    t = (vendeurs.merge(resume, on="seller_id", how="left")
         .merge(geo_cp[["geolocation_zip_code_prefix", "lat", "lng"]],
                left_on="seller_zip_code_prefix", right_on="geolocation_zip_code_prefix", how="left")
         .drop(columns="geolocation_zip_code_prefix"))
    assert t.seller_id.is_unique
    return t


# --------------------------------------------------------------------------- produits

def table_produits(produits, articles, avis_resume):
    """Une ligne par produit : ventes, prix (le prix médian est celui du catalogue), note moyenne, volume."""
    ventes = articles.groupby("product_id").agg(
        nb_ventes=("order_item_id", "count"),
        nb_commandes=("order_id", "nunique"),
        prix_median=("price", "median"),
        prix_min=("price", "min"),
        prix_max=("price", "max"),
        chiffre_affaires=("price", "sum"),
        frais_port_moyen=("freight_value", "mean"),
    ).reset_index()
    # Note du produit : une note par commande qui contient le produit
    notes = (articles[["product_id", "order_id"]].drop_duplicates()
             .merge(avis_resume[["order_id", "note"]], on="order_id")
             .groupby("product_id").agg(note_moyenne=("note", "mean"), nb_avis=("note", "count"))
             .reset_index())

    t = produits.merge(ventes, on="product_id", how="left").merge(notes, on="product_id", how="left")
    t["volume_cm3"] = t.product_length_cm * t.product_height_cm * t.product_width_cm
    for col in ["prix_median", "chiffre_affaires", "frais_port_moyen"]:
        t[col] = t[col].round(2)
    t["note_moyenne"] = pd.to_numeric(t.note_moyenne).round(2)
    assert t.product_id.is_unique
    return t


# --------------------------------------------------------------------------- indicateurs clients

def indicateurs_clients(commandes):
    """Calcule les indicateurs RFM, une ligne par personne.

    La récence est calculée par rapport à la date d'achat la plus récente du
    jeu de données. Le montant correspond à la valeur totale des commandes
    (`montant_total`), frais de port compris.
    """
    colonnes_requises = {"customer_unique_id", "order_id", "order_purchase_timestamp", "montant_total"}
    manquantes = colonnes_requises.difference(commandes.columns)
    if manquantes:
        raise KeyError(f"colonnes manquantes pour les indicateurs clients : {sorted(manquantes)}")

    resume = (commandes.groupby("customer_unique_id").agg(
        date_derniere_commande=("order_purchase_timestamp", "max"),
        nb_commandes=("order_id", "nunique"),
        montant_total=("montant_total", "sum"),
    ).reset_index())

    date_reference = commandes.order_purchase_timestamp.max()
    resume["recence_jours"] = (date_reference - resume.date_derniere_commande).dt.days
    resume["panier_moyen"] = (resume.montant_total / resume.nb_commandes).round(2)
    resume["montant_total"] = resume.montant_total.round(2)

    return resume[[
        "customer_unique_id", "recence_jours", "nb_commandes",
        "montant_total", "panier_moyen", "date_derniere_commande",
    ]]
