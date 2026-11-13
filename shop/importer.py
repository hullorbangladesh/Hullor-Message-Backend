import csv
import io
import re
from dataclasses import dataclass, field

from django.utils.text import slugify

from .models import Product

TEXT_COLUMNS = ("name_bn", "category", "keywords", "description", "image_url")


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    errors: list = field(default_factory=list)


def _parse_int(raw):
    cleaned = re.sub(r"[^\d.]", "", str(raw or ""))
    if not cleaned:
        raise ValueError("empty")
    return int(float(cleaned))


def import_products_csv(data: bytes) -> ImportResult:
    """Create or update products from CSV bytes.

    Required columns: name, price.
    Optional: sku, name_bn, category, keywords, description, stock, image_url, is_active.
    Rows with the same sku are updated; if sku is empty it is made from the name.
    """
    result = ImportResult()
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        result.errors.append("The file is empty.")
        return result
    reader.fieldnames = [(h or "").strip().lower() for h in reader.fieldnames]
    for required in ("name", "price"):
        if required not in reader.fieldnames:
            result.errors.append(f"Missing required column: {required}")
    if result.errors:
        return result

    columns = set(reader.fieldnames)
    for lineno, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        if not name:
            continue
        try:
            price = _parse_int(row.get("price"))
        except ValueError:
            result.errors.append(f"Line {lineno} ({name}): invalid price")
            continue

        sku = (row.get("sku") or "").strip() or slugify(name, allow_unicode=True)[:80]
        if not sku:
            result.errors.append(f"Line {lineno} ({name}): could not make a sku, add a sku column")
            continue

        defaults = {"name": name[:200], "price": price}
        for col in TEXT_COLUMNS:
            if col in columns:
                defaults[col] = (row.get(col) or "").strip()
        if "stock" in columns and (row.get("stock") or "").strip():
            try:
                defaults["stock"] = _parse_int(row.get("stock"))
            except ValueError:
                result.errors.append(f"Line {lineno} ({name}): invalid stock, left unchanged")
        if "is_active" in columns and (row.get("is_active") or "").strip():
            defaults["is_active"] = row["is_active"].strip().lower() in ("1", "yes", "y", "true", "active")

        _, created = Product.objects.update_or_create(sku=sku, defaults=defaults)
        if created:
            result.created += 1
        else:
            result.updated += 1
    return result
