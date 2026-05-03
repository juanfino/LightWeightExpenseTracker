import unicodedata


def normalize(text: str) -> str:
    """Lowercase + quitar acentos. 'Ñoño' → 'nono', 'Farmacia' → 'farmacia'"""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def categorize(concept: str, keywords: list) -> int | None:
    """
    Recibe el concepto parseado y la lista de keywords de la DB
    (cada elemento debe tener 'keyword' y 'category_id').
    Retorna category_id del primer match, o None si no hay match.
    """
    normalized_concept = normalize(concept)
    for kw in keywords:
        if normalize(kw["keyword"]) in normalized_concept:
            return kw["category_id"]
    return None


# ── Tests inline ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mock_keywords = [
        {"keyword": "supermercado", "category_id": 1},
        {"keyword": "super",        "category_id": 1},
        {"keyword": "farmacia",     "category_id": 3},
        {"keyword": "nafta",        "category_id": 2},
        {"keyword": "ypf",          "category_id": 2},
        {"keyword": "netflix",      "category_id": 5},
    ]

    cases = [
        ("Supermercado",       1),
        ("Super Vea",          1),
        ("Farmácia Central",   3),   # acento en la á
        ("Nafta YPF",          2),   # matchea 'nafta' primero
        ("YPF",                2),
        ("NETFLIX",            5),   # mayúsculas
        ("Cena cumpleaños",    None),
        ("Ropa interior",      None),
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
