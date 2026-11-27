import csv
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from boutique import config

CSV_COLUMNS = [
    "order_id", "customer_id", "customer_unique_id", "customer_zip_code_prefix",
    "customer_city", "customer_state", "order_status", "order_purchase_timestamp",
    "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date",
    "order_estimated_delivery_date", "order_item_id", "product_id", "seller_id",
    "shipping_limit_date", "price", "freight_value", "payment_sequential",
    "payment_type", "payment_installments", "payment_value",
]


def montant(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def preparer_commande(order_id: str, account: dict, items: list[dict], payment_type: str,
                      installments: int, total: Decimal, created_at: datetime) -> tuple[str, Path, Path]:
    customer_id = uuid.uuid4().hex
    created_utc = created_at.astimezone(timezone.utc)
    estimated_delivery = created_utc + timedelta(days=14)
    shipping_limit = created_utc + timedelta(days=7)
    filename = f"commande_{created_utc:%Y%m%dT%H%M%SZ}_{order_id}.csv"
    directory = Path(config.DONNEES_ENTRANTES)
    directory.mkdir(parents=True, exist_ok=True)
    final_path = directory / filename
    temporary_path = directory / f".{filename}.{uuid.uuid4().hex}.tmp"
    with temporary_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for index, item in enumerate(items, start=1):
            writer.writerow({
                "order_id": order_id,
                "customer_id": customer_id,
                "customer_unique_id": account["customer_unique_id"],
                "customer_zip_code_prefix": account["postal_code"],
                "customer_city": account["city"],
                "customer_state": account["state"],
                "order_status": "processing",
                "order_purchase_timestamp": created_utc.isoformat(),
                "order_approved_at": created_utc.isoformat(),
                "order_delivered_carrier_date": "",
                "order_delivered_customer_date": "",
                "order_estimated_delivery_date": estimated_delivery.isoformat(),
                "order_item_id": index,
                "product_id": item["product_id"],
                "seller_id": item["seller_id"],
                "shipping_limit_date": shipping_limit.isoformat(),
                "price": f'{montant(item["price"]):.2f}',
                "freight_value": f'{montant(item["freight"]):.2f}',
                "payment_sequential": 1,
                "payment_type": payment_type,
                "payment_installments": installments,
                "payment_value": f"{total:.2f}",
            })
        file.flush()
        os.fsync(file.fileno())
    return filename, temporary_path, final_path


def publier_commande(order_id: str, account: dict, items: list[dict], payment_type: str,
                     installments: int, total: Decimal, created_at: datetime) -> str:
    filename, temporary_path, final_path = preparer_commande(
        order_id, account, items, payment_type, installments, total, created_at
    )
    try:
        os.replace(temporary_path, final_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return filename
