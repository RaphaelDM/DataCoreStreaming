-- =====================================================================
-- sql/etoile.sql : modèle en étoile du dashboard (schéma gold)
--
--                       dim_date
--                          |
--   dim_client ----- fait_ventes ----- dim_produit
--                          |
--                      dim_vendeur
--
-- GRAIN de fait_ventes : UNE LIGNE PAR ARTICLE d'une commande
--                        (order_id, order_item_id).
-- Donc SUM(fait_ventes.prix) = SUM(silver.articles.price).
--
-- Les paiements restent HORS de l'étoile : un paiement est au niveau de la
-- commande ; le répéter sur chaque article compterait plusieurs fois le CA.
--
-- Le script est REJOUABLE : les dimensions et les faits sont chargés
-- uniquement pour les lignes absentes (Airflow réutilisera les mêmes
-- INSERT pour les nouvelles commandes). Ordre obligatoire : dimensions
-- d'abord, faits ensuite (un fait a besoin des clés des dimensions).
-- Prérequis : src/chargement.py silver a rempli silver.* ; public.client_geo existe.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS gold;

-- ---------------------------------------------------------------------
-- 1. DDL
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_key      INTEGER PRIMARY KEY,           -- AAAAMMJJ, en heure LOCALE de São Paulo
    date_jour     DATE NOT NULL UNIQUE,
    annee         SMALLINT NOT NULL,
    trimestre     SMALLINT NOT NULL,
    mois          SMALLINT NOT NULL,
    annee_mois    CHAR(7)  NOT NULL,             -- '2017-11'
    jour_mois     SMALLINT NOT NULL,
    jour_semaine  SMALLINT NOT NULL,             -- 1 = lundi ... 7 = dimanche (ISO)
    mois_complet  BOOLEAN  NOT NULL              -- FALSE pour les mois incomplets du jeu Olist
);

-- Une ligne par PERSONNE (customer_unique_id) et par période de validité (SCD type 2).
CREATE TABLE IF NOT EXISTS gold.dim_client (
    client_key          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- clé de substitution
    customer_unique_id  CHAR(32) NOT NULL,
    code_postal         CHAR(5)  NOT NULL,
    ville               TEXT     NOT NULL,
    etat                CHAR(2)  NOT NULL,
    latitude            NUMERIC(9,6),
    longitude           NUMERIC(9,6),
    segment             TEXT,                  -- NULL tant que le clustering (étape 7) n'a pas tourné
    version_modele      TEXT,
    date_debut          TIMESTAMPTZ NOT NULL,
    date_fin            TIMESTAMPTZ,           -- NULL = ligne en cours
    est_courant         BOOLEAN NOT NULL DEFAULT TRUE
);
-- une seule ligne courante par personne
CREATE UNIQUE INDEX IF NOT EXISTS ux_dim_client_courant
    ON gold.dim_client (customer_unique_id) WHERE est_courant;
CREATE INDEX IF NOT EXISTS ix_dim_client_uid ON gold.dim_client (customer_unique_id);

CREATE TABLE IF NOT EXISTS gold.dim_produit (
    produit_key    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id     CHAR(32) NOT NULL UNIQUE,
    categorie      TEXT NOT NULL,              -- portugais, 'inconnue' si manquante
    categorie_en   TEXT NOT NULL,              -- traduction anglaise
    poids_g        INTEGER,
    longueur_cm    INTEGER,
    hauteur_cm     INTEGER,
    largeur_cm     INTEGER,
    volume_cm3     INTEGER,
    nb_photos      INTEGER
);

CREATE TABLE IF NOT EXISTS gold.dim_vendeur (
    vendeur_key  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id    CHAR(32) NOT NULL UNIQUE,
    code_postal  CHAR(5)  NOT NULL,
    ville        TEXT     NOT NULL,
    etat         CHAR(2)  NOT NULL,
    latitude     NUMERIC(9,6),                 -- médiane du code postal (NULL pour 7 vendeurs)
    longitude    NUMERIC(9,6)
);

