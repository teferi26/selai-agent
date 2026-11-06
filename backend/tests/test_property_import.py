"""CSV catalog import — parser + flexible column mapping."""
from app.db.models import PropertyType
from app.services.property_import import (
    decode_csv_bytes,
    detect_delimiter,
    map_headers,
    normalize_header,
    parse_price,
    parse_properties_csv,
)

# ---------------------------------------------------------------------------
# Header normalization
# ---------------------------------------------------------------------------


def test_spanish_headers_map_to_canonical_fields():
    assert normalize_header("Título") == "title"
    assert normalize_header("PRECIO") == "price"
    assert normalize_header("Dirección") == "address"
    assert normalize_header("baños") == "bathrooms"
    assert normalize_header("m²") == "area_sqm"
    assert normalize_header("Superficie") == "area_sqm"
    assert normalize_header("Referencia") == "reference"


def test_english_headers_map_to_canonical_fields():
    assert normalize_header("Title") == "title"
    assert normalize_header("price") == "price"
    assert normalize_header("Bedrooms") == "bedrooms"
    assert normalize_header("external_ref") == "reference"


def test_unknown_header_is_none():
    assert normalize_header("columna_rara") is None
    assert normalize_header("") is None


def test_map_headers_first_occurrence_wins():
    mapped = map_headers(["titulo", "title", "precio"])
    assert mapped == {0: "title", 2: "price"}


# ---------------------------------------------------------------------------
# Price parsing
# ---------------------------------------------------------------------------


def test_parse_price_plain():
    assert parse_price("185000") == 185000


def test_parse_price_spanish_thousands():
    assert parse_price("185.000 €") == 185000
    assert parse_price("1.250.000") == 1250000


def test_parse_price_spanish_decimals():
    assert parse_price("185.000,50") == 185000


def test_parse_price_english_format():
    assert parse_price("1,200,000.50") == 1200000


def test_parse_price_invalid():
    assert parse_price("a consultar") is None
    assert parse_price("") is None
    assert parse_price(None) is None
    assert parse_price("0") is None


# ---------------------------------------------------------------------------
# Delimiter + encoding
# ---------------------------------------------------------------------------


def test_detects_semicolon_delimiter():
    assert detect_delimiter("titulo;precio;ciudad\na;1;b") == ";"


def test_detects_comma_delimiter():
    assert detect_delimiter("titulo,precio,ciudad\na,1,b") == ","


def test_decodes_utf8_bom():
    assert decode_csv_bytes("titulo\ná".encode("utf-8-sig")).startswith("titulo")


def test_decodes_latin1_fallback():
    text = decode_csv_bytes("titulo\nático".encode("latin-1"))
    assert "ático" in text


# ---------------------------------------------------------------------------
# End-to-end parsing
# ---------------------------------------------------------------------------

CSV_COMMA = """\
titulo,precio,ciudad,direccion,tipo,habitaciones,banos,m2,descripcion,referencia
Piso en Malasaña,325.000 €,Madrid,Calle Pez 10,piso,2,1,78,Luminoso y reformado,MAD-001
Chalet en Las Rozas,650000,Las Rozas,Av. Iglesia 5,chalet,4,3,240,Con jardín,MAD-002
"""


def test_parses_comma_csv():
    result = parse_properties_csv(CSV_COMMA.encode("utf-8"))
    assert result.skipped == 0
    assert result.errors == []
    assert len(result.rows) == 2

    first = result.rows[0]
    assert first["title"] == "Piso en Malasaña"
    assert first["price"] == 325000
    assert first["city"] == "Madrid"
    assert first["address"] == "Calle Pez 10"
    assert first["property_type"] is PropertyType.APARTMENT
    assert first["bedrooms"] == 2
    assert first["bathrooms"] == 1
    assert first["area_sqm"] == 78
    assert first["reference"] == "MAD-001"

    second = result.rows[1]
    assert second["property_type"] is PropertyType.HOUSE
    assert second["price"] == 650000


CSV_SEMICOLON_ENGLISH = """\
Title;Price;City;Type;Bedrooms
Ático centro;450.000;Valencia;ático;3
Studio near beach;120000;Alicante;studio;
"""


def test_parses_semicolon_csv_with_english_headers():
    result = parse_properties_csv(CSV_SEMICOLON_ENGLISH.encode("utf-8-sig"))
    assert result.skipped == 0
    assert len(result.rows) == 2
    assert result.rows[0]["property_type"] is PropertyType.PENTHOUSE
    assert result.rows[0]["price"] == 450000
    assert result.rows[1]["property_type"] is PropertyType.STUDIO
    assert result.rows[1]["bedrooms"] is None


CSV_BAD_ROWS = """\
titulo,precio,ciudad
,300000,Madrid
Piso sin precio,,Madrid
Piso válido,200000,Madrid
"""


def test_rows_without_title_or_price_are_skipped_with_errors():
    result = parse_properties_csv(CSV_BAD_ROWS.encode("utf-8"))
    assert len(result.rows) == 1
    assert result.skipped == 2
    assert any("Fila 2" in e and "título" in e for e in result.errors)
    assert any("Fila 3" in e and "precio" in e.lower() for e in result.errors)


def test_missing_reference_gets_generated():
    result = parse_properties_csv(b"titulo,precio\nPiso,100000\n")
    assert result.rows[0]["reference"].startswith("IMP-")


def test_unrecognized_headers_report_error():
    result = parse_properties_csv(b"foo,bar\n1,2\n")
    assert result.rows == []
    assert result.errors and "cabeceras" in result.errors[0]


def test_empty_file_reports_error():
    result = parse_properties_csv(b"")
    assert result.rows == []
    assert result.errors


def test_url_column_lands_in_extra():
    result = parse_properties_csv(
        b"titulo,precio,url\nPiso,100000,https://example.com/p/1\n"
    )
    assert result.rows[0]["extra"]["source_url"] == "https://example.com/p/1"


def test_blank_lines_are_ignored():
    result = parse_properties_csv(b"titulo,precio\nPiso,100000\n,\n\n")
    assert len(result.rows) == 1
    assert result.skipped == 0
