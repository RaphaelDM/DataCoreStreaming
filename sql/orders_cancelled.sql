-- =====================================================================
-- sql/orders_cancelled.sql : silver.orders_cancelled
-- Copie de TOUTES les commandes au statut 'canceled', avec leur raison
-- quand elle peut être déduite des données (Olist n'a pas de colonne
-- "motif d'annulation").
--
-- Les commandes restent aussi dans silver.commandes : c'est une COPIE.
-- Rejouable : la table est vidée puis remplie à chaque exécution.
-- Prérequis : bronze.paiements et silver.* sont chargés.
--
-- La raison est la PREMIÈRE règle vraie, dans cet ordre :
--   1. un paiement 'not_defined' existe pour la commande (lu dans bronze,
--      donc indépendant de la table des rejets)
--   2. aucun article n'est rattaché à la commande
--   3. la commande n'a jamais été validée (order_approved_at vide)
--   4. une date de livraison client est pourtant enregistrée
--   5. elle a déjà été remise au transporteur
--   6. sinon : raison non renseignée (raison_connue = FALSE)
-- =====================================================================
TRUNCATE silver.orders_cancelled;

INSERT INTO silver.orders_cancelled
SELECT o.*,
       CASE
           WHEN EXISTS (SELECT 1 FROM bronze.paiements p
                        WHERE p.order_id = o.order_id AND p.payment_type = 'not_defined')
               THEN 'moyen de paiement non défini'
           WHEN NOT EXISTS (SELECT 1 FROM silver.articles a WHERE a.order_id = o.order_id)
               THEN 'aucun article (produit non expédié)'
           WHEN o.order_approved_at IS NULL
               THEN 'annulée avant la validation du paiement'
           WHEN o.order_delivered_customer_date IS NOT NULL
               THEN 'annulée alors qu''une livraison est enregistrée'
           WHEN o.order_delivered_carrier_date IS NOT NULL
               THEN 'annulée après remise au transporteur'
           ELSE 'raison non renseignée'
       END AS raison_annulation
FROM silver.commandes o
WHERE o.order_status = 'canceled';

-- Bilan par raison (affiché par le chargement)
SELECT raison_annulation, count(*) AS commandes
FROM silver.orders_cancelled
GROUP BY raison_annulation
ORDER BY commandes DESC;
