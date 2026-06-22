from flask import Flask, jsonify, request
import os, psycopg2, requests

app = Flask(__name__)

DATABASE_URL       = os.environ.get("DATABASE_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

def db_query(sql, params=()):
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows

def send_tg(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True},
        timeout=10,
    )

@app.route('/api/articles')
def articles():
    rows = db_query("""
        SELECT url, title, source, category, importance, summary, collected_at
        FROM articles
        WHERE collected_at > NOW() - INTERVAL '7 days'
        ORDER BY importance DESC, collected_at DESC
        LIMIT 60
    """)
    for r in rows:
        if r.get("collected_at"):
            r["collected_at"] = r["collected_at"].isoformat()
    resp = jsonify(rows)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp

@app.route('/api/telegram', methods=['POST'])
def telegram():
    body = request.get_json(silent=True) or {}
    msg = body.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return "OK", 200

    if text in ("/start", "/help"):
        send_tg(chat_id,
            "*Commandes :*\n/critiques — Articles critiques\n"
            "/search [terme] — Rechercher\n/stats — Statistiques")

    elif text == "/critiques":
        rows = db_query("""
            SELECT title, source, importance, summary FROM articles
            WHERE importance >= 4 AND collected_at > NOW() - INTERVAL '7 days'
            ORDER BY importance DESC LIMIT 8
        """)
        if not rows:
            send_tg(chat_id, "Aucun article critique cette semaine.")
        else:
            lines = ["🔥 *Critiques — 7 derniers jours*\n"]
            for a in rows:
                lines.append(f"• `[{a['importance']}/5]` *{a['title']}* _{a['source']}_")
                if a.get("summary"):
                    lines.append(f"  {a['summary'][:120]}")
            send_tg(chat_id, "\n".join(lines))

    elif text.startswith("/search "):
        query = text[8:].strip()
        rows = db_query("""
            SELECT title, source, importance, summary FROM articles
            WHERE (title ILIKE %s OR summary ILIKE %s)
              AND collected_at > NOW() - INTERVAL '30 days'
            ORDER BY importance DESC LIMIT 5
        """, (f"%{query}%", f"%{query}%"))
        if not rows:
            send_tg(chat_id, f"Aucun résultat pour *{query}*.")
        else:
            lines = [f"🔍 *{query}*\n"]
            for a in rows:
                lines.append(f"• `[{a['importance']}/5]` *{a['title']}* _{a['source']}_")
                if a.get("summary"):
                    lines.append(f"  {a['summary'][:100]}")
            send_tg(chat_id, "\n".join(lines))

    elif text == "/stats":
        rows = db_query("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE importance >= 4) AS critiques,
                   COUNT(*) FILTER (WHERE collected_at > NOW() - INTERVAL '24h') AS auj
            FROM articles
        """)
        r = rows[0]
        send_tg(chat_id, f"📊 *Stats*\n\nTotal : {r['total']}\n"
                         f"Critiques : {r['critiques']}\nAujourd'hui : {r['auj']}")
    else:
        send_tg(chat_id, "Commande inconnue. Tape /help.")

    return "OK", 200

@app.route('/')
def index():
    try:
        with open('public/index.html', 'r') as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    except FileNotFoundError:
        return "Dashboard non trouvé", 404
