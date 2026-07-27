"""Exercise every non-parameterized GET route against a migrated scratch DB."""
import dashboard
from support import authenticated_client


def main():
    client, _csrf_headers = authenticated_client(dashboard)
    failures = []
    skipped = {"/static/<path:filename>", "/resumenes/<period>"}
    routes = sorted(
        {
            rule.rule
            for rule in dashboard.app.url_map.iter_rules()
            if "GET" in rule.methods and rule.rule not in skipped and "<" not in rule.rule
        }
    )
    for route in routes:
        response = client.get(route)
        print(route, response.status_code)
        if response.status_code >= 500:
            failures.append((route, response.status_code))
    if failures:
        raise AssertionError(f"GET route failures: {failures}")
    print(f"postgres_web_smoke_ok routes={len(routes)}")


if __name__ == "__main__":
    main()
