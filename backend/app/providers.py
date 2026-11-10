"""Bounded provider adapters. No retry of billable requests; no personal email requests."""

import base64

import httpx


class ProviderError(Exception):
    def __init__(self, provider, code, uncertain=False):
        self.provider, self.code, self.uncertain = provider, str(code), uncertain
        super().__init__(f"{provider}: {code}")


def request(provider, method, url, *, api_key, params=None, payload=None):
    headers = {"Accept": "application/json"}
    headers["Authorization" if provider in {"fullenrich", "apify"} else "X-API-Key"] = (
        "Bearer " if provider in {"fullenrich", "apify"} else ""
    ) + api_key
    if provider == "lemlist":
        headers.pop("X-API-Key", None)
        headers["Authorization"] = "Basic " + base64.b64encode((":" + api_key).encode()).decode()
    try:
        with httpx.Client(timeout=httpx.Timeout(25, connect=8), follow_redirects=False) as client:
            response = client.request(method, url, headers=headers, params=params, json=payload)
        if response.status_code >= 400:
            if provider == "lemlist" and response.status_code == 402:
                raise ProviderError(provider, "API indisponible sur le forfait actuel (HTTP 402)")
            raise ProviderError(
                provider, f"HTTP {response.status_code}", uncertain=response.status_code >= 500
            )
        if response.status_code >= 300:
            raise ProviderError(provider, "Redirect refused")
        if len(response.content) > 5_000_000:
            raise ProviderError(provider, "Response too large", uncertain=True)
        result = response.json()
        if provider == "hunter" and response.status_code == 202:
            return {"_pending": True}
        return result
    except httpx.RequestError:
        # Do not include URLs, API keys, response bodies or contact data in errors.
        raise ProviderError(provider, "Network failure; reconcile before retry", uncertain=True) from None
    except ValueError:
        raise ProviderError(provider, "Invalid JSON response", uncertain=True) from None


def verify_connection(provider, config):
    if provider == "apify":
        from .acquisition import check

        return check(config)
    if provider == "hunter":
        result = request(provider, "GET", "https://api.hunter.io/v2/account", api_key=config["api_key"])
        data = result.get("data", {})
        # Do not persist account emails, names, API metadata or other returned fields.
        requests = {
            kind: {k: value.get(k) for k in ("used", "available", "remaining")}
            for kind, value in data.get("requests", {}).items()
            if kind in {"credits", "searches", "verifications"} and isinstance(value, dict)
        }
        return {
            "authenticated": bool(data),
            "quota": {
                "plan_name": data.get("plan_name"),
                "reset_date": data.get("reset_date"),
                "requests": requests,
            },
        }
    if provider == "fullenrich":
        request(
            provider,
            "GET",
            "https://app.fullenrich.com/api/v2/account/keys/verify",
            api_key=config["api_key"],
        )
        balance = request(
            provider,
            "GET",
            "https://app.fullenrich.com/api/v2/account/credits",
            api_key=config["api_key"],
        )
        return {"authenticated": True, "quota": {"credits": balance.get("balance")}}
    if provider == "apollo":
        result = request(
            provider, "GET", "https://api.apollo.io/api/v1/auth/health", api_key=config["api_key"]
        )
        result = {"authenticated": result.get("healthy") is True and result.get("is_logged_in") is True}
        if result["authenticated"]:
            try:
                balance = request(
                    provider,
                    "POST",
                    "https://api.apollo.io/api/v1/usage_stats/credit_usage_stats",
                    api_key=config["api_key"],
                )
                stats = balance.get("credit_usage_stats", {})
                result["quota"] = {
                    "credit_usage_stats": {
                        kind: {k: stats[kind].get(k) for k in ("limit", "consumed", "left_over")}
                        for kind in ("lead_credit", "direct_dial_credit", "export_credit")
                        if isinstance(stats.get(kind), dict)
                    },
                    "current_credit_cycle": {
                        k: balance.get("current_credit_cycle", {}).get(k) for k in ("start_date", "end_date")
                    },
                }
            except ProviderError:
                # Some scoped keys can authenticate but cannot read account usage.
                result["quota_unavailable"] = True
            try:
                # This documented endpoint costs zero credits. Probe one row for
                # a reserved domain and discard all returned contact fields.
                request(
                    provider,
                    "POST",
                    "https://api.apollo.io/api/v1/mixed_people/api_search",
                    api_key=config["api_key"],
                    params={"page": 1, "per_page": 1, "q_organization_domains_list[]": "example.com"},
                )
                result["capabilities"] = {"people_search": True}
            except ProviderError as exc:
                result["capabilities"] = {"people_search": False}
                result["limitation"] = "Recherche API indisponible pour ce compte (" + exc.code + ")."
        return result
    if provider == "lemlist":
        result = request(
            provider, "GET", "https://api.lemlist.com/api/team/credits", api_key=config["api_key"]
        )
        remaining = result.get("details", {}).get("remaining", {})
        return {
            "authenticated": isinstance(result.get("credits"), (int, float)),
            "quota": {
                "credits": result.get("credits"),
                "remaining": {
                    k: remaining.get(k) for k in ("total", "freemium", "subscription", "gifted", "paid")
                },
            },
        }
    raise ProviderError(provider, "Unsupported provider")