CREATE TABLE IF NOT EXISTS gold.fait_ventes (
    order_id               CHAR(32) NOT NULL,
    order_item_id          INTEGER  NOT NULL,
    client_key             BIGINT   NOT NULL REFERENCES gold.dim_client  (client_key),
    produit_key            BIGINT   NOT NULL REFERENCES gold.dim_produit (produit_key),
    vendeur_key            BIGINT   NOT NULL REFERENCES gold.dim_vendeur (vendeur_key),
    date_key               INTEGER  NOT NULL REFERENCES gold.dim_date    (date_key),   -- date d'achat locale
    prix                   NUMERIC(12,2) NOT NULL,
    frais_port             NUMERIC(12,2) NOT NULL,
    statut                 TEXT     NOT NULL,            -- order_status
    delai_livraison_jours  NUMERIC(7,2),                 -- NULL si non livrée ou dates incohérentes
    en_retard              BOOLEAN,                      -- idem ; NULL = non évaluable
    note                   SMALLINT,                     -- avis le plus récent de la commande
    PRIMARY KEY (order_id, order_item_id)
);
CREATE INDEX IF NOT EXISTS ix_fait_date    ON gold.fait_ventes (date_key);
CREATE INDEX IF NOT EXISTS ix_fait_client  ON gold.fait_ventes (client_key);
CREATE INDEX IF NOT EXISTS ix_fait_produit ON gold.fait_ventes (produit_key);
CREATE INDEX IF NOT EXISTS ix_fait_vendeur ON gold.fait_ventes (vendeur_key);

-- ---------------------------------------------------------------------
-- 2. Chargement des DIMENSIONS (avant les faits : un fait a besoin des clés)
-- ---------------------------------------------------------------------

-- dim_date : de septembre 2016 à décembre 2018, jour par jour.
INSERT INTO gold.dim_date
SELECT to_char(d, 'YYYYMMDD')::int,
       d::date,
       extract(year    FROM d)::smallint,
       extract(quarter FROM d)::smallint,
       extract(month   FROM d)::smallint,
       to_char(d, 'YYYY-MM'),
       extract(day     FROM d)::smallint,
       extract(isodow  FROM d)::smallint,
       d::date BETWEEN DATE '2017-01-01' AND DATE '2018-08-31'
FROM generate_series(DATE '2016-09-01', DATE '2018-12-31', INTERVAL '1 day') AS d
ON CONFLICT (date_key) DO NOTHING;

-- dim_client : nouvelles personnes seulement. Les attributs (adresse) viennent de
-- leur commande la plus récente (une personne a un customer_id par commande).
INSERT INTO gold.dim_client
    (customer_unique_id, code_postal, ville, etat, latitude, longitude, date_debut, est_courant)
SELECT DISTINCT ON (c.customer_unique_id)
       c.customer_unique_id, c.customer_zip_code_prefix, c.customer_city, c.customer_state,
       g.latitude, g.longitude,
       TIMESTAMPTZ '2000-01-01 00:00:00+00',        -- "depuis toujours" pour la première ligne
       TRUE
FROM silver.clients c
JOIN silver.commandes o      ON o.customer_id = c.customer_id
LEFT JOIN public.client_geo g ON g.customer_id = c.customer_id
WHERE NOT EXISTS (SELECT 1 FROM gold.dim_client d WHERE d.customer_unique_id = c.customer_unique_id)
ORDER BY c.customer_unique_id, o.order_purchase_timestamp DESC;

-- dim_produit
INSERT INTO gold.dim_produit
    (product_id, categorie, categorie_en, poids_g, longueur_cm, hauteur_cm, largeur_cm, volume_cm3, nb_photos)
SELECT p.product_id,
       p.product_category_name,
       COALESCE(t.product_category_name_english, p.product_category_name),
       p.product_weight_g, p.product_length_cm, p.product_height_cm, p.product_width_cm,
       p.product_length_cm * p.product_height_cm * p.product_width_cm,
       p.product_photos_qty
FROM silver.produits p
LEFT JOIN silver.traductions t ON t.product_category_name = p.product_category_name
ON CONFLICT (product_id) DO NOTHING;

