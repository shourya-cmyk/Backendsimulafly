import pytest

from app.utils.slug import slugify_base, make_unique_slug


def test_slugify_base_simple():
    assert slugify_base("Acme Furniture Co.") == "acme-furniture-co"


def test_slugify_base_strips_unicode_and_special_chars():
    assert slugify_base("Café Déco — 北京") == "cafe-deco"


def test_slugify_base_collapses_whitespace_and_dashes():
    assert slugify_base("  Foo   --  Bar  ") == "foo-bar"


def test_make_unique_slug_appends_random_suffix_when_collision():
    existing = {"acme-furniture-co"}
    result = make_unique_slug("Acme Furniture Co.", lambda s: s in existing)
    # Must NOT equal the colliding base; must START with the base + dash.
    assert result != "acme-furniture-co"
    assert result.startswith("acme-furniture-co-")
    # Suffix is 6 lowercase alphanumerics.
    suffix = result[len("acme-furniture-co-"):]
    assert len(suffix) == 6
    assert suffix.isalnum() and suffix.islower()
