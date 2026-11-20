"""Règles de nettoyage des données Olist.

Une fonction par table. Chaque fonction :
- prend un DataFrame brut (tel que lu dans le CSV, ou tel que produit par la boutique) ;
- renvoie (propre, rejets) : les lignes gardées, et les lignes rejetées avec une colonne `raison_rejet` ;
- ne lit ni n'écrit aucun fichier : elle transforme, c'est tout (Airflow la réutilisera telle quelle) ;
- est idempotente : la relancer sur des données déjà propres ne change rien.

Conventions :
- les dates des CSV Olist sont en heure de São Paulo : elles sont converties en UTC ;
- les montants sont arrondis au centime (ils seront stockés en NUMERIC(12,2) dans PostgreSQL) ;
- les codes postaux sont des textes de 5 chiffres (« 01003 » et pas 1003) ;
- les villes sont en minuscules, sans accents.
"""

import pandas as pd

FUSEAU_OLIST = "America/Sao_Paulo"

ETATS_BRESIL = {
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
}
STATUTS_COMMANDE = {"created", "approved", "invoiced", "processing", "shipped",
                    "delivered", "canceled", "unavailable"}
MOYENS_PAIEMENT = {"credit_card", "boleto", "voucher", "debit_card"}

# Les deux catégories absentes du fichier de traduction
TRADUCTIONS_MANQUANTES = {
    "pc_gamer": "pc_gamer",
    "portateis_cozinha_e_preparadores_de_alimentos": "kitchen_portables_and_food_preparers",
    "inconnue": "unknown",
}

# Rectangle qui contient le Brésil
LATITUDE_BRESIL = (-34, 6)
LONGITUDE_BRESIL = (-74, -34)

DATES_COMMANDE = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
                  "order_delivered_customer_date", "order_estimated_delivery_date"]
DATES_AVIS = ["review_creation_date", "review_answer_timestamp"]


# --------------------------------------------------------------------------- outils

def vers_utc(dates):
    """Texte ou date sans fuseau (heure de São Paulo) -> date en UTC. Une date déjà en UTC ne bouge pas."""
    dates = pd.to_datetime(dates, errors="coerce")
    if dates.dt.tz is None:
        # Changement d'heure : une heure qui n'existe pas est décalée, une heure ambiguë est prise en heure d'hiver
        dates = dates.dt.tz_localize(FUSEAU_OLIST, nonexistent="shift_forward",
                                     ambiguous=pd.Series(False, index=dates.index).to_numpy())
    return dates.dt.tz_convert("UTC")


def montant(valeurs):
    """Texte ou nombre -> nombre arrondi au centime."""
    return pd.to_numeric(valeurs, errors="coerce").round(2)


def simplifier_texte(textes):
    """Minuscules, sans accents, espaces en trop retirés : « São  Paulo » -> « sao paulo »."""
    return (textes.astype("string").str.lower().str.normalize("NFKD")
            .str.encode("ascii", "ignore").str.decode("ascii")
            .str.replace(r"\s+", " ", regex=True).str.strip())


def code_postal(valeurs):
    """1003, « 1003 » ou « 01003 » -> « 01003 ». Valeur illisible -> manquante."""
    nombres = pd.to_numeric(valeurs, errors="coerce")
    return nombres.map(lambda n: f"{int(n):05d}" if pd.notna(n) and 0 < n < 100000 else pd.NA).astype("string")


def etat(valeurs):
    return valeurs.astype("string").str.strip().str.upper()


def est_identifiant(valeurs):
    """Un identifiant Olist : 32 caractères hexadécimaux (comme uuid.uuid4().hex)."""
    return valeurs.astype("string").str.fullmatch(r"[0-9a-f]{32}").fillna(False).astype(bool)


def texte_libre(valeurs):
    """Commentaire : espaces retirés, texte vide -> manquant."""
    textes = valeurs.astype("string").str.strip()
    return textes.mask(textes == "")


def separer(df, regles):
    """Applique les règles dans l'ordre. Une ligne est rejetée par la première règle qu'elle ne respecte pas.

    regles : liste de (raison, masque) où masque vaut True pour les lignes à rejeter.
    """
    raison = pd.Series(None, index=df.index, dtype="object")
    for texte, masque in regles:
        masque = pd.Series(masque, index=df.index).fillna(False).astype(bool)
        raison = raison.where(~(raison.isna() & masque), texte)
    garde = raison.isna()
    propre = df[garde].reset_index(drop=True)
    rejets = df[~garde].assign(raison_rejet=raison[~garde]).reset_index(drop=True)
    return propre, rejets


# --------------------------------------------------------------------------- une fonction par table

