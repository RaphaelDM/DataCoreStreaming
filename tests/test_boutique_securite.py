import csv
from datetime import datetime, timezone
import os
import re

os.environ.setdefault("SESSION_SECRET", "test-only-session-secret")

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from starlette.requests import Request

from boutique import main
from boutique.models import Registration, simplifier_ville
from boutique.orders import preparer_commande, publier_commande
from boutique.security import hash_password, verify_csrf, verify_password


def test_mot_de_passe_hache_et_verifiable():
    hashed = hash_password("Un-mot-de-passe-solide-123")
    assert hashed != "Un-mot-de-passe-solide-123"
    assert hashed.startswith("$argon2id$")
    assert verify_password("Un-mot-de-passe-solide-123", hashed)
    assert not verify_password("autre-mot-de-passe", hashed)


def test_inscription_refuse_mot_de_passe_court_et_etat_inconnu():
    with pytest.raises(ValueError):
        Registration(
            email="client@example.test",
            password="trop-court",
            full_name="Client Olist",
            city="sao paulo",
            state="SP",
            postal_code="01001",
        )
    with pytest.raises(ValueError):
        Registration(
            email="client@example.test",
            password="mot-de-passe-de-test-123",
            full_name="Client Olist",
            city="sao paulo",
            state="XX",
            postal_code="01001",
        )


def test_normalisation_ville_bresilienne():
    assert simplifier_ville("  São   Paulo ") == "sao paulo"


def test_commande_csv_atomique_et_reconstructible(tmp_path, monkeypatch):
    from boutique import config

    monkeypatch.setattr(config, "DONNEES_ENTRANTES", tmp_path)
    order_id = "1" * 32
    account = {
        "customer_unique_id": "2" * 32,
        "postal_code": "01001",
        "city": "sao paulo",
        "state": "SP",
    }
    items = [
        {"product_id": "3" * 32, "seller_id": "4" * 32, "price": 10, "freight": 2},
        {"product_id": "5" * 32, "seller_id": "6" * 32, "price": 20, "freight": 3},
    ]
    filename = publier_commande(
        order_id, account, items, "credit_card", 3, 35,
        datetime(2026, 11, 6, 10, 15, 30, tzinfo=timezone.utc),
    )
    with (tmp_path / filename).open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert filename == f"commande_20261106T101530Z_{order_id}.csv"
    assert len(rows) == 2
    assert [row["order_item_id"] for row in rows] == ["1", "2"]
    assert {row["payment_value"] for row in rows} == {"35.00"}
    assert len({row["customer_id"] for row in rows}) == 1
    assert list(tmp_path.iterdir()) == [tmp_path / filename]


def test_preparation_commande_ne_publie_pas_le_csv_avant_validation(tmp_path, monkeypatch):
    from boutique import config

    monkeypatch.setattr(config, "DONNEES_ENTRANTES", tmp_path)
    filename, temporary_path, final_path = preparer_commande(
        "1" * 32,
        {"customer_unique_id": "2" * 32, "postal_code": "01001",
         "city": "sao paulo", "state": "SP"},
        [{"product_id": "3" * 32, "seller_id": "4" * 32, "price": 10, "freight": 2}],
        "boleto",
        1,
        12,
        datetime(2026, 11, 6, 10, 15, 30, tzinfo=timezone.utc),
    )
    assert temporary_path.exists()
    assert not final_path.exists()
    assert filename == final_path.name


def test_csrf_rejette_un_jeton_manquant_ou_modifie():
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "session": {"csrf_token": "valide"}})
    with pytest.raises(HTTPException) as error:
        verify_csrf(request, "falsifie")
    assert error.value.status_code == 403


def test_route_admin_interdite_a_un_client(monkeypatch):
    monkeypatch.setattr(main, "start_database", lambda: None)
    monkeypatch.setattr(main, "stop_database", lambda: None)
    main.app.dependency_overrides[main.client_connecte] = lambda: {"role": "client"}
    try:
        with TestClient(main.app) as client:
            response = client.get("/admin/dashboard")
        assert response.status_code == 403
    finally:
        main.app.dependency_overrides.clear()


def test_route_panier_refuse_un_csrf_invalide(monkeypatch):
    monkeypatch.setattr(main, "start_database", lambda: None)
    monkeypatch.setattr(main, "stop_database", lambda: None)
    main.app.dependency_overrides[main.client_connecte] = lambda: {"role": "client"}
    try:
        with TestClient(main.app) as client:
            page = client.get("/connexion")
            assert "httponly" in page.headers["set-cookie"].lower()
            token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            response = client.post(
                "/panier/ajouter",
                data={"csrf": token + "modifie", "product_id": "a" * 32, "quantity": "1"},
            )
        assert response.status_code == 403
    finally:
        main.app.dependency_overrides.clear()
