"""Users, usage and limits. Supabase (Postgres over REST) in production, memory for local tests."""
import datetime as dt
import threading


def now():
    return dt.datetime.now(dt.timezone.utc)


def month_start():
    n = now()
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def day_start():
    return now().replace(hour=0, minute=0, second=0, microsecond=0)


class MemoryStore:
    """Not persistent. For local runs and tests only."""

    def __init__(self):
        self.lock = threading.Lock()
        self.users, self.reads, self._id = {}, [], 0

    def get_or_create_user(self, email):
        with self.lock:
            return self.users.setdefault(email, {"email": email, "blocked": False, "monthly_limit": None, "plan": "free", "created_at": now().isoformat()})

    def set_user(self, email, **fields):
        with self.lock:
            self.users[email].update(fields)

    def list_users(self):
        return list(self.users.values())

    def add_read(self, email):
        with self.lock:
            self._id += 1
            self.reads.append({"id": self._id, "email": email, "status": "pending", "at": now()})
            return self._id

    def finish_read(self, rid, status):
        with self.lock:
            for r in self.reads:
                if r["id"] == rid:
                    r["status"] = status

    def count_reads(self, since, email=None):
        return sum(1 for r in self.reads if r["at"] >= since and r["status"] != "failed" and (email is None or r["email"] == email))

    def last_read_at(self, email):
        t = [r["at"] for r in self.reads if r["email"] == email]
        return max(t) if t else None


class SupabaseStore:
    """Talks to Supabase's REST API with the service key. The key stays on the server."""

    def __init__(self, url, key):
        import requests

        self.r = requests
        self.base = url.rstrip("/") + "/rest/v1/"
        self.h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}

    def _req(self, method, table, params=None, json=None, prefer=None):
        h = dict(self.h)
        if prefer:
            h["Prefer"] = prefer
        resp = self.r.request(method, self.base + table, headers=h, params=params, json=json, timeout=15)
        resp.raise_for_status()
        return resp

    def get_or_create_user(self, email):
        rows = self._req("GET", "iso_users", {"email": "eq." + email, "select": "*"}).json()
        if rows:
            return rows[0]
        return self._req("POST", "iso_users", json={"email": email}, prefer="return=representation,resolution=ignore-duplicates").json()[0]

    def set_user(self, email, **fields):
        self._req("PATCH", "iso_users", {"email": "eq." + email}, json=fields)

    def list_users(self):
        return self._req("GET", "iso_users", {"select": "*", "order": "created_at.desc", "limit": "500"}).json()

    def add_read(self, email):
        return self._req("POST", "iso_reads", json={"email": email, "status": "pending"}, prefer="return=representation").json()[0]["id"]

    def finish_read(self, rid, status):
        self._req("PATCH", "iso_reads", {"id": "eq." + str(rid)}, json={"status": status})

    def count_reads(self, since, email=None):
        p = {"select": "id", "at": "gte." + since.isoformat(), "status": "neq.failed"}
        if email:
            p["email"] = "eq." + email
        h = dict(self.h, Prefer="count=exact", Range="0-0")
        resp = self.r.get(self.base + "iso_reads", headers=h, params=p, timeout=15)
        resp.raise_for_status()
        return int(resp.headers.get("Content-Range", "0/0").split("/")[-1])

    def last_read_at(self, email):
        rows = self._req("GET", "iso_reads", {"email": "eq." + email, "select": "at", "order": "at.desc", "limit": "1"}).json()
        return dt.datetime.fromisoformat(rows[0]["at"].replace("Z", "+00:00")) if rows else None


def check_allowed(store, email, *, default_monthly, app_daily, cooldown_s):
    """Return (ok, message, reads_left). Run this BEFORE any Claude call."""
    u = store.get_or_create_user(email)
    if u.get("blocked"):
        return False, "This account has been turned off. Contact the app owner.", 0
    limit = u.get("monthly_limit")
    limit = default_monthly if limit is None else int(limit)
    used = store.count_reads(month_start(), email)
    left = max(0, limit - used)
    if left <= 0:
        return False, "You have used all %d reads for this month." % limit, 0
    if store.count_reads(day_start()) >= app_daily:
        return False, "The app has reached its daily limit. Try again tomorrow.", left
    last = store.last_read_at(email)
    if last and (now() - last).total_seconds() < cooldown_s:
        wait = int(cooldown_s - (now() - last).total_seconds()) + 1
        return False, "Please wait %d seconds before the next read." % wait, left
    return True, "", left
