FROM apache/airflow:3.1.3

# Bibliothèques de nos traitements (nettoyage.py, agregations.py, clustering.py).
# On réinstalle la même version d'Airflow dans la commande pour que pip ne la
# remplace pas en résolvant les dépendances.
RUN pip install --no-cache-dir \
    "apache-airflow==${AIRFLOW_VERSION}" \
    pandas \
    pyarrow \
    scikit-learn \
    joblib
