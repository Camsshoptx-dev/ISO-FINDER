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
        self.team = []  # (owner, member)

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

    def finish_read(self, rid, status, tokens_in=None, tokens_out=None, cost=None):
        with self.lock:
            for r in self.reads:
                if r["id"] == rid:
                    r["status"] = status
                    r["tokens_in"], r["tokens_out"], r["cost"] = tokens_in, tokens_out, cost

    def sum_cost(self, since, email=None):
        return sum((r.get("cost") or 0) for r in self.reads if r["at"] >= since and r["status"] == "ok" and (email is None or r["email"] == email))

    def count_costed(self, since):
        return sum(1 for r in self.reads if r["at"] >= since and r["status"] == "ok" and r.get("cost") is not None)

    def count_reads(self, since, email=None, emails=None):
        want = set(emails) if emails else ({email} if email else None)
        return sum(1 for r in self.reads if r["at"] >= since and r["status"] != "failed" and (want is None or r["email"] in want))

    def list_members(self, owner):
        return [m for o, m in self.team if o == owner]

    def add_member(self, owner, member):
        with self.lock:
            if (owner, member) not in self.team:
                self.team.append((owner, member))

    def remove_member(self, owner, member):
        with self.lock:
            self.team = [(o, m) for o, m in self.team if (o, m) != (owner, member)]

    def owner_of(self, member):
        for o, m in self.team:
            if m == member:
                return o
        return None

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

    def finish_read(self, rid, status, tokens_in=None, tokens_out=None, cost=None):
        body = {"status": status}
        if cost is not None:
            body.update({"input_tokens": tokens_in, "output_tokens": tokens_out, "cost_usd": round(cost, 5)})
        try:
            self._req("PATCH", "iso_reads", {"id": "eq." + str(rid)}, json=body)
        except Exception:  # cost columns not added yet (setup.sql block not run): still save the status
            if len(body) > 1:
                self._req("PATCH", "iso_reads", {"id": "eq." + str(rid)}, json={"status": status})
            else:
                raise

    def _costs(self, since, email=None):
        p = {"select": "cost_usd", "at": "gte." + since.isoformat(), "status": "eq.ok", "cost_usd": "not.is.null", "limit": "10000"}
        if email:
            p["email"] = "eq." + email
        try:
            return [float(r["cost_usd"]) for r in self._req("GET", "iso_reads", p).json()]
        except Exception:
            return []

    def sum_cost(self, since, email=None):
        return sum(self._costs(since, email))

    def count_costed(self, since):
        return len(self._costs(since))

    def list_members(self, owner):
        rows = self._req("GET", "iso_team", {"owner_email": "eq." + owner, "select": "member_email"}).json()
        return [r["member_email"] for r in rows]

    def add_member(self, owner, member):
        self._req("POST", "iso_team", json={"owner_email": owner, "member_email": member}, prefer="resolution=ignore-duplicates")

    def remove_member(self, owner, member):
        self._req("DELETE", "iso_team", {"owner_email": "eq." + owner, "member_email": "eq." + member})

    def owner_of(self, member):
        rows = self._req("GET", "iso_team", {"member_email": "eq." + member, "select": "owner_email", "limit": "1"}).json()
        return rows[0]["owner_email"] if rows else None

    def count_reads(self, since, email=None, emails=None):
        p = {"select": "id", "at": "gte." + since.isoformat(), "status": "neq.failed"}
        if emails:
            p["email"] = "in.(" + ",".join('"%s"' % e for e in emails) + ")"
        elif email:
            p["email"] = "eq." + email
        h = dict(self.h, Prefer="count=exact", Range="0-0")
        resp = self.r.get(self.base + "iso_reads", headers=h, params=p, timeout=15)
        resp.raise_for_status()
        return int(resp.headers.get("Content-Range", "0/0").split("/")[-1])

    def last_read_at(self, email):
        rows = self._req("GET", "iso_reads", {"email": "eq." + email, "select": "at", "order": "at.desc", "limit": "1"}).json()
        return dt.datetime.fromisoformat(rows[0]["at"].replace("Z", "+00:00")) if rows else None


def check_allowed(store, email, *, default_monthly, app_daily, cooldown_s, limit=None, group=None):
    """Return (ok, message, reads_left). Run this BEFORE any Claude call.

    limit: reads per month for this person's plan (None = their own override or the free default).
    group: emails that share the pool (a team). None = just this person.
    """
    u = store.get_or_create_user(email)
    if u.get("blocked"):
        return False, "This account has been turned off. Contact the app owner.", 0
    if limit is None:
        limit = u.get("monthly_limit")
        limit = default_monthly if limit is None else int(limit)
    used = store.count_reads(month_start(), emails=group) if group else store.count_reads(month_start(), email)
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
