"""Read-only PostgreSQL overview for the postgres-pane mod. Standard library only.

Usage: python3 pgview.py <folder> [database] [schema table]
Finds .env in <folder> or a parent (up to the git root or $HOME), connects with
OCCOS_DATABASE_URL, else POSTGRES_PASSWORD (+ POSTGRES_USER/DB/HOST/PORT), and
prints JSON: databases, tables and views with columns and row counts, or, given
a schema and table, its last ROW_LIMIT rows (by primary key, else physical order).
The password is never printed.
"""
import base64, hashlib, hmac, json, os, socket, ssl, struct, sys
from urllib.parse import urlsplit, unquote, parse_qs

COUNT_LIMIT = 200          # more relations than this: estimates only
COUNT_TIMEOUT_MS = 2000    # per-table count(*) budget
ROW_LIMIT = 100
CELL_CHARS = 300           # longer values are cut


def find_env(folder):
    """.env in the folder, else in each parent up to the git root (or $HOME), else one folder down."""
    home = os.path.expanduser("~").rstrip("/")
    start = d = os.path.abspath(folder)
    while True:
        path = os.path.join(d, ".env")
        if os.path.isfile(path):
            return path
        if d in (home, "/") or os.path.isdir(os.path.join(d, ".git")):
            break
        d = os.path.dirname(d)
    try:
        subs = sorted(e.path for e in os.scandir(start) if e.is_dir() and not e.name.startswith("."))
    except OSError:
        return None
    for sub in subs:
        path = os.path.join(sub, ".env")
        if os.path.isfile(path) and settings(read_env(path)):
            return path
    return None


def read_env(path):
    env = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            elif " #" in v:
                v = v.split(" #", 1)[0].rstrip()
            env[k] = v
    return env


def settings(env):
    url = env.get("OCCOS_DATABASE_URL")
    if url:
        u = urlsplit(url)
        q = parse_qs(u.query)
        return dict(source="OCCOS_DATABASE_URL", user=unquote(u.username or "postgres"),
                    password=unquote(u.password or ""), host=u.hostname or "localhost",
                    port=u.port or 5432, db=unquote(u.path.lstrip("/")) or unquote(u.username or "postgres"),
                    sslmode=q.get("sslmode", ["prefer"])[0])
    if env.get("POSTGRES_PASSWORD"):
        user = env.get("POSTGRES_USER") or "postgres"
        return dict(source="POSTGRES_PASSWORD", user=user, password=env["POSTGRES_PASSWORD"],
                    host=env.get("POSTGRES_HOST") or "localhost", port=int(env.get("POSTGRES_PORT") or 5432),
                    db=env.get("POSTGRES_DB") or user, sslmode="prefer")
    return None


class PgError(Exception):
    pass