def nettoyer_commandes(df):
    """orders : dates en UTC, statut vérifié, dates incohérentes marquées (pas supprimées)."""
    df = df.copy()
    for col in DATES_COMMANDE:
        df[col] = vers_utc(df[col])
    df["order_status"] = df["order_status"].astype("string").str.strip().str.lower()

    propre, rejets = separer(df, [
        ("order_id absent ou mal formé", ~est_identifiant(df.order_id)),
        ("order_id en double", df.order_id.duplicated()),
        ("customer_id absent ou mal formé", ~est_identifiant(df.customer_id)),
        ("date d'achat absente ou illisible", df.order_purchase_timestamp.isna()),
        ("statut de commande inconnu", ~df.order_status.isin(STATUTS_COMMANDE)),
    ])

    # Commande réelle (de l'argent a été payé) : on la garde, mais on la marque pour l'exclure des calculs de délai
    propre["dates_incoherentes"] = (
        (propre.order_delivered_carrier_date < propre.order_approved_at)
        | (propre.order_delivered_carrier_date < propre.order_purchase_timestamp)
        | (propre.order_delivered_customer_date < propre.order_delivered_carrier_date)
    )
    return propre, rejets


def nettoyer_clients(df):
    """customers : code postal sur 5 chiffres, ville simplifiée, État vérifié."""
    df = df.copy()
    df["customer_zip_code_prefix"] = code_postal(df["customer_zip_code_prefix"])
    df["customer_city"] = simplifier_texte(df["customer_city"])
    df["customer_state"] = etat(df["customer_state"])

    return separer(df, [
        ("customer_id absent ou mal formé", ~est_identifiant(df.customer_id)),
        ("customer_id en double", df.customer_id.duplicated()),
        ("customer_unique_id absent ou mal formé", ~est_identifiant(df.customer_unique_id)),
        ("code postal absent ou illisible", df.customer_zip_code_prefix.isna()),
        ("État inconnu", ~df.customer_state.isin(ETATS_BRESIL)),
    ])


def nettoyer_articles(df):
    """order_items : montants au centime, date limite d'expédition en UTC."""
    df = df.copy()
    df["order_item_id"] = pd.to_numeric(df["order_item_id"], errors="coerce").astype("Int64")
    df["shipping_limit_date"] = vers_utc(df["shipping_limit_date"])
    df["price"] = montant(df["price"])
    df["freight_value"] = montant(df["freight_value"])

    return separer(df, [
        ("order_id absent ou mal formé", ~est_identifiant(df.order_id)),
        ("product_id absent ou mal formé", ~est_identifiant(df.product_id)),
        ("seller_id absent ou mal formé", ~est_identifiant(df.seller_id)),
        ("numéro d'article absent", df.order_item_id.isna()),
        ("article en double", df.duplicated(["order_id", "order_item_id"])),
        ("prix absent ou ≤ 0", ~(df.price > 0)),
        # Frais de port à 0 gardés : c'est une livraison gratuite, pas une erreur
        ("frais de port absents ou négatifs", ~(df.freight_value >= 0)),
    ])


def nettoyer_paiements(df):
    """order_payments : montants au centime, « 0 échéance » corrigé en 1, moyen de paiement vérifié."""
    df = df.copy()
    df["payment_type"] = df["payment_type"].astype("string").str.strip().str.lower()
    df["payment_value"] = montant(df["payment_value"])
    df["payment_sequential"] = pd.to_numeric(df["payment_sequential"], errors="coerce").astype("Int64")
    echeances = pd.to_numeric(df["payment_installments"], errors="coerce").astype("Int64")
    # Un paiement se fait au moins en 1 fois : 0 est une erreur de saisie, on corrige au lieu de rejeter
    df["payment_installments"] = echeances.mask(echeances == 0, 1)

    return separer(df, [
        ("order_id absent ou mal formé", ~est_identifiant(df.order_id)),
        ("paiement en double", df.duplicated(["order_id", "payment_sequential"])),
        ("moyen de paiement inconnu", ~df.payment_type.isin(MOYENS_PAIEMENT)),
        ("montant absent ou négatif", ~(df.payment_value >= 0)),
        ("nombre d'échéances absent ou négatif", ~(df.payment_installments >= 1)),
    ])