-- dim_vendeur : un point par code postal = médiane des coordonnées silver.geolocalisation
INSERT INTO gold.dim_vendeur (seller_id, code_postal, ville, etat, latitude, longitude)
SELECT s.seller_id, s.seller_zip_code_prefix, s.seller_city, s.seller_state, g.lat, g.lng
FROM silver.vendeurs s
LEFT JOIN (
    SELECT geolocation_zip_code_prefix AS cp,
           (percentile_cont(0.5) WITHIN GROUP (ORDER BY geolocation_lat))::numeric(9,6) AS lat,
           (percentile_cont(0.5) WITHIN GROUP (ORDER BY geolocation_lng))::numeric(9,6) AS lng
    FROM silver.geolocalisation
    GROUP BY geolocation_zip_code_prefix
) g ON g.cp = s.seller_zip_code_prefix
ON CONFLICT (seller_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- 3. Chargement des FAITS : seulement les articles pas encore présents
-- ---------------------------------------------------------------------
INSERT INTO gold.fait_ventes
    (order_id, order_item_id, client_key, produit_key, vendeur_key, date_key,
     prix, frais_port, statut, delai_livraison_jours, en_retard, note)
SELECT a.order_id, a.order_item_id,
       dc.client_key, dp.produit_key, dv.vendeur_key, dd.date_key,
       a.price, a.freight_value, o.order_status,
       CASE WHEN NOT o.dates_incoherentes AND o.order_delivered_customer_date IS NOT NULL
            THEN round((extract(epoch FROM o.order_delivered_customer_date - o.order_purchase_timestamp) / 86400.0)::numeric, 2)
       END,
       CASE WHEN NOT o.dates_incoherentes AND o.order_delivered_customer_date IS NOT NULL
            THEN o.order_delivered_customer_date > o.order_estimated_delivery_date
       END,
       av.review_score
FROM silver.articles a
JOIN silver.commandes o ON o.order_id    = a.order_id
JOIN silver.clients   c ON c.customer_id = o.customer_id
JOIN gold.dim_client  dc ON dc.customer_unique_id = c.customer_unique_id AND dc.est_courant
JOIN gold.dim_produit dp ON dp.product_id = a.product_id
JOIN gold.dim_vendeur dv ON dv.seller_id  = a.seller_id
JOIN gold.dim_date    dd ON dd.date_key =
     to_char(o.order_purchase_timestamp AT TIME ZONE 'America/Sao_Paulo', 'YYYYMMDD')::int
LEFT JOIN LATERAL (                       -- avis le plus récent de la commande
    SELECT r.review_score
    FROM silver.avis r
    WHERE r.order_id = a.order_id
    ORDER BY r.review_creation_date DESC NULLS LAST, r.review_id
    LIMIT 1
) av ON TRUE
ON CONFLICT (order_id, order_item_id) DO NOTHING;

ANALYZE gold.fait_ventes;

-- ---------------------------------------------------------------------
-- 4. CONTRÔLES : le script s'arrête en erreur si l'étoile perd ou duplique des lignes
-- ---------------------------------------------------------------------
DO $$
DECLARE
    ca_etoile  NUMERIC;
    ca_silver  NUMERIC;
    nb_etoile  BIGINT;
    nb_silver  BIGINT;
BEGIN
    SELECT sum(prix), count(*) INTO ca_etoile, nb_etoile FROM gold.fait_ventes;
    SELECT sum(price), count(*) INTO ca_silver, nb_silver FROM silver.articles;
    RAISE NOTICE 'CA étoile = %, CA silver = %, lignes étoile = %, lignes silver = %',
                 ca_etoile, ca_silver, nb_etoile, nb_silver;
    IF ca_etoile IS DISTINCT FROM ca_silver OR nb_etoile <> nb_silver THEN
        RAISE EXCEPTION 'Étoile incohérente : une jointure perd ou duplique des lignes';
    END IF;
END $$;

-- Aucun fait ne pointe vers une dimension absente (garanti par les FK, vérifié ici pour le DAG de qualité)
SELECT count(*) AS faits_sans_dimension
FROM gold.fait_ventes f
WHERE NOT EXISTS (SELECT 1 FROM gold.dim_client  d WHERE d.client_key  = f.client_key)
   OR NOT EXISTS (SELECT 1 FROM gold.dim_produit d WHERE d.produit_key = f.produit_key)
   OR NOT EXISTS (SELECT 1 FROM gold.dim_vendeur d WHERE d.vendeur_key = f.vendeur_key)
   OR NOT EXISTS (SELECT 1 FROM gold.dim_date    d WHERE d.date_key    = f.date_key);

-- ---------------------------------------------------------------------
-- 5. Requête de comparaison (demi-journée 2) : CA par mois et par État.
--    À lancer avec EXPLAIN ANALYSE sur l'étoile, puis sur silver, et noter les 2 temps.
-- ---------------------------------------------------------------------
-- EXPLAIN ANALYZE
-- SELECT d.annee_mois, c.etat, sum(f.prix) AS ca
-- FROM gold.fait_ventes f
-- JOIN gold.dim_date   d ON d.date_key   = f.date_key
-- JOIN gold.dim_client c ON c.client_key = f.client_key
-- GROUP BY d.annee_mois, c.etat;
--
-- EXPLAIN ANALYZE
-- SELECT to_char(o.order_purchase_timestamp AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM') AS annee_mois,
--        cl.customer_state, sum(a.price) AS ca
-- FROM silver.articles a
-- JOIN silver.commandes o ON o.order_id = a.order_id
-- JOIN silver.clients  cl ON cl.customer_id = o.customer_id
-- GROUP BY 1, 2;
