from fastapi import Depends, HTTPException, Request, status

from boutique.db import connection
from boutique.security import require_role


def client_connecte(request: Request) -> dict:
    account_id = request.session.get("account_id")
    if not isinstance(account_id, int):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Connexion requise")
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT account_id, email, role, full_name, customer_unique_id, city, state, postal_code
                FROM boutique.accounts WHERE account_id = %s
                """,
                (account_id,),
            )
            row = cur.fetchone()
    if row is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Connexion requise")
    keys = ("account_id", "email", "role", "full_name", "customer_unique_id", "city", "state", "postal_code")
    return dict(zip(keys, row))


def admin_requis(account: dict = Depends(client_connecte)) -> dict:
    return require_role(account, "admin")


def client_requis(account: dict = Depends(client_connecte)) -> dict:
    return require_role(account, "client")
