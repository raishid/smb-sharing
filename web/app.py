#!/usr/bin/env python3
"""Panel web interno: colas en vivo e historial de impresiones del print server.

Fuente de verdad del historial: el historial de trabajos de CUPS via IPP
(Get-Jobs which-jobs=completed), que se vuelca a SQLite para que sobreviva a
reinicios y a la rotacion/purga que hace el propio cupsd (MaxJobs).

El page_log se lee como complemento: con colas raw (-m raw) CUPS no interpreta
el documento, asi que el conteo de paginas casi siempre es aproximado.
"""

import csv
import io
import os
import re
import sqlite3
import threading
import time

import cups
from flask import Flask, g, jsonify, render_template, request, Response

CUPS_HOST = os.environ.get("CUPS_HOST", "localhost")
CUPS_PORT = int(os.environ.get("CUPS_PORT", "631"))
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "/data/printjobs.db")
PAGE_LOG = os.environ.get("PAGE_LOG", "/var/log/cups/page_log")
RETENTION_DAYS = int(os.environ.get("WEB_RETENTION_DAYS", "365"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "15"))

JOB_ATTRS = [
    "job-id",
    "job-name",
    "job-printer-uri",
    "job-originating-user-name",
    "job-originating-host-name",
    "job-k-octets",
    "job-impressions-completed",
    "job-state",
    "job-state-reasons",
    "time-at-creation",
    "time-at-processing",
    "time-at-completed",
]

JOB_STATES = {
    3: "pending",
    4: "held",
    5: "processing",
    6: "stopped",
    7: "canceled",
    8: "aborted",
    9: "completed",
}

PRINTER_STATES = {3: "idle", 4: "processing", 5: "stopped"}

app = Flask(__name__)


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        INTEGER NOT NULL,
    created_at    INTEGER NOT NULL,
    printer       TEXT,
    user          TEXT,
    host          TEXT,
    title         TEXT,
    size_kb       INTEGER,
    impressions   INTEGER,
    pages         INTEGER,
    state         INTEGER,
    state_reasons TEXT,
    started_at    INTEGER,
    finished_at   INTEGER,
    PRIMARY KEY (job_id, created_at)
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_printer ON jobs (printer);
CREATE INDEX IF NOT EXISTS idx_jobs_user    ON jobs (user);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = connect_db()
    with conn:
        conn.executescript(SCHEMA)
    conn.close()


def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


# --------------------------------------------------------------------------
# CUPS
# --------------------------------------------------------------------------


def cups_connect():
    cups.setServer(CUPS_HOST)
    cups.setPort(CUPS_PORT)
    return cups.Connection()


def printer_from_uri(uri):
    """ipp://host/printers/sotano -> sotano"""
    if not uri:
        return None
    return uri.rstrip("/").rsplit("/", 1)[-1]


def normalize_job(job_id, attrs):
    created = attrs.get("time-at-creation") or 0
    return (
        int(job_id),
        int(created),
        printer_from_uri(attrs.get("job-printer-uri")),
        attrs.get("job-originating-user-name"),
        attrs.get("job-originating-host-name"),
        attrs.get("job-name"),
        attrs.get("job-k-octets"),
        attrs.get("job-impressions-completed"),
        attrs.get("job-state"),
        _as_text(attrs.get("job-state-reasons")),
        attrs.get("time-at-processing") or None,
        attrs.get("time-at-completed") or None,
    )


def _as_text(value):
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return value if value is None else str(value)


UPSERT = """
INSERT INTO jobs (job_id, created_at, printer, user, host, title, size_kb,
                  impressions, state, state_reasons, started_at, finished_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(job_id, created_at) DO UPDATE SET
    printer       = excluded.printer,
    user          = excluded.user,
    host          = excluded.host,
    title         = excluded.title,
    size_kb       = excluded.size_kb,
    impressions   = COALESCE(excluded.impressions, jobs.impressions),
    state         = excluded.state,
    state_reasons = excluded.state_reasons,
    started_at    = COALESCE(excluded.started_at, jobs.started_at),
    finished_at   = COALESCE(excluded.finished_at, jobs.finished_at)
"""


