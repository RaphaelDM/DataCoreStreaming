# Format du fichier CSV de commande

## Fichier et grain

La boutique écrit un fichier par commande sous `donnees/entrantes/`, nommé `commande_<UTC>_<order_id>.csv`, par exemple `commande_20261106T101530Z_0123456789abcdef0123456789abcdef.csv`. Le fichier est produit en UTF-8 avec en-tête, séparateur virgule et décimales avec un point. Chaque ligne représente un article ; les données de commande, client et paiement sont répétées pour faciliter la reconstruction. L'ingestion déduplique commande et paiement par leurs clés métier.

La boutique écrit d'abord un fichier temporaire dans le même dossier, le synchronise, puis le renomme avec `os.replace`. Seuls les fichiers `.csv` finaux sont à consommer.

## Colonnes

| Colonne | Type | Sens |
|---|---|---|
| `order_id` | texte hexadécimal (32) | Identifiant Olist de commande, UUID sans tirets |
| `customer_id` | texte hexadécimal (32) | Identifiant client spécifique à cette commande |
| `customer_unique_id` | texte hexadécimal (32) | Identifiant stable de la personne inscrite |
| `customer_zip_code_prefix` | texte (5 chiffres) | Code postal fourni à l'inscription |
| `customer_city` | texte | Ville normalisée en minuscules sans accents |
| `customer_state` | texte (2) | État brésilien |
| `order_status` | texte | `processing` tant que la chaîne de données n'a pas pris en charge la commande |
| `order_purchase_timestamp` | date ISO 8601 UTC | Heure de validation de la commande |
| `order_approved_at` | date ISO 8601 UTC | Heure de validation du paiement simulé |
| `order_delivered_carrier_date` | vide | Livraison non encore prise en charge |
| `order_delivered_customer_date` | vide | Pas encore livrée |
| `order_estimated_delivery_date` | date ISO 8601 UTC | Date estimée, 14 jours après l'achat |
| `order_item_id` | entier | Numéro d'article dans la commande, à partir de 1 |
| `product_id` | texte hexadécimal (32) | Produit du catalogue Olist |
| `seller_id` | texte hexadécimal (32) | Vendeur historique associé au produit |
| `shipping_limit_date` | date ISO 8601 UTC | Limite d'expédition simulée, 7 jours après l'achat |
| `price` | décimal (12,2) | Prix médian catalogue par unité |
| `freight_value` | décimal (12,2) | Frais de port moyens par unité |
| `payment_sequential` | entier | Rang du paiement, ici `1` |
| `payment_type` | texte | `credit_card`, `boleto` ou `voucher` |
| `payment_installments` | entier | 1 à 10 pour la carte, 1 pour boleto/bon |
| `payment_value` | décimal (12,2) | Montant total payé (produits + frais) ; répété par ligne article |

Une ingestion doit produire une seule ligne de paiement par couple `(order_id, payment_sequential)` et ne pas additionner `payment_value` sur les lignes d'articles. Le montant de chaque article reste `price + freight_value`.
