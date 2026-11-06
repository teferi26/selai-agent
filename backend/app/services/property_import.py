"""CSV catalog import — pure parsing/mapping logic.

Kept free of DB access so it is unit-testable: the endpoint feeds raw bytes
in and gets back a list of `Property(**kwargs)`-ready dicts plus per-row
error messages (Spanish — they surface in the dashboard UI).

Handles the messy reality of agency exports: `,` or `;` separators,
UTF-8 with BOM or latin-1 encodings, Spanish or English column names,
and prices formatted like "185.000 €".
"""
from __future__ import annotations

import csv
import io
import unicodedata
import uuid
from dataclasses import dataclass, field

from app.db.models import PropertyType

# ---------------------------------------------------------------------------
# Header mapping
# ---------------------------------------------------------------------------

# normalized alias -> canonical field name
_HEADER_ALIASES: dict[str, str] = {
    # title
    "titulo": "title",
    "title": "title",
    "nombre": "title",
    # price
    "precio": "price",
    "price": "price",
    "importe": "price",
    # city
    "ciudad": "city",
    "city": "city",
    "localidad": "city",
    "municipio": "city",
    "poblacion": "city",
    # address
    "direccion": "address",
    "address": "address",
    "calle": "address",
    # type
    "tipo": "property_type",
    "type": "property_type",
    "tipoinmueble": "property_type",
    "tipodeinmueble": "property_type",
    "propertytype": "property_type",
    # bedrooms
    "habitaciones": "bedrooms",
    "bedrooms": "bedrooms",
    "dormitorios": "bedrooms",
    "hab": "bedrooms",
    # bathrooms
    "banos": "bathrooms",
    "bathrooms": "bathrooms",
    "aseos": "bathrooms",
    "bano": "bathrooms",
    # area
    "m2": "area_sqm",
    "superficie": "area_sqm",
    "areasqm": "area_sqm",
    "area": "area_sqm",
    "metros": "area_sqm",
    "metroscuadrados": "area_sqm",
    # description
    "descripcion": "description",
    "description": "description",
    # reference
    "referencia": "reference",
    "ref": "reference",
    "externalref": "reference",
    "reference": "reference",
    "codigo": "reference",
    # url (stored in Property.extra["source_url"])
    "url": "url",
    "enlace": "url",
    "link": "url",
    # extras that map to real columns
    "barrio": "neighborhood",
    "zona": "neighborhood",
    "neighborhood": "neighborhood",
    "cp": "postal_code",
    "codigopostal": "postal_code",
    "postalcode": "postal_code",
    "operacion": "operation",
    "operation": "operation",
}

_TYPE_ALIASES: dict[str, PropertyType] = {
    "piso": PropertyType.APARTMENT,
    "apartamento": PropertyType.APARTMENT,
    "apartment": PropertyType.APARTMENT,
    "flat": PropertyType.APARTMENT,
    "casa": PropertyType.HOUSE,
    "chalet": PropertyType.HOUSE,
    "adosado": PropertyType.HOUSE,
    "villa": PropertyType.HOUSE,
    "house": PropertyType.HOUSE,
    "duplex": PropertyType.DUPLEX,
    "estudio": PropertyType.STUDIO,
    "studio": PropertyType.STUDIO,
    "atico": PropertyType.PENTHOUSE,
    "penthouse": PropertyType.PENTHOUSE,
    "oficina": PropertyType.OFFICE,
    "office": PropertyType.OFFICE,
    "local": PropertyType.COMMERCIAL,
    "localcomercial": PropertyType.COMMERCIAL,
    "comercial": PropertyType.COMMERCIAL,
    "commercial": PropertyType.COMMERCIAL,
    "terreno": PropertyType.LAND,
    "parcela": PropertyType.LAND,
    "suelo": PropertyType.LAND,
    "land": PropertyType.LAND,
}


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def normalize_header(name: str) -> str | None:
    """Map a raw CSV header to a canonical field name, or None if unknown."""
    key = _strip_accents(name or "").lower().strip()
    key = key.replace("²", "2")
    key = "".join(ch for ch in key if ch.isalnum())
    return _HEADER_ALIASES.get(key)


