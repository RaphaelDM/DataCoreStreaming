"""Robot HTTP de la boutique : inscriptions, connexions, paniers et commandes."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import random
import re
import string
from threading import Lock
import time

import requests


class CSRFParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "csrf":
            self.token = values.get("value")


def csrf_from(response: requests.Response) -> str:
    parser = CSRFParser()
    parser.feed(response.text)
    if not parser.token:
        raise RuntimeError(f"Jeton CSRF absent de {response.url}")
    return parser.token


def register_client(base_url: str, index: int, locations: list[dict],
                    unknown_city: bool = False) -> requests.Session | None:
    session = requests.Session()
    page = session.get(f"{base_url}/inscription", timeout=15)
    page.raise_for_status()
    city_data = random.choices(
        locations, weights=[location["frequency"] for location in locations], k=1
    )[0]
    city, state, postal_code = (
        city_data["city"], city_data["state"], city_data["postal_code"]
    )
    if unknown_city:
        city = "ville inconnue"
    email = f"robot-{index}-{''.join(random.choices(string.ascii_lowercase, k=8))}@example.com"
    password = f"Robot-{index}-{''.join(random.choices(string.ascii_letters + string.digits, k=16))}"
    response = session.post(
        f"{base_url}/inscription",
        data={
            "csrf": csrf_from(page),
            "email": email,
            "password": password,
            "full_name": f"Client robot {index}",
            "city": city,
            "state": state,
            "postal_code": postal_code,
        },
        allow_redirects=True,
        timeout=15,
    )
    if unknown_city:
        if "Ville inconnue" not in response.text:
            raise RuntimeError("La ville inconnue n'a pas été rejetée par le formulaire")
        return None
    response.raise_for_status()
    if "/catalogue" not in response.url:
        raise RuntimeError(f"Inscription refusée pour {email}: {response.url}")
    catalogue = session.get(f"{base_url}/catalogue", timeout=15)
    catalogue.raise_for_status()
    logout = session.post(
        f"{base_url}/deconnexion",
        data={"csrf": csrf_from(catalogue)},
        allow_redirects=True,
        timeout=15,
    )
    logout.raise_for_status()
    login_page = session.get(f"{base_url}/connexion", timeout=15)
    login_page.raise_for_status()
    logged_in = session.post(
        f"{base_url}/connexion",
        data={"csrf": csrf_from(login_page), "email": email, "password": password},
        allow_redirects=True,
        timeout=15,
    )
    logged_in.raise_for_status()
    if "/catalogue" not in logged_in.url:
        raise RuntimeError(f"Connexion refusée pour le client robot {index}")
    return session


def products_for_category(session: requests.Session, base_url: str, category: str) -> list[str]:
    page = session.get(f"{base_url}/catalogue", params={"categorie": category}, timeout=15)
    page.raise_for_status()
    return re.findall(r'href="/produit/([0-9a-f]{32})"', page.text)


def place_order(session: requests.Session, base_url: str, category: str, anomaly: bool,
                payment_types: list[str], payment_weights: list[int]) -> str:
    product_ids = products_for_category(session, base_url, category)
    if not product_ids:
        product_ids = re.findall(
            r'href="/produit/([0-9a-f]{32})"',
            session.get(f"{base_url}/catalogue", timeout=15).text,
        )
    if not product_ids:
        raise RuntimeError("Le catalogue ne contient aucun produit commandable")
    product_id = random.choice(product_ids)
    detail = session.get(f"{base_url}/produit/{product_id}", timeout=15)
    detail.raise_for_status()
    reject_quantity = anomaly and random.random() < 0.5
    add = session.post(
        f"{base_url}/panier/ajouter",
        data={
            "csrf": csrf_from(detail),
            "product_id": product_id,
            "quantity": 11 if reject_quantity else random.randint(1, 3),
        },
        allow_redirects=True,
        timeout=15,
    )
    if reject_quantity:
        if add.status_code != 400:
            raise RuntimeError("La quantité hors limites n'a pas été rejetée")
        return "rejet_quantite"
    add.raise_for_status()

    cart = session.get(f"{base_url}/panier", timeout=15)
    cart.raise_for_status()
    if anomaly and random.random() < 0.5:
        return "panier_abandonne"
    payment_type = random.choices(payment_types, weights=payment_weights, k=1)[0]
    checkout = session.post(
        f"{base_url}/commande",
        data={
            "csrf": csrf_from(cart),
            "payment_type": payment_type,
            "installments": random.randint(1, 10) if payment_type == "credit_card" else 1,
        },
        allow_redirects=True,
        timeout=20,
    )
    checkout.raise_for_status()
    if "/compte" not in checkout.url:
        raise RuntimeError("La commande n'a pas été confirmée")
    return "commande"


def run(args):
    random.seed()
    base_url = args.base_url.rstrip("/")
    references_response = requests.get(f"{base_url}/api/robot/references", timeout=15)
    references_response.raise_for_status()
    references = references_response.json()
    categories = [row["category"] for row in references["categories"]]
    category_weights = [row["frequency"] for row in references["categories"]]
    locations = references["locations"]
    payment_rows = references["payments"]
    payment_types = [row["payment_type"] for row in payment_rows]
    payment_weights = [row["frequency"] for row in payment_rows]
    if not categories or not locations or not payment_types:
        raise RuntimeError("Les données de référence du robot sont vides")

    sessions = []
    for index in range(args.clients):
        if random.random() < args.anomaly_rate:
            register_client(base_url, index, locations, unknown_city=True)
        session = register_client(base_url, index, locations)
        if session is None:
            raise RuntimeError(f"Inscription du client robot {index} sans résultat")
        sessions.append(session)

    total_orders = args.orders_per_minute * args.duration_minutes
    if total_orders < 1:
        raise ValueError("Le nombre de commandes à générer doit être positif")
    start = time.monotonic()
    results = []
    client_locks = [Lock() for _ in sessions]
    with ThreadPoolExecutor(max_workers=min(args.clients, 16)) as executor:
        futures = []
        for order_number in range(total_orders):
            session = sessions[order_number % len(sessions)]
            category = random.choices(categories, weights=category_weights, k=1)[0]
            anomaly = random.random() < args.anomaly_rate
            due = start + order_number * 60 / args.orders_per_minute
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            futures.append(executor.submit(
                place_order_locked, session, client_locks[order_number % len(sessions)],
                base_url, category, anomaly, payment_types, payment_weights,
            ))
        for future in futures:
            results.append(future.result())
    print(f"Résultats : {results.count('commande')} commandes, "
          f"{results.count('rejet_quantite')} quantités rejetées, "
          f"{results.count('panier_abandonne')} paniers abandonnés.")


def place_order_locked(session, lock, base_url, category, anomaly,
                       payment_types, payment_weights):
    with lock:
        return place_order(session, base_url, category, anomaly, payment_types, payment_weights)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--clients", type=int, default=10)
    parser.add_argument("--orders-per-minute", type=int, default=10)
    parser.add_argument("--duration-minutes", type=int, default=1)
    parser.add_argument("--anomaly-rate", type=float, default=0.05)
    args = parser.parse_args()
    if args.clients < 1 or args.orders_per_minute < 1 or args.duration_minutes < 1:
        parser.error("clients, orders-per-minute et duration-minutes doivent être positifs")
    if not 0 <= args.anomaly_rate <= 1:
        parser.error("anomaly-rate doit être comprise entre 0 et 1")
    run(args)


if __name__ == "__main__":
    main()