def nettoyer_avis(df):
    """order_reviews : dates en UTC, commentaires vides -> manquants, note entre 1 et 5.

    Un même review_id peut concerner plusieurs commandes (un avis pour un achat groupé) :
    la clé est donc le couple (review_id, order_id).
    """
    df = df.copy()
    for col in DATES_AVIS:
        df[col] = vers_utc(df[col])
    df["review_score"] = pd.to_numeric(df["review_score"], errors="coerce").astype("Int64")
    df["review_comment_title"] = texte_libre(df["review_comment_title"])
    df["review_comment_message"] = texte_libre(df["review_comment_message"])

    return separer(df, [
        ("review_id absent ou mal formé", ~est_identifiant(df.review_id)),
        ("order_id absent ou mal formé", ~est_identifiant(df.order_id)),
        ("avis en double", df.duplicated(["review_id", "order_id"])),
        ("note absente ou hors de 1 à 5", ~df.review_score.between(1, 5)),
        ("date de l'avis absente", df.review_creation_date.isna()),
    ])


def nettoyer_traductions(df):
    """product_category_name_translation : textes simplifiés + les 2 catégories qui manquaient."""
    df = df.copy()
    df["product_category_name"] = df["product_category_name"].astype("string").str.strip().str.lower()
    df["product_category_name_english"] = df["product_category_name_english"].astype("string").str.strip().str.lower()
    manquantes = pd.DataFrame({"product_category_name": list(TRADUCTIONS_MANQUANTES),
                               "product_category_name_english": list(TRADUCTIONS_MANQUANTES.values())}
                              ).astype("string")
    manquantes = manquantes[~manquantes.product_category_name.isin(df.product_category_name)]
    df = pd.concat([df, manquantes], ignore_index=True)

    return separer(df, [
        ("catégorie absente", df.product_category_name.isna() | (df.product_category_name == "")),
        ("catégorie en double", df.product_category_name.duplicated()),
    ])


def nettoyer_produits(df, traductions):
    """products : faute « lenght » corrigée, catégorie vide -> « inconnue », traduction anglaise ajoutée,
    poids ou dimension ≤ 0 -> manquant (impossible, mais le produit a été vendu : on le garde).

    traductions : la table de traduction déjà nettoyée (sortie de nettoyer_traductions).
    """
    df = df.copy().rename(columns={"product_name_lenght": "product_name_length",
                                   "product_description_lenght": "product_description_length"})
    df = df.drop(columns="product_category_name_english", errors="ignore")   # pour rester idempotent

    categorie = df["product_category_name"].astype("string").str.strip().str.lower()
    df["product_category_name"] = categorie.fillna("inconnue").mask(categorie == "", "inconnue")
    traduction = traductions.set_index("product_category_name")["product_category_name_english"]
    df["product_category_name_english"] = (df["product_category_name"].map(traduction)
                                           .fillna(df["product_category_name"]).astype("string"))

    for col in ["product_name_length", "product_description_length", "product_photos_qty"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]:
        valeurs = pd.to_numeric(df[col], errors="coerce")
        df[col] = valeurs.where(valeurs > 0)

    return separer(df, [
        ("product_id absent ou mal formé", ~est_identifiant(df.product_id)),
        ("product_id en double", df.product_id.duplicated()),
    ])


def nettoyer_vendeurs(df):
    """sellers : code postal sur 5 chiffres, ville simplifiée, État vérifié."""
    df = df.copy()
    df["seller_zip_code_prefix"] = code_postal(df["seller_zip_code_prefix"])
    df["seller_city"] = simplifier_texte(df["seller_city"])
    df["seller_state"] = etat(df["seller_state"])

    return separer(df, [
        ("seller_id absent ou mal formé", ~est_identifiant(df.seller_id)),
        ("seller_id en double", df.seller_id.duplicated()),
        ("code postal absent ou illisible", df.seller_zip_code_prefix.isna()),
        ("État inconnu", ~df.seller_state.isin(ETATS_BRESIL)),
    ])


def nettoyer_geolocalisation(df):
    """geolocation : code postal sur 5 chiffres, ville simplifiée, points hors du Brésil et doublons retirés."""
    df = df.copy()
    df["geolocation_zip_code_prefix"] = code_postal(df["geolocation_zip_code_prefix"])
    df["geolocation_city"] = simplifier_texte(df["geolocation_city"])
    df["geolocation_state"] = etat(df["geolocation_state"])
    df["geolocation_lat"] = pd.to_numeric(df["geolocation_lat"], errors="coerce")
    df["geolocation_lng"] = pd.to_numeric(df["geolocation_lng"], errors="coerce")

    hors_bresil = ~(df.geolocation_lat.between(*LATITUDE_BRESIL) & df.geolocation_lng.between(*LONGITUDE_BRESIL))
    return separer(df, [
        ("code postal absent ou illisible", df.geolocation_zip_code_prefix.isna()),
        ("coordonnées hors du Brésil", hors_bresil),
        ("ligne en double", df.duplicated()),
    ])