def map_headers(headers: list[str]) -> dict[int, str]:
    """Column index -> canonical field. First occurrence of a field wins."""
    mapped: dict[int, str] = {}
    seen: set[str] = set()
    for idx, raw in enumerate(headers):
        canonical = normalize_header(raw)
        if canonical and canonical not in seen:
            mapped[idx] = canonical
            seen.add(canonical)
    return mapped


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------


def parse_price(raw: str | None) -> int | None:
    """Parse '185.000 €', '185000', '1,200,000.50' ... into integer euros."""
    if raw is None:
        return None
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch in ".,-")
    if not cleaned or not any(ch.isdigit() for ch in cleaned):
        return None
    if "." in cleaned and "," in cleaned:
        # Rightmost symbol is the decimal separator; the other is thousands.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        head, _, tail = cleaned.rpartition(",")
        # ",50" → decimals; ",000" (3 digits) → thousands separator.
        cleaned = f"{head}.{tail}" if len(tail) == 2 else cleaned.replace(",", "")
    elif "." in cleaned:
        head, _, tail = cleaned.rpartition(".")
        if len(tail) == 3:  # "185.000" → Spanish thousands
            cleaned = cleaned.replace(".", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value <= 0:
        return None
    return int(value)


def _parse_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    digits = "".join(ch for ch in raw.split(",")[0].split(".")[0] if ch.isdigit())
    return int(digits) if digits else None


def recognize_type(raw: str | None) -> PropertyType | None:
    """The listing type, or None when the file's word is not one we know.

    None is not the same as "apartment": a CSV that says "Nave industrial"
    tells us nothing, and guessing would retype a listing the agency
    curated — which changes what the AI offers to clients.
    """
    key = _strip_accents(raw or "").lower().strip()
    key = "".join(ch for ch in key if ch.isalnum())
    return _TYPE_ALIASES.get(key)


_RENT_WORDS = frozenset({"alquiler", "rent", "renta", "arrendamiento", "alquilar"})
_SALE_WORDS = frozenset({"venta", "sale", "vender", "compra", "comprar", "buy"})


def recognize_operation(raw: str | None) -> str | None:
    """"sale" / "rent", or None when the word is not one we understand."""
    key = _strip_accents(raw or "").lower().strip()
    if key in _RENT_WORDS:
        return "rent"
    if key in _SALE_WORDS:
        return "sale"
    return None


def _parse_type(raw: str | None) -> PropertyType:
    """Same, with the default a *new* row needs (the column is NOT NULL)."""
    return recognize_type(raw) or PropertyType.APARTMENT


def _parse_operation(raw: str | None) -> str:
    return recognize_operation(raw) or "sale"


# ---------------------------------------------------------------------------
# CSV decoding + row parsing
# ---------------------------------------------------------------------------


def decode_csv_bytes(raw: bytes) -> str:
    """UTF-8 (BOM-tolerant) first, latin-1 fallback (never fails)."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def detect_delimiter(text: str) -> str:
    """Autodetect `,` vs `;` from the header line."""
    first_line = text.splitlines()[0] if text.splitlines() else ""
    return ";" if first_line.count(";") > first_line.count(",") else ","


@dataclass
class ImportParseResult:
    rows: list[dict] = field(default_factory=list)  # Property(**kwargs)-ready
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    # Canonical field names that the CSV actually carried a value for, one
    # set per row (same order/length as `rows`). Needed for the upsert: a
    # column the file does not have must never overwrite what the agency
    # already curated in selai.
    provided: list[set[str]] = field(default_factory=list)


def parse_properties_csv(raw: bytes) -> ImportParseResult:
    result = ImportParseResult()
    text = decode_csv_bytes(raw).strip()
    if not text:
        result.errors.append("El archivo está vacío.")
        return result

    reader = csv.reader(io.StringIO(text), delimiter=detect_delimiter(text))
    try:
        headers = next(reader)
    except StopIteration:
        result.errors.append("El archivo está vacío.")
        return result

    columns = map_headers(headers)
    if "title" not in columns.values() and "price" not in columns.values():
        result.errors.append(
            "No se reconocen las columnas del archivo. Se esperan cabeceras como "
            "'titulo', 'precio', 'ciudad', 'direccion'..."
        )
        return result

    for line_no, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue  # blank line

        values: dict[str, str] = {}
        for idx, fieldname in columns.items():
            if idx < len(cells) and cells[idx].strip():
                values[fieldname] = cells[idx].strip()

        title = values.get("title")
        price = parse_price(values.get("price"))
        if not title:
            result.skipped += 1
            result.errors.append(f"Fila {line_no}: falta el título.")
            continue
        if price is None:
            result.skipped += 1
            result.errors.append(f"Fila {line_no}: precio ausente o inválido.")
            continue

        extra: dict = {"import_source": "csv"}
        if values.get("url"):
            extra["source_url"] = values["url"]

        # `provided` drives the upsert, so it must list the fields the file
        # really pinned down — not the ones it merely had a column for. A
        # `tipo`/`operacion` value we cannot map is dropped here: the new
        # row still gets the default it needs, an existing row keeps what
        # the agency curated.
        provided = set(values)
        if "property_type" in values and recognize_type(values["property_type"]) is None:
            provided.discard("property_type")
        if "operation" in values and recognize_operation(values["operation"]) is None:
            provided.discard("operation")

        result.provided.append(provided)
        result.rows.append(
            {
                "reference": values.get("reference")
                or f"IMP-{uuid.uuid4().hex[:8].upper()}",
                "title": title,
                "description": values.get("description", ""),
                "property_type": _parse_type(values.get("property_type")),
                "operation": _parse_operation(values.get("operation")),
                "price": price,
                "address": values.get("address", ""),
                "neighborhood": values.get("neighborhood"),
                "city": values.get("city", ""),
                "postal_code": values.get("postal_code"),
                "bedrooms": _parse_int(values.get("bedrooms")),
                "bathrooms": _parse_int(values.get("bathrooms")),
                "area_sqm": _parse_int(values.get("area_sqm")),
                "extra": extra,
            }
        )

    return result


# ---------------------------------------------------------------------------
# Upsert by reference
# ---------------------------------------------------------------------------

# Columns a re-import may refresh on an existing listing. `reference` is the
# match key, so it never moves; everything the agency edits *inside* selai
# (status, photos, embeddings, price history) is deliberately absent.
UPSERTABLE_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "property_type",
    "operation",
    "price",
    "address",
    "neighborhood",
    "city",
    "postal_code",
    "bedrooms",
    "bathrooms",
    "area_sqm",
)


def apply_row_to_property(prop, row: dict, provided: set[str]) -> bool:
    """Refresh an existing listing from a parsed CSV row.

    Only fields the file actually carried (`provided`) and that parsed to a
    non-empty value are written — a monthly export that omits the
    description must not blank the description the agency wrote by hand.
    Returns whether anything changed.
    """
    changed = False
    for name in UPSERTABLE_FIELDS:
        if name not in provided:
            continue
        value = row.get(name)
        if value is None or value == "":
            continue
        if getattr(prop, name, None) != value:
            setattr(prop, name, value)
            changed = True

    # `extra` is a JSON column: reassign the whole dict so SQLAlchemy's
    # change tracking notices.
    incoming_extra = row.get("extra") or {}
    if incoming_extra:
        merged = dict(prop.extra or {})
        if merged != {**merged, **incoming_extra}:
            merged.update(incoming_extra)
            prop.extra = merged
            changed = True

    return changed
