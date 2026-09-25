#!/usr/bin/env python3
"""PostgREST de mentira para probar el pipeline sin Supabase.

    MOCK_DB_URL="postgresql://..." MOCK_PORT=8899 python tests/mock_postgrest.py

Rutas: GET /rest/v1/torrents?select=&limit=  |  POST /rest/v1/rpc/<fn>
Cada RPC se ejecuta contra Postgres introspeccionando su firma (notación
nombrada + cast, igual que PostgREST real; los args que faltan usan defaults).
"""
import datetime
import decimal
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

DSN = os.environ.get("MOCK_DB_URL", "")
PORT = int(os.environ.get("MOCK_PORT", "8899"))
HOST = os.environ.get("MOCK_HOST", "127.0.0.1")

if not DSN:
    print("ERROR: falta MOCK_DB_URL")
    sys.exit(2)


def pg():
    return psycopg.connect(DSN, autocommit=True, row_factory=dict_row)


def jdefault(o):
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, (bytes, bytearray)):
        return bytes(o).decode("utf-8", "replace")
    return str(o)


def adapt(v):
    if isinstance(v, dict):
        return Json(v)
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return Json(v)
    return v


class H(BaseHTTPRequestHandler):
    server_version = "MockPostgREST/1.6"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"mock {self.command} {self.path.split('?')[0]}\n")

    def _send(self, code, obj):
        body = json.dumps(obj, default=jdefault).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path != "/rest/v1/torrents":
            return self._send(404, {"message": "not found"})
        qs = parse_qs(u.query)
        select = qs.get("select", ["*"])[0]
        try:
            limit = max(0, min(int(qs.get("limit", ["100"])[0]), 5000))
        except ValueError:
            limit = 100
        where, params = [], []
        for k, vs in qs.items():
            if k in ("select", "limit", "order", "offset"):
                continue
            for v in vs:
                if v.startswith("eq."):
                    where.append(f'"{k}" = %s')
                    params.append(v[3:])
                elif v == "is.null":
                    where.append(f'"{k}" is null')
        cols = "*"
        if select != "*":
            cols = ", ".join(f'"{c.strip()}"' for c in select.split(",")
                             if c.strip()) or "*"
        sql = f"select {cols} from public.torrents"
        if where:
            sql += " where " + " and ".join(where)
        sql += " order by id limit %s"
        params.append(limit)
        try:
            with pg() as c:
                rows = c.execute(sql, params).fetchall()
            self._send(200, rows)
        except Exception as e:  # noqa: BLE001
            self._send(400, {"message": str(e)[:300]})

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if not u.path.startswith("/rest/v1/rpc/"):
            return self._send(404, {"message": "not found"})
        fn = u.path.rsplit("/", 1)[-1]
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)
                                      or 0))
            body = json.loads(raw or b"{}")
        except (ValueError, OSError):
            body = {}
        if not isinstance(body, dict):
            return self._send(400, {"message": "body must be a JSON object"})
        try:
            with pg() as c:
                f = c.execute(
                    "select proargnames, "
                    "oidvectortypes(proargtypes) as t from pg_proc "
                    "where pronamespace = 'public'::regnamespace "
                    "and proname = %s order by pronargs desc limit 1",
                    [fn]).fetchone()
                if not f:
                    return self._send(404, {
                        "message": f"Could not find the function public.{fn}"
                                   " in the schema cache"})
                names = f["proargnames"] or []
                types = f["t"].split(", ") if f["t"] else []
                args, params = [], []
                for i, nm in enumerate(names):
                    if nm in body and body[nm] is not None:
                        cast = f"::{types[i]}" if i < len(types) else ""
                        args.append(f'"{nm}" := %s{cast}')
                        params.append(adapt(body[nm]))
                sql = (f'select * from public."{fn}"({", ".join(args)})'
                       if args else f'select * from public."{fn}"()')
                rows = c.execute(sql, params).fetchall()
            self._send(200, rows)
        except Exception as e:  # noqa: BLE001
            self._send(400, {"message": str(e)[:500]})


if __name__ == "__main__":
    with pg() as c:
        c.execute("select 1")
    print(f"mock_postgrest en http://{HOST}:{PORT} "
          f"(db: {DSN.split('@')[-1][:60]}...)", flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