def apollo_search(config, kind, filters, page, limit):
    allowed = {
        "person_titles",
        "person_locations",
        "person_seniorities",
        "organization_locations",
        "q_organization_domains_list",
        "organization_ids",
        "organization_num_employees_ranges",
        "currently_using_all_of_technology_uids",
        "q_keywords",
        "q_organization_name",
    }
    if set(filters) - allowed:
        raise ProviderError("apollo", "Unsupported search filter")
    params = [("page", page), ("per_page", limit)]
    for key, value in filters.items():
        if isinstance(value, list):
            params.extend((key + "[]", item) for item in value)
        else:
            params.append((key, value))
    endpoint = "mixed_people/api_search" if kind == "people" else "mixed_companies/search"
    return request(
        "apollo", "POST", "https://api.apollo.io/api/v1/" + endpoint, api_key=config["api_key"], params=params
    )


def enrich(provider, config, identity, fields, job_id):
    if provider == "hunter":
        if fields != ["email"] or not identity.get("email"):
            raise ProviderError(provider, "Hunter requires an existing professional email to verify")
        result = request(
            provider,
            "GET",
            "https://api.hunter.io/v2/email-verifier",
            api_key=config["api_key"],
            params={"email": identity["email"]},
        )
        if result.get("_pending"):
            return {"complete": False}
        value = result.get("data") or {}
        status = value.get("status") or value.get("result")
        return {
            "complete": True,
            "email": identity["email"],
            "email_status": "valid"
            if status in {"valid", "deliverable"}
            else "invalid"
            if status in {"invalid", "undeliverable"}
            else "risky",
            "provider_status": status,
        }
    if provider == "apollo":
        if "phone" in fields:
            raise ProviderError(provider, "Use FullEnrich for async phone enrichment in this version")
        params = {
            k: identity[k] for k in ["first_name", "last_name", "domain", "linkedin_url"] if identity.get(k)
        }
        params.update(reveal_personal_emails="false", reveal_phone_number="false")
        result = request(
            provider,
            "POST",
            "https://api.apollo.io/api/v1/people/match",
            api_key=config["api_key"],
            params=params,
        )
        person = result.get("person") or {}
        # Partial or ambiguous matches require review; never silently overwrite the identity.
        confidence = result.get("match_confidence") or person.get("match_confidence")
        return {
            "complete": True,
            "email": person.get("email"),
            "email_status": "valid" if person.get("email_status") == "verified" else "unknown",
            "match_confidence": confidence,
            "review_required": confidence != "high",
            "provider_person_id": person.get("id"),
        }
    if provider == "fullenrich":
        entry = {
            k: identity[k]
            for k in ["first_name", "last_name", "domain", "company_name", "linkedin_url"]
            if identity.get(k)
        }
        entry["enrich_fields"] = ["contact.work_emails" if f == "email" else "contact.phones" for f in fields]
        entry["custom"] = {"job_id": job_id}
        result = request(
            provider,
            "POST",
            "https://app.fullenrich.com/api/v2/contact/enrich/bulk",
            api_key=config["api_key"],
            payload={"name": "Lumelia " + job_id, "data": [entry]},
        )
        external_id = result.get("enrichment_id")
        if not external_id:
            raise ProviderError(provider, "Missing enrichment identifier", uncertain=True)
        return {"complete": False, "external_id": external_id}
    raise ProviderError(provider, "Unsupported provider")


def poll_fullenrich(config, external_id, job_id):
    from urllib.parse import quote

    result = request(
        "fullenrich",
        "GET",
        "https://app.fullenrich.com/api/v2/contact/enrich/bulk/" + quote(external_id, safe=""),
        api_key=config["api_key"],
    )
    if result.get("status") != "FINISHED":
        if result.get("status") in {"FAILED", "CANCELED", "CANCELLED"}:
            raise ProviderError("fullenrich", "Enrichment terminated")
        return {"complete": False, "external_id": external_id}
    rows = [x for x in result.get("data", []) if x.get("custom", {}).get("job_id") == job_id]
    if len(rows) != 1:
        raise ProviderError("fullenrich", "Response correlation mismatch", uncertain=True)
    info = rows[0].get("contact_info") or {}
    email, phone = info.get("most_probable_work_email") or {}, info.get("most_probable_phone") or {}
    return {
        "complete": True,
        "email": email.get("email"),
        "email_status": "valid" if email.get("status") == "DELIVERABLE" else "unknown",
        "phone": phone.get("number"),
        "phone_type": "unknown",
        "phone_status": "unknown",
        "phone_evidence": {k: phone.get(k) for k in ["line_type", "line_status", "ownership_match"]},
        "credits": result.get("cost", {}).get("credits"),
        "external_id": external_id,
    }