def ingest_jobs(conn):
    """Vuelca a SQLite los trabajos que CUPS todavia recuerda."""
    cups_conn = cups_connect()
    rows = []
    for which in ("completed", "not-completed"):
        jobs = cups_conn.getJobs(
            which_jobs=which, my_jobs=False, requested_attributes=JOB_ATTRS
        )
        for job_id, attrs in jobs.items():
            rows.append(normalize_job(job_id, attrs))
    with conn:
        conn.executemany(UPSERT, rows)
    return len(rows)


# --------------------------------------------------------------------------
# page_log (complemento: paginas cuando la impresora las reporta)
# --------------------------------------------------------------------------

# %p %u %j %T %P %C %{job-billing} %{job-originating-host-name} %{job-name}
PAGE_LOG_RE = re.compile(r"^(\S+) (\S+) (\d+) \[[^\]]*\] (\S+) (\S+)")


def ingest_page_log(conn):
    """Lee el page_log de forma incremental y suma copias por trabajo."""
    try:
        size = os.path.getsize(PAGE_LOG)
    except OSError:
        return 0

    offset = int(meta_get(conn, "page_log_offset", "0"))
    if size < offset:  # el log rotó
        offset = 0

    with open(PAGE_LOG, "r", errors="replace") as fh:
        fh.seek(offset)
        chunk = fh.read()
        new_offset = fh.tell()

    # La última línea puede estar a medio escribir: se reprocesa en la próxima vuelta.
    lines = chunk.split("\n")
    if not chunk.endswith("\n"):
        incomplete = lines.pop() if lines else ""
        new_offset -= len(incomplete.encode("utf-8", "replace"))

    counted = {}
    for line in lines:
        match = PAGE_LOG_RE.match(line)
        if not match:
            continue
        printer, _user, job_id, page_number, copies = match.groups()
        if page_number in ("total", "-"):
            continue
        try:
            copies = int(copies)
        except ValueError:
            copies = 1
        counted[(printer, int(job_id))] = counted.get((printer, int(job_id)), 0) + copies

    with conn:
        for (printer, job_id), pages in counted.items():
            # Se suma sobre la fila mas reciente de ese (job_id, impresora): los ids
            # de CUPS pueden reiniciarse si se vacia el spool.
            cur = conn.execute(
                "UPDATE jobs SET pages = COALESCE(pages, 0) + ? "
                "WHERE job_id = ? AND printer = ? "
                "AND created_at = (SELECT MAX(created_at) FROM jobs "
                "                  WHERE job_id = ? AND printer = ?)",
                (pages, job_id, printer, job_id, printer),
            )
            if cur.rowcount == 0:
                print(
                    f"[ingest] page_log: sin trabajo para {printer}#{job_id} "
                    f"({pages} pag.); CUPS ya lo olvido",
                    flush=True,
                )
        meta_set(conn, "page_log_offset", new_offset)
    return len(counted)


def purge_old(conn):
    if RETENTION_DAYS <= 0:
        return
    cutoff = int(time.time()) - RETENTION_DAYS * 86400
    with conn:
        conn.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,))


def ingest_loop():
    conn = connect_db()
    last_purge = 0.0
    while True:
        try:
            ingest_jobs(conn)
            ingest_page_log(conn)
            if time.time() - last_purge > 3600:
                purge_old(conn)
                last_purge = time.time()
        except Exception as exc:  # el hilo nunca debe morir
            print(f"[ingest] error: {exc}", flush=True)
        time.sleep(POLL_SECONDS)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/queues")
