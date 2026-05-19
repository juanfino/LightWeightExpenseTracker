import unicodedata


def normalize(text: str) -> str:
    """Lowercase + quitar acentos. 'Ñoño' → 'nono', 'Farmacia' → 'farmacia'"""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def categorize(concept: str, keywords: list) -> tuple[int | None, int | None]:
    """
    Recibe el concepto parseado y la lista de keywords de la DB
    (cada elemento debe tener 'keyword', 'category_id' y 'subcategory_id').
    Retorna (category_id, subcategory_id) del primer match, o (None, None) si no hay match.
    """
    normalized_concept = normalize(concept)
    for kw in keywords:
        if normalize(kw["keyword"]) in normalized_concept:
            return kw["category_id"], kw["subcategory_id"]
    return None, None


# ── Tests inline ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mock_keywords = [
        {"keyword": "supermercado", "category_id": 1, "subcategory_id": 10},
        {"keyword": "super",        "category_id": 1, "subcategory_id": 10},
        {"keyword": "farmacia",     "category_id": 3, "subcategory_id": 30},
        {"keyword": "nafta",        "category_id": 2, "subcategory_id": None},
        {"keyword": "ypf",          "category_id": 2, "subcategory_id": None},
        {"keyword": "netflix",      "category_id": 5, "subcategory_id": 50},
    ]

    cases = [
        ("Supermercado",       (1, 10)),
        ("Super Vea",          (1, 10)),
        ("Farmácia Central",   (3, 30)),    # acento en la á
        ("Nafta YPF",          (2, None)),  # matchea 'nafta' primero
        ("YPF",                (2, None)),
        ("NETFLIX",            (5, 50)),    # mayúsculas
        ("Cena cumpleaños",    (None, None)),
        ("Ropa interior",      (None, None)),
    ]

    passed = 0
    for concept, expected in cases:
        result = categorize(concept, mock_keywords)
        ok = result == expected
        status = "✅" if ok else "❌"
        print(f"{status} '{concept}' → {result}  (esperado: {expected})")
        if ok:
            passed += 1

    print(f"\n{passed}/{len(cases)} tests pasaron")
