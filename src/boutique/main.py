from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import os
import uuid

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.middleware.sessions import SessionMiddleware

from boutique import config
from boutique.auth import admin_requis, client_connecte, client_requis
from boutique.db import connection, start_database, stop_database
from boutique.models import Registration, simplifier_ville
from boutique.orders import montant, preparer_commande
from boutique.security import csrf_token, hash_password, verify_csrf, verify_password

templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
def format_montant(value):
    # Une commande annulée sans article n'a pas de montant (NULL en base).
    if value is None:
        return "—"
    return f"{Decimal(str(value)):,.2f} R$".replace(",", " ")


templates.env.filters["money"] = format_montant


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        start_database()
        yield
    finally:
        stop_database()


app = FastAPI(title="Boutique Olist", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="olist_session",
    max_age=60 * 60 * 8,
    same_site="lax",
    https_only=config.SESSION_HTTPS_ONLY,
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).with_name("static"))), name="static")
templates.env.globals["max_cart_products"] = config.MAX_CART_PRODUCTS


def render(request: Request, template: str, **context):
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={"csrf_token": csrf_token(request), **context},
    )


def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            columns = [column.name for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def fetch_one(query: str, params: tuple = ()) -> dict | None:
    rows = fetch_all(query, params)
    return rows[0] if rows else None


def session_cart(request: Request) -> dict[str, int]:
    cart = request.session.get("cart", {})
    if not isinstance(cart, dict) or len(cart) > config.MAX_CART_PRODUCTS or any(
        not isinstance(key, str)
        or len(key) != 32
        or any(char not in "0123456789abcdef" for char in key)
        or not isinstance(quantity, int)
        or not 1 <= quantity <= config.MAX_QUANTITY
        for key, quantity in cart.items()
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Panier de session invalide")
    return cart


@app.get("/")
def home():
    return RedirectResponse("/catalogue", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/api/robot/references")
def robot_references():
    categories = fetch_all(
        """
        SELECT product_category_name_english AS category, sum(nb_ventes) AS frequency
        FROM gold.produits
        WHERE prix_median IS NOT NULL AND nb_ventes IS NOT NULL
        GROUP BY product_category_name_english
        ORDER BY category
        """
    )
    locations = fetch_all(
        """
        SELECT customer_city AS city, customer_state AS state,
               customer_zip_code_prefix AS postal_code, count(*) AS frequency
        FROM silver.clients
        GROUP BY customer_city, customer_state, customer_zip_code_prefix
        ORDER BY frequency DESC
        LIMIT 100
        """
    )
    payments = fetch_all(
        """
        SELECT payment_type, count(*) AS frequency
        FROM silver.paiements
        WHERE payment_type IN ('credit_card', 'boleto', 'voucher')
        GROUP BY payment_type
        """
    )
    return {"categories": categories, "locations": locations, "payments": payments}


@app.get("/catalogue")
def catalogue(request: Request, q: str = "", categorie: str = "", page: int = 1):
    page = max(page, 1)
    search = q.strip()[:100]
    filters = ["p.prix_median IS NOT NULL", "p.frais_port_moyen IS NOT NULL",
               "EXISTS (SELECT 1 FROM silver.articles a WHERE a.product_id = p.product_id)"]
    params: list = []
    if search:
        filters.append("(p.product_category_name_english ILIKE %s OR p.product_id ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    if categorie:
        filters.append("p.product_category_name_english = %s")
        params.append(categorie)
    where = " AND ".join(filters)
    products = fetch_all(
        f"""
        SELECT p.product_id, p.product_category_name_english AS category,
               p.product_weight_g AS weight_g, p.prix_median AS price,
               p.frais_port_moyen AS freight
        FROM gold.produits p
        WHERE {where}
        ORDER BY p.product_category_name_english, p.product_id
        LIMIT %s OFFSET %s
        """,
        tuple(params + [config.PAGE_SIZE, (page - 1) * config.PAGE_SIZE]),
    )
    count = fetch_one(
        f"""
        SELECT count(*) AS total
        FROM gold.produits p
        WHERE {where}
          AND EXISTS (SELECT 1 FROM silver.articles a WHERE a.product_id = p.product_id)
        """,
        tuple(params),
    )["total"]
    categories = fetch_all(
        """
        SELECT DISTINCT product_category_name_english AS category
        FROM gold.produits
        WHERE prix_median IS NOT NULL
        ORDER BY category
        """
    )
    return render(
        request,
        "catalogue.html",
        products=products,
        categories=categories,
        q=search,
        categorie=categorie,
        page=page,
        has_previous=page > 1,
        has_next=page * config.PAGE_SIZE < count,
        cart_count=sum(session_cart(request).values()),
    )


@app.get("/produit/{product_id}")
def fiche_produit(request: Request, product_id: str):
    if len(product_id) != 32 or any(char not in "0123456789abcdef" for char in product_id):
        raise HTTPException(status_code=404, detail="Produit introuvable")
    product = fetch_one(
        """
        SELECT p.product_id, p.product_category_name_english AS category,
               p.product_weight_g AS weight_g, p.product_length_cm AS length_cm,
               p.product_height_cm AS height_cm, p.product_width_cm AS width_cm,
               p.prix_median AS price, p.frais_port_moyen AS freight
        FROM gold.produits p
        WHERE p.product_id = %s AND p.prix_median IS NOT NULL
          AND p.frais_port_moyen IS NOT NULL
          AND EXISTS (SELECT 1 FROM silver.articles a WHERE a.product_id = p.product_id)
        """,
        (product_id,),
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    return render(request, "produit.html", product=product)


@app.get("/inscription")
def inscription_page(request: Request):
    return render(request, "inscription.html", error=None)


@app.post("/inscription")
def inscription(
    request: Request,
    csrf: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    city: str = Form(...),
    state: str = Form(...),
    postal_code: str = Form(...),
):
    verify_csrf(request, csrf)
    try:
        data = Registration(
            email=email,
            password=password,
            full_name=full_name,
            city=city,
            state=state,
            postal_code=postal_code,
        )
    except ValidationError as error:
        return render(request, "inscription.html", error=str(error))

    known_city = fetch_one(
        """
        SELECT customer_city FROM silver.clients
        WHERE customer_city = %s AND customer_state = %s
        LIMIT 1
        """,
        (simplifier_ville(data.city), data.state),
    )
    if known_city is None:
        return render(
            request,
            "inscription.html",
            error="Ville inconnue dans les données Olist ; vérifiez l’orthographe.",
        )

    with connection() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO boutique.accounts
                        (email, password_hash, role, full_name, customer_unique_id, city, state, postal_code)
                    VALUES (%s, %s, 'client', %s, %s, %s, %s, %s)
                    ON CONFLICT (email) DO NOTHING
                    RETURNING account_id
                    """,
                    (
                        str(data.email),
                        hash_password(data.password),
                        data.full_name,
                        uuid.uuid4().hex,
                        simplifier_ville(data.city),
                        data.state,
                        data.postal_code,
                    ),
                )
                inserted = cur.fetchone()
    if inserted is None:
        return render(request, "inscription.html", error="Cette adresse e-mail est déjà utilisée.")
    request.session.clear()
    request.session["account_id"] = inserted[0]
    request.session["account_role"] = "client"
    csrf_token(request)
    return RedirectResponse("/catalogue", status_code=status.HTTP_303_SEE_OTHER)


def login(request: Request, email: str, password: str, expected_role: str, template: str):
    normalized_email = email.strip().lower()
    account = fetch_one(
        "SELECT account_id, password_hash, role FROM boutique.accounts WHERE email = %s",
        (normalized_email,),
    )
    if account is None or not verify_password(password, account["password_hash"]):
        return render(request, template, error="Identifiants incorrects.")
    if account["role"] != expected_role:
        return render(request, template, error="Identifiants incorrects.")
    request.session.clear()
    request.session["account_id"] = account["account_id"]
    request.session["account_role"] = expected_role
    csrf_token(request)
    destination = "/admin/dashboard" if expected_role == "admin" else "/catalogue"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/connexion")
def connexion_page(request: Request):
    return render(request, "connexion.html", error=None)


@app.post("/connexion")
def connexion_client(
    request: Request,
    csrf: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    verify_csrf(request, csrf)
    return login(request, email, password, "client", "connexion.html")


@app.get("/admin/connexion")
def connexion_admin_page(request: Request):
    return render(request, "connexion_admin.html", error=None)


@app.post("/admin/connexion")
def connexion_admin(
    request: Request,
    csrf: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    verify_csrf(request, csrf)
    return login(request, email, password, "admin", "connexion_admin.html")


@app.post("/deconnexion")
def deconnexion(request: Request, csrf: str = Form(...)):
    verify_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/catalogue", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/panier/ajouter")
def ajouter_panier(
    request: Request,
    account: dict = Depends(client_requis),
    csrf: str = Form(...),
    product_id: str = Form(...),
    quantity: int = Form(1),
):
    del account
    verify_csrf(request, csrf)
    if len(product_id) != 32 or any(char not in "0123456789abcdef" for char in product_id):
        raise HTTPException(status_code=400, detail="Identifiant produit invalide")
    if not 1 <= quantity <= config.MAX_QUANTITY:
        raise HTTPException(status_code=400, detail=f"Quantité comprise entre 1 et {config.MAX_QUANTITY}")
    if fetch_one(
        """
        SELECT p.product_id FROM gold.produits p
        WHERE p.product_id = %s AND p.prix_median IS NOT NULL
          AND p.frais_port_moyen IS NOT NULL
          AND EXISTS (SELECT 1 FROM silver.articles a WHERE a.product_id = p.product_id)
        """,
        (product_id,),
    ) is None:
        raise HTTPException(status_code=404, detail="Produit indisponible")
    cart = session_cart(request)
    if product_id not in cart and len(cart) >= config.MAX_CART_PRODUCTS:
        raise HTTPException(status_code=409, detail="Le panier est limité à 20 produits différents.")
    new_quantity = cart.get(product_id, 0) + quantity
    if new_quantity > config.MAX_QUANTITY:
        raise HTTPException(status_code=400, detail=f"Quantité totale limitée à {config.MAX_QUANTITY}")
    cart[product_id] = new_quantity
    request.session["cart"] = cart
    return RedirectResponse("/panier", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/panier")
def panier(request: Request, account: dict = Depends(client_requis)):
    del account
    cart = session_cart(request)
    products = []
    if cart:
        products = fetch_all(
            """
            SELECT p.product_id, p.product_category_name_english AS category,
                   p.prix_median AS price, p.frais_port_moyen AS freight
            FROM gold.produits p
            WHERE p.product_id = ANY(%s)
              AND p.prix_median IS NOT NULL AND p.frais_port_moyen IS NOT NULL
              AND EXISTS (SELECT 1 FROM silver.articles a WHERE a.product_id = p.product_id)
            """,
            (list(cart),),
        )
    indexed = {product["product_id"].strip(): product for product in products}
    lines = []
    for product_id, quantity in cart.items():
        product = indexed.get(product_id)
        if product is None:
            raise HTTPException(status_code=409, detail="Un produit du panier n’est plus disponible.")
        product["quantity"] = quantity
        product["line_total"] = montant(
            (Decimal(str(product["price"])) + Decimal(str(product["freight"]))) * quantity
        )
        lines.append(product)
    total = sum((line["line_total"] for line in lines), Decimal("0.00"))
    return render(request, "panier.html", lines=lines, total=total)


@app.post("/panier/modifier")
def modifier_panier(
    request: Request,
    account: dict = Depends(client_requis),
    csrf: str = Form(...),
    product_id: str = Form(...),
    quantity: int = Form(...),
):
    del account
    verify_csrf(request, csrf)
    if not 1 <= quantity <= config.MAX_QUANTITY:
        raise HTTPException(status_code=400, detail=f"Quantité comprise entre 1 et {config.MAX_QUANTITY}")
    cart = session_cart(request)
    if product_id not in cart:
        raise HTTPException(status_code=404, detail="Article absent du panier")
    cart[product_id] = quantity
    request.session["cart"] = cart
    return RedirectResponse("/panier", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/panier/retirer")
def retirer_panier(
    request: Request,
    account: dict = Depends(client_requis),
    csrf: str = Form(...),
    product_id: str = Form(...),
):
    del account
    verify_csrf(request, csrf)
    cart = session_cart(request)
    cart.pop(product_id, None)
    request.session["cart"] = cart
    return RedirectResponse("/panier", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/commande")
def commander(
    request: Request,
    account: dict = Depends(client_requis),
    csrf: str = Form(...),
    payment_type: str = Form(...),
    installments: int = Form(1),
):
    verify_csrf(request, csrf)
    cart = session_cart(request)
    if not cart:
        raise HTTPException(status_code=409, detail="Le panier est vide")
    if payment_type not in {"credit_card", "boleto", "voucher"}:
        raise HTTPException(status_code=400, detail="Moyen de paiement inconnu")
    if payment_type == "credit_card" and not 1 <= installments <= 10:
        raise HTTPException(status_code=400, detail="La carte accepte de 1 à 10 échéances")
    if payment_type != "credit_card" and installments != 1:
        raise HTTPException(status_code=400, detail="Ce moyen de paiement ne permet pas d’échéances")

    products = fetch_all(
        """
        SELECT p.product_id, p.prix_median AS price, p.frais_port_moyen AS freight,
               seller.seller_id
        FROM gold.produits p
        JOIN LATERAL (
            SELECT a.seller_id FROM silver.articles a
            WHERE a.product_id = p.product_id ORDER BY a.seller_id LIMIT 1
        ) seller ON TRUE
        WHERE p.product_id = ANY(%s)
          AND p.prix_median IS NOT NULL AND p.frais_port_moyen IS NOT NULL
        """,
        (list(cart),),
    )
    by_id = {row["product_id"].strip(): row for row in products}
    if set(by_id) != set(cart):
        raise HTTPException(status_code=409, detail="Un produit du panier n’est plus disponible.")
    order_items = []
    total = Decimal("0.00")
    for product_id, quantity in cart.items():
        product = by_id[product_id]
        price = montant(product["price"])
        freight = montant(product["freight"])
        total += (price + freight) * quantity
        for _ in range(quantity):
            order_items.append({
                "product_id": product_id,
                "seller_id": product["seller_id"].strip(),
                "price": price,
                "freight": freight,
            })

    order_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc)
    csv_filename, temporary_path, final_path = preparer_commande(
        order_id, account, order_items, payment_type, installments, total, created_at
    )
    try:
        with connection() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO boutique.orders (order_id, account_id, created_at, total, csv_filename)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (order_id, account["account_id"], created_at, total, csv_filename),
                    )
                os.replace(temporary_path, final_path)
    except Exception:
        if final_path.exists():
            final_path.unlink()
        raise
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    request.session["cart"] = {}
    return RedirectResponse("/compte", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/compte")
def compte(request: Request, account: dict = Depends(client_requis)):
    orders = fetch_all(
        """
        SELECT o.order_id, o.created_at, o.total,
               COALESCE(s.order_status, o.status) AS status
        FROM boutique.orders o
        LEFT JOIN silver.commandes s ON s.order_id = o.order_id
        WHERE o.account_id = %s
        ORDER BY o.created_at DESC
        LIMIT 100
        """,
        (account["account_id"],),
    )
    segment = fetch_one(
        """
        SELECT segment FROM gold.dim_client
        WHERE customer_unique_id = %s AND est_courant
        """,
        (account["customer_unique_id"],),
    )
    return render(request, "compte.html", account=account, orders=orders, segment=segment)


admin_router = APIRouter(prefix="/admin", dependencies=[Depends(admin_requis)])


@admin_router.get("/dashboard")
def admin_dashboard(request: Request):
    latest = fetch_all(
        """
        SELECT order_id, customer_unique_id, order_status, order_purchase_timestamp, montant_total
        FROM gold.commandes
        ORDER BY order_purchase_timestamp DESC
        LIMIT 20
        """
    )
    updated = fetch_one(
        "SELECT updated_at FROM gold.etat_donnees WHERE source = %s",
        ("gold",),
    )
    return render(request, "admin_dashboard.html", orders=latest, updated_at=updated["updated_at"])


@admin_router.get("/clients")
def admin_clients(request: Request):
    clients = fetch_all(
        """
        SELECT a.full_name, a.email, a.city, a.state, d.segment
        FROM boutique.accounts a
        LEFT JOIN gold.dim_client d
          ON d.customer_unique_id = a.customer_unique_id AND d.est_courant
        WHERE a.role = 'client'
        ORDER BY a.created_at DESC
        LIMIT 500
        """
    )
    return render(request, "admin_clients.html", clients=clients)


app.include_router(admin_router)