def api_queues():
    try:
        conn = cups_connect()
        printers = conn.getPrinters()
        active = conn.getJobs(
            which_jobs="not-completed", my_jobs=False, requested_attributes=JOB_ATTRS
        )
    except Exception as exc:
        return jsonify({"error": f"No se pudo conectar a CUPS: {exc}"}), 503

    by_printer = {}
    for job_id, attrs in active.items():
        name = printer_from_uri(attrs.get("job-printer-uri"))
        by_printer.setdefault(name, []).append(
            {
                "job_id": int(job_id),
                "title": attrs.get("job-name"),
                "user": attrs.get("job-originating-user-name"),
                "host": attrs.get("job-originating-host-name"),
                "size_kb": attrs.get("job-k-octets"),
                "created_at": attrs.get("time-at-creation"),
                "state": JOB_STATES.get(attrs.get("job-state"), "unknown"),
            }
        )

    result = []
    for name, info in sorted(printers.items()):
        jobs = sorted(by_printer.get(name, []), key=lambda j: j["created_at"] or 0)
        result.append(
            {
                "name": name,
                "device_uri": info.get("device-uri"),
                "state": PRINTER_STATES.get(info.get("printer-state"), "unknown"),
                "state_message": info.get("printer-state-message") or "",
                "state_reasons": _as_text(info.get("printer-state-reasons")) or "",
                "accepting": bool(info.get("printer-is-accepting-jobs", True)),
                "jobs": jobs,
            }
        )
    return jsonify({"printers": result, "now": int(time.time())})


def build_filters(args):
    where, params = [], []
    if args.get("printer"):
        where.append("printer = ?")
        params.append(args["printer"])
    if args.get("user"):
        where.append("user = ?")
        params.append(args["user"])
    if args.get("state"):
        where.append("state = ?")
        params.append(int(args["state"]))
    if args.get("from"):
        where.append("created_at >= ?")
        params.append(int(args["from"]))
    if args.get("to"):
        where.append("created_at <= ?")
        params.append(int(args["to"]))
    if args.get("q"):
        where.append("(title LIKE ? OR host LIKE ? OR user LIKE ?)")
        like = f"%{args['q']}%"
        params.extend([like, like, like])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def row_to_dict(row):
    data = dict(row)
    data["state_label"] = JOB_STATES.get(data.get("state"), "unknown")
    return data


@app.route("/api/jobs")
def api_jobs():
    db = get_db()
    clause, params = build_filters(request.args)
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(500, max(1, int(request.args.get("per_page", 50))))
    except ValueError:
        page, per_page = 1, 50

    total = db.execute(f"SELECT COUNT(*) AS n FROM jobs{clause}", params).fetchone()["n"]
    rows = db.execute(
        f"SELECT * FROM jobs{clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return jsonify(
        {
            "jobs": [row_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    )


@app.route("/api/filters")
def api_filters():
    db = get_db()
    printers = [
        r["printer"]
        for r in db.execute(
            "SELECT DISTINCT printer FROM jobs WHERE printer IS NOT NULL ORDER BY printer"
        )
    ]
    users = [
        r["user"]
        for r in db.execute(
            "SELECT DISTINCT user FROM jobs WHERE user IS NOT NULL ORDER BY user"
        )
    ]
    return jsonify({"printers": printers, "users": users, "states": JOB_STATES})


@app.route("/api/export.csv")
def api_export():
    db = get_db()
    clause, params = build_filters(request.args)
    rows = db.execute(
        f"SELECT * FROM jobs{clause} ORDER BY created_at DESC LIMIT 50000", params
    ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        ["fecha", "impresora", "usuario", "equipo", "documento", "kb", "paginas", "estado"]
    )
    for row in rows:
        writer.writerow(
            [
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["created_at"])),
                row["printer"] or "",
                row["user"] or "",
                row["host"] or "",
                row["title"] or "",
                row["size_kb"] if row["size_kb"] is not None else "",
                row["pages"] if row["pages"] is not None else "",
                JOB_STATES.get(row["state"], "unknown"),
            ]
        )
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=impresiones.csv",
        },
    )


@app.route("/api/jobs/<int:job_id>/cancel", methods=["POST"])
def api_cancel(job_id):
    try:
        cups_connect().cancelJob(job_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@app.route("/api/printers/<name>/<action>", methods=["POST"])
def api_printer_action(name, action):
    try:
        conn = cups_connect()
        if action == "enable":
            conn.enablePrinter(name)
        elif action == "disable":
            conn.disablePrinter(name)
        elif action == "purge":
            conn.cancelAllJobs(name)
        else:
            return jsonify({"error": "accion desconocida"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@app.route("/health")
def health():
    try:
        cups_connect().getPrinters()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    threading.Thread(target=ingest_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=WEB_PORT, threaded=True)
