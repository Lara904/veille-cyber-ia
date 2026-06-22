from http.server import BaseHTTPRequestHandler
import json, os, psycopg2, requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATABASE_URL       = os.environ["DATABASE_URL"]
GROQ_API_KEY       = os.environ["GROQ_API_KEY"]


def send(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True},
    )


def db_query(sql, params=()):
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def cmd_critiques(chat_id):
    articles = db_query("""
        SELECT title, source, importance, url, summary
        FROM articles
        WHERE importance >= 4 AND collected_at > NOW() - INTERVAL '7 days'
        ORDER BY importance DESC, collected_at DESC LIMIT 8
    """)
    if not articles:
        send(chat_id, "Aucun article critique cette semaine.")
        return
    lines = ["🔥 *Critiques — 7 derniers jours*\n"]
    for a in articles:
        lines.append(f"• `[{a['importance']}/5]` *{a['title']}* _{a['source']}_")
        if a.get("summary"):
            lines.append(f"  {a['summary'][:120]}")
    send(chat_id, "\n".join(lines))


def cmd_search(chat_id, query):
    articles = db_query("""
        SELECT title, source, importance, summary, url
        FROM articles
        WHERE (title ILIKE %s OR summary ILIKE %s)
          AND collected_at > NOW() - INTERVAL '30 days'
        ORDER BY importance DESC, collected_at DESC LIMIT 5
    """, (f"%{query}%", f"%{query}%"))
    if not articles:
        send(chat_id, f"Aucun résultat pour *{query}* (30 derniers jours).")
        return
    lines = [f"🔍 *Résultats : {query}*\n"]
    for a in articles:
        lines.append(f"• `[{a['importance']}/5]` *{a['title']}* _{a['source']}_")
        if a.get("summary"):
            lines.append(f"  {a['summary'][:100]}")
    send(chat_id, "\n".join(lines))


def cmd_stats(chat_id):
    rows = db_query("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE importance >= 4) AS critiques,
               COUNT(*) FILTER (WHERE collected_at > NOW() - INTERVAL '24h') AS aujourd_hui
        FROM articles
    """)
    r = rows[0]
    send(chat_id,
         f"📊 *Stats de la base*\n\n"
         f"Total articles : {r['total']}\n"
         f"Critiques (≥4) : {r['critiques']}\n"
         f"Aujourd'hui    : {r[\"aujourd_hui\"]}")


HELP_TEXT = """*Commandes disponibles :*

/critiques — Articles critiques (7 derniers jours)
/search [terme] — Rechercher dans les 30 derniers jours
/stats — Statistiques de la base
/help — Cette aide

*Exemples :*
/search CVE Apache
/search ransomware
/search llama"""


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        msg = body.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        text = (msg.get("text") or "").strip()

        if chat_id and text:
            if text == "/start" or text == "/help":
                send(chat_id, HELP_TEXT)
            elif text == "/critiques":
                cmd_critiques(chat_id)
            elif text.startswith("/search "):
                cmd_search(chat_id, text[8:].strip())
            elif text == "/stats":
                cmd_stats(chat_id)
            else:
                send(chat_id, "Commande inconnue. Tape /help pour la liste.")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass  # Évite les logs verbeux de BaseHTTPRequestHandler