class Conn:
    def __init__(self, s, db):
        sock = socket.create_connection((s["host"], s["port"]), timeout=5)
        if s["sslmode"] != "disable":
            sock.sendall(struct.pack("!ii", 8, 80877103))
            if sock.recv(1) == b"S":
                # Same as libpq: only verify-ca / verify-full check the certificate.
                ctx = ssl.create_default_context()
                ctx.check_hostname = s["sslmode"] == "verify-full"
                if not s["sslmode"].startswith("verify-"):
                    ctx.verify_mode = ssl.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=s["host"])
            elif s["sslmode"] in ("require", "verify-ca", "verify-full"):
                raise PgError("server does not support SSL")
        self.sock = sock
        self.buf = b""
        params = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in
                          (("user", s["user"]), ("database", db), ("application_name", "herdr-postgres-pane"))) + b"\0"
        body = struct.pack("!i", 196608) + params
        sock.sendall(struct.pack("!i", len(body) + 4) + body)
        self.auth(s["user"], s["password"])

    def recv(self):
        while len(self.buf) < 5 or len(self.buf) < 1 + struct.unpack("!i", self.buf[1:5])[0]:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise PgError("connection closed by server")
            self.buf += chunk
        n = struct.unpack("!i", self.buf[1:5])[0]
        t, data, self.buf = self.buf[:1], self.buf[5:1 + n], self.buf[1 + n:]
        if t == b"E":
            fields = {f[:1]: f[1:].decode(errors="replace") for f in data.split(b"\0") if f}
            raise PgError(fields.get(b"M", "error"))
        return t, data

    def send(self, t, data):
        self.sock.sendall(t + struct.pack("!i", len(data) + 4) + data)

    def auth(self, user, password):
        while True:
            t, data = self.recv()
            if t == b"Z":
                return
            if t != b"R":
                continue
            code = struct.unpack("!i", data[:4])[0]
            if code == 3:
                self.send(b"p", password.encode() + b"\0")
            elif code == 5:
                inner = hashlib.md5(password.encode() + user.encode()).hexdigest().encode()
                self.send(b"p", b"md5" + hashlib.md5(inner + data[4:8]).hexdigest().encode() + b"\0")
            elif code == 10:
                self.scram(password)
            elif code not in (0, 12):
                raise PgError(f"unsupported authentication method {code}")

    def scram(self, password):
        nonce = base64.b64encode(os.urandom(18)).decode()
        first_bare = f"n=,r={nonce}"
        msg = ("n,," + first_bare).encode()
        self.send(b"p", b"SCRAM-SHA-256\0" + struct.pack("!i", len(msg)) + msg)
        t, data = self.recv()
        server_first = data[4:].decode()
        a = dict(p.split("=", 1) for p in server_first.split(","))
        if not a["r"].startswith(nonce):
            raise PgError("SCRAM nonce mismatch")
        salted = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(a["s"]), int(a["i"]))
        client_key = hmac.digest(salted, b"Client Key", "sha256")
        final_bare = f"c=biws,r={a['r']}"
        auth_msg = f"{first_bare},{server_first},{final_bare}".encode()
        sig = hmac.digest(hashlib.sha256(client_key).digest(), auth_msg, "sha256")
        proof = base64.b64encode(bytes(x ^ y for x, y in zip(client_key, sig))).decode()
        self.send(b"p", f"{final_bare},p={proof}".encode())
        t, data = self.recv()
        server_key = hmac.digest(salted, b"Server Key", "sha256")
        expected = base64.b64encode(hmac.digest(server_key, auth_msg, "sha256")).decode()
        if data[4:].decode() != f"v={expected}":
            raise PgError("SCRAM server signature mismatch")

    def query(self, sql):
        """Simple query; returns rows as lists of str/None. Raises PgError after ReadyForQuery."""
        self.send(b"Q", sql.encode() + b"\0")
        rows, err = [], None
        while True:
            try:
                t, data = self.recv()
            except PgError as e:
                if "closed" in str(e):
                    raise
                err = e
                continue
            if t == b"D":
                n, pos, row = struct.unpack("!h", data[:2])[0], 2, []
                for _ in range(n):
                    ln = struct.unpack("!i", data[pos:pos + 4])[0]
                    pos += 4
                    row.append(None if ln < 0 else data[pos:pos + ln].decode(errors="replace"))
                    pos += max(ln, 0)
                rows.append(row)
            elif t == b"Z":
                if err:
                    raise err
                return rows

    def close(self):
        try:
            self.send(b"X", b"")
        finally:
            self.sock.close()


RELATIONS = """
select n.nspname, c.relname, c.relkind, c.reltuples::bigint
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.relkind in ('r','p','v','m') and n.nspname not in ('pg_catalog','information_schema')
  and n.nspname not like 'pg_toast%' and not c.relispartition
order by n.nspname = 'public' desc, n.nspname, c.relname"""

