"""Stripe subscriptions without a webhook server.

The app asks Stripe "does this email have an active subscription?" when someone signs in
(and caches the answer for a few minutes), so Streamlit does not need to receive webhooks.
Talks to Stripe's REST API with `requests`; the secret key never leaves the server.
"""
import requests

# plan name -> reads per month for the whole account/team, and seats
PLANS = {
    "individual": {"label": "Individual", "reads": 40, "seats": 1, "price": "$19/mo"},
    "team10": {"label": "Team of 10", "reads": 300, "seats": 10, "price": "$99/mo"},
    "team20": {"label": "Team of 20", "reads": 600, "seats": 20, "price": "$179/mo"},
}
API = "https://api.stripe.com/v1/"


class Billing:
    def __init__(self, secret_key, price_ids, http=requests):
        """price_ids: {"individual": "price_...", "team10": "price_...", "team20": "price_..."}"""
        self.key, self.http = secret_key, http
        self.by_price = {v: k for k, v in price_ids.items() if v}
        self.price_ids = price_ids

    def _get(self, path, **params):
        r = self.http.get(API + path, params=params, auth=(self.key, ""), timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        r = self.http.post(API + path, data=data, auth=(self.key, ""), timeout=15)
        r.raise_for_status()
        return r.json()

    def customers(self, email):
        return self._get("customers", email=email, limit=5).get("data", [])

    def active_plan(self, email):
        """Plan name if this email has an active or trialing subscription, else None."""
        for c in self.customers(email):
            for sub in self._get("subscriptions", customer=c["id"], status="all", limit=10).get("data", []):
                if sub.get("status") in ("active", "trialing"):
                    for it in sub.get("items", {}).get("data", []):
                        plan = self.by_price.get(it.get("price", {}).get("id"))
                        if plan:
                            return plan
        return None

    def checkout_url(self, email, plan, back_url):
        data = {
            "mode": "subscription",
            "customer_email": email,
            "line_items[0][price]": self.price_ids[plan],
            "line_items[0][quantity]": 1,
            "success_url": back_url + ("&" if "?" in back_url else "?") + "paid=1",
            "cancel_url": back_url,
            "allow_promotion_codes": "true",
        }
        return self._post("checkout/sessions", data)["url"]

    def portal_url(self, email, back_url):
        cs = self.customers(email)
        if not cs:
            return None
        return self._post("billing_portal/sessions", {"customer": cs[0]["id"], "return_url": back_url})["url"]


def entitlement(store, billing, email, default_free):
    """What this person gets this month: {"plan", "limit", "group", "role", "seats"}.

    - Own paid plan: individual reads, or a team pool shared with the members they added.
    - Added to someone's team: that team's pool, if the owner's subscription is active.
    - Otherwise: the free allowance.
    """
    free = {"plan": "free", "limit": default_free, "group": None, "role": None, "seats": 0}
    if billing is None:
        return free
    try:
        own = billing.active_plan(email)
        if own:
            p = PLANS[own]
            if p["seats"] > 1:
                return {"plan": own, "limit": p["reads"], "group": [email] + store.list_members(email), "role": "owner", "seats": p["seats"]}
            return {"plan": own, "limit": p["reads"], "group": None, "role": "owner", "seats": 1}
        boss = store.owner_of(email)
        if boss:
            bp = billing.active_plan(boss)
            if bp and PLANS[bp]["seats"] > 1:
                return {"plan": bp, "limit": PLANS[bp]["reads"], "group": [boss] + store.list_members(boss), "role": "member", "seats": PLANS[bp]["seats"], "boss": boss}
    except Exception:
        pass  # Stripe unreachable: fall back to free rather than locking everyone out
    return free
