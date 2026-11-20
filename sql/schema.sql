-- =====================================================================
-- sql/schema.sql : architecture médaillon dans PostgreSQL (base olist)
--   bronze : copie exacte des CSV, TOUT en texte, sans clé ni contrainte
--   silver : tables nettoyées (types corrects, PK, FK) + rejets_<table>
--   gold   : tables prêtes à l'usage (créées par src/chargement.py)
-- Le script est rejouable : il supprime et recrée bronze et silver.
-- (la table public.client_geo, fournie par le TP, n'est jamais touchée)
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

DROP SCHEMA bronze CASCADE;
DROP SCHEMA silver CASCADE;
CREATE SCHEMA bronze;
CREATE SCHEMA silver;

-- =====================================================================
-- BRONZE : mêmes noms de colonnes que les CSV Olist, tout en TEXT.
-- On ne décide d'aucun type à l'entrée : c'est le rôle de silver.
-- =====================================================================
CREATE TABLE bronze.commandes (
    order_id                       TEXT,
    customer_id                    TEXT,
    order_status                   TEXT,
    order_purchase_timestamp       TEXT,
    order_approved_at              TEXT,
    order_delivered_carrier_date   TEXT,
    order_delivered_customer_date  TEXT,
    order_estimated_delivery_date  TEXT
);
CREATE TABLE bronze.clients (
    customer_id               TEXT,
    customer_unique_id        TEXT,
    customer_zip_code_prefix  TEXT,
    customer_city             TEXT,
    customer_state            TEXT
);
CREATE TABLE bronze.articles (
    order_id             TEXT,
    order_item_id        TEXT,
    product_id           TEXT,
    seller_id            TEXT,
    shipping_limit_date  TEXT,
    price                TEXT,
    freight_value        TEXT
);
CREATE TABLE bronze.paiements (
    order_id              TEXT,
    payment_sequential    TEXT,
    payment_type          TEXT,
    payment_installments  TEXT,
    payment_value         TEXT
);
CREATE TABLE bronze.avis (
    review_id                TEXT,
    order_id                 TEXT,
    review_score             TEXT,
    review_comment_title     TEXT,
    review_comment_message   TEXT,
    review_creation_date     TEXT,
    review_answer_timestamp  TEXT
);
CREATE TABLE bronze.produits (
    product_id                  TEXT,
    product_category_name       TEXT,
    product_name_lenght         TEXT,   -- faute d'origine conservée
    product_description_lenght  TEXT,   -- faute d'origine conservée
    product_photos_qty          TEXT,
    product_weight_g            TEXT,
    product_length_cm           TEXT,
    product_height_cm           TEXT,
    product_width_cm            TEXT
);
CREATE TABLE bronze.vendeurs (
    seller_id               TEXT,
    seller_zip_code_prefix  TEXT,
    seller_city             TEXT,
    seller_state            TEXT
);
CREATE TABLE bronze.geolocalisation (
    geolocation_zip_code_prefix  TEXT,
    geolocation_lat              TEXT,
    geolocation_lng              TEXT,
    geolocation_city             TEXT,
    geolocation_state            TEXT
);
CREATE TABLE bronze.traductions (
    product_category_name          TEXT,
    product_category_name_english  TEXT
);

-- =====================================================================
-- SILVER : types corrects, clés primaires et étrangères.
-- Dates en UTC (timestamptz), montants en NUMERIC(12,2),
-- codes postaux en CHAR(5) (zéro initial conservé).
-- =====================================================================
CREATE TABLE silver.traductions (
    product_category_name          TEXT PRIMARY KEY,
    product_category_name_english  TEXT NOT NULL
);

CREATE TABLE silver.clients (
    customer_id               CHAR(32) PRIMARY KEY,
    customer_unique_id        CHAR(32) NOT NULL,      -- la PERSONNE (un customer_id par commande)
    customer_zip_code_prefix  CHAR(5)  NOT NULL,
    customer_city             TEXT     NOT NULL,      -- minuscules, sans accents
    customer_state            CHAR(2)  NOT NULL
);
CREATE INDEX ix_clients_unique ON silver.clients (customer_unique_id);

CREATE TABLE silver.vendeurs (
    seller_id               CHAR(32) PRIMARY KEY,
    seller_zip_code_prefix  CHAR(5)  NOT NULL,
    seller_city             TEXT     NOT NULL,
    seller_state            CHAR(2)  NOT NULL
);

CREATE TABLE silver.produits (
    product_id                  CHAR(32) PRIMARY KEY,
    product_category_name       TEXT NOT NULL,        -- 'inconnue' si manquante
    product_name_length         INTEGER,
    product_description_length  INTEGER,
    product_photos_qty          INTEGER,
    product_weight_g            INTEGER CHECK (product_weight_g > 0),   -- 0 g -> NULL
    product_length_cm           INTEGER,
    product_height_cm           INTEGER,
    product_width_cm            INTEGER
);
CREATE INDEX ix_produits_categorie ON silver.produits (product_category_name);

CREATE TABLE silver.commandes (
    order_id                       CHAR(32) PRIMARY KEY,
    customer_id                    CHAR(32) NOT NULL REFERENCES silver.clients (customer_id),
    order_status                   TEXT NOT NULL CHECK (order_status IN
        ('created','approved','invoiced','processing','shipped','delivered','unavailable','canceled')),
    order_purchase_timestamp       TIMESTAMPTZ NOT NULL,
    order_approved_at              TIMESTAMPTZ,
    order_delivered_carrier_date   TIMESTAMPTZ,
    order_delivered_customer_date  TIMESTAMPTZ,
    order_estimated_delivery_date  TIMESTAMPTZ,
    dates_incoherentes             BOOLEAN NOT NULL DEFAULT FALSE   -- commande gardée, délais à ignorer
);
CREATE INDEX ix_commandes_client ON silver.commandes (customer_id);
CREATE INDEX ix_commandes_achat  ON silver.commandes (order_purchase_timestamp);

-- Copie des commandes annulées (statut 'canceled') avec leur raison si elle se déduit
-- des données. Remplie par sql/orders_cancelled.sql après le chargement de silver.
CREATE TABLE silver.orders_cancelled (
    LIKE silver.commandes,
    raison_annulation  TEXT NOT NULL,
    PRIMARY KEY (order_id),
    FOREIGN KEY (order_id) REFERENCES silver.commandes (order_id) ON DELETE CASCADE
);

CREATE TABLE silver.articles (
    order_id             CHAR(32) NOT NULL REFERENCES silver.commandes (order_id),
    order_item_id        INTEGER  NOT NULL,
    product_id           CHAR(32) NOT NULL REFERENCES silver.produits (product_id),
    seller_id            CHAR(32) NOT NULL REFERENCES silver.vendeurs (seller_id),
    shipping_limit_date  TIMESTAMPTZ,
    price                NUMERIC(12,2) NOT NULL CHECK (price > 0),
    freight_value        NUMERIC(12,2) NOT NULL CHECK (freight_value >= 0),   -- 0 = livraison gratuite
    PRIMARY KEY (order_id, order_item_id)
);
CREATE INDEX ix_articles_produit ON silver.articles (product_id);
CREATE INDEX ix_articles_vendeur ON silver.articles (seller_id);

CREATE TABLE silver.paiements (
    order_id              CHAR(32) NOT NULL REFERENCES silver.commandes (order_id),
    payment_sequential    INTEGER  NOT NULL,
    payment_type          TEXT     NOT NULL CHECK (payment_type IN
        ('credit_card','boleto','voucher','debit_card')),                      -- not_defined rejeté
    payment_installments  INTEGER  NOT NULL CHECK (payment_installments >= 1), -- 0 corrigé en 1
    payment_value         NUMERIC(12,2) NOT NULL CHECK (payment_value >= 0),    -- bon d'achat à 0 accepté
    PRIMARY KEY (order_id, payment_sequential)
);

-- Un même review_id peut concerner plusieurs commandes (achat groupé) : clé composée.
CREATE TABLE silver.avis (
    review_id                CHAR(32) NOT NULL,
    order_id                 CHAR(32) NOT NULL REFERENCES silver.commandes (order_id),
    review_score             SMALLINT NOT NULL CHECK (review_score BETWEEN 1 AND 5),
    review_comment_title     TEXT,        -- NULL = pas de texte
    review_comment_message   TEXT,
    review_creation_date     TIMESTAMPTZ,
    review_answer_timestamp  TIMESTAMPTZ,
    PRIMARY KEY (review_id, order_id)
);
CREATE INDEX ix_avis_commande ON silver.avis (order_id);

-- geolocalisation : plusieurs points par code postal, donc pas de clé primaire.
CREATE TABLE silver.geolocalisation (
    geolocation_zip_code_prefix  CHAR(5) NOT NULL,
    geolocation_lat              NUMERIC(9,6) NOT NULL,
    geolocation_lng              NUMERIC(9,6) NOT NULL,
    geolocation_city             TEXT,
    geolocation_state            CHAR(2)
);
CREATE INDEX ix_geoloc_cp ON silver.geolocalisation (geolocation_zip_code_prefix);

-- ---------------------------------------------------------------------
-- Lignes rejetées : colonnes de la table d'origine en TEXT (le rejet peut
-- venir d'une valeur illisible, donc on ne force aucun type) + la raison.
-- Pas de clé : une même ligne peut être rejetée plusieurs fois (rechargement).
-- ---------------------------------------------------------------------
CREATE TABLE silver.rejets_traductions     (LIKE bronze.traductions,     raison_rejet TEXT NOT NULL);
CREATE TABLE silver.rejets_clients         (LIKE bronze.clients,         raison_rejet TEXT NOT NULL);
CREATE TABLE silver.rejets_vendeurs        (LIKE bronze.vendeurs,        raison_rejet TEXT NOT NULL);
CREATE TABLE silver.rejets_produits        (LIKE bronze.produits,        raison_rejet TEXT NOT NULL);
CREATE TABLE silver.rejets_commandes       (LIKE bronze.commandes,       raison_rejet TEXT NOT NULL);
CREATE TABLE silver.rejets_articles        (LIKE bronze.articles,        raison_rejet TEXT NOT NULL);
CREATE TABLE silver.rejets_paiements       (LIKE bronze.paiements,       raison_rejet TEXT NOT NULL);
CREATE TABLE silver.rejets_avis            (LIKE bronze.avis,            raison_rejet TEXT NOT NULL);
CREATE TABLE silver.rejets_geolocalisation (LIKE bronze.geolocalisation, raison_rejet TEXT NOT NULL);

-- =====================================================================
-- GOLD : tables dérivées (commandes, vendeurs, produits, geo_par_code_postal).
-- Elles sont recréées à chaque chargement par src/chargement.py à partir
-- des Parquet de donnees/gold (types déduits des colonnes, puis clé primaire
-- ajoutée). Le modèle en étoile (dim_*, fait_ventes) est dans sql/etoile.sql.
-- =====================================================================