COLUMNS = """
select n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
       exists (select 1 from pg_index i where i.indrelid = c.oid and i.indisprimary and a.attnum = any(i.indkey))
from pg_attribute a join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace
where a.attnum > 0 and not a.attisdropped and c.relkind in ('r','p','v','m')
  and n.nspname not in ('pg_catalog','information_schema') and n.nspname not like 'pg_toast%'
order by n.nspname, c.relname, a.attnum"""


def ident(name):
    return '"' + name.replace('"', '""') + '"'


def overview(s, db):
    c = Conn(s, db)
    try:
        c.query("set default_transaction_read_only = on")
        dbs = [r[0] for r in c.query("select datname from pg_database where not datistemplate and datallowconn order by 1")]
        rels = {}
        for schema, name, kind, est in c.query(RELATIONS):
            rels[(schema, name)] = dict(schema=schema, name=name, view=kind in "vm",
                                        rows=None if kind == "v" or int(est) < 0 else int(est), exact=False, columns=[])
        for schema, name, col, typ, notnull, pk in c.query(COLUMNS):
            r = rels.get((schema, name))
            if r:
                r["columns"].append(dict(name=col, type=typ, notnull=notnull == "t", pk=pk == "t"))
        countable = [r for r in rels.values() if r["rows"] is not None or not r["view"]]
        if len(countable) <= COUNT_LIMIT:
            c.query(f"set statement_timeout = {COUNT_TIMEOUT_MS}")
            for r in countable:
                try:
                    r["rows"] = int(c.query(f"select count(*) from {ident(r['schema'])}.{ident(r['name'])}")[0][0])
                    r["exact"] = True
                except PgError:
                    pass
        return dbs, list(rels.values())
    finally:
        c.close()


def last_rows(s, db, schema, table):
    c = Conn(s, db)
    try:
        c.query("set default_transaction_read_only = on")
        c.query("set statement_timeout = 15000")
        rel = f"{ident(schema)}.{ident(table)}"
        kind = c.query(f"select c.relkind from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                       f"where n.nspname = {literal(schema)} and c.relname = {literal(table)}")
        if not kind:
            raise PgError(f"{schema}.{table} not found")
        pk = [r[0] for r in c.query(
            f"select a.attname from pg_index i join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey) "
            f"where i.indrelid = {literal(rel)}::regclass and i.indisprimary order by array_position(i.indkey, a.attnum)")]
        if pk:
            order = "primary key"
            by = " order by " + ", ".join(f"{ident(k)} desc" for k in pk)
        elif kind[0][0] in "rpm":
            order, by = "physical order", " order by ctid desc"
        else:
            order, by = "unordered", ""
        cols = [r[0] for r in c.query(
            f"select attname from pg_attribute where attrelid = {literal(rel)}::regclass and attnum > 0 and not attisdropped order by attnum")]
        sel = ", ".join(f"left({ident(k)}::text, {CELL_CHARS + 1})" for k in cols) or "1"
        rows = c.query(f"select {sel} from {rel}{by} limit {ROW_LIMIT}")
        return dict(columns=cols, rows=rows, order=order, pk=pk, limit=ROW_LIMIT)
    finally:
        c.close()


def literal(text):
    return "'" + text.replace("'", "''") + "'"


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    env_path = find_env(folder)
    if not env_path:
        return dict(status="none")
    s = settings(read_env(env_path))
    if not s:
        return dict(status="none", env=env_path)
    db = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else s["db"]
    out = dict(env=env_path, source=s["source"], user=s["user"], host=s["host"], port=s["port"], db=db, default_db=s["db"])
    try:
        if len(sys.argv) > 4:
            out.update(last_rows(s, db, sys.argv[3], sys.argv[4]))
        else:
            out["databases"], out["relations"] = overview(s, db)
        out["status"] = "ok"
    except (OSError, PgError, ValueError) as e:
        out.update(status="error", error=str(e) or e.__class__.__name__)
    return out


if __name__ == "__main__":
    print(json.dumps(main()))
