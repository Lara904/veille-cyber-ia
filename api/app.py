from flask import Flask, jsonify, request
import os, psycopg2, requests

app = Flask(__name__)

DATABASE_URL       = os.environ.get("DATABASE_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")


def db_query(sql, params=()):
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def send_tg(chat_id, text):
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": chunk,
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
            "*Commandes disponibles :*\n\n"
            "/critiques — Articles critiques (7j)\n"
            "/search [terme] — Rechercher un sujet\n"
            "/stats — Statistiques de la base\n"
            "/help — Cette aide\n\n"
            "💡 Tu peux aussi poser une question libre :\n"
            "_\"Explique-moi la faille Apache mentionnée\"\n"
            "\"Comment fonctionne ce ransomware ?\"_")

    elif text == "/critiques":
        rows = db_query("""
            SELECT title, source, importance, summary, cve, cvss
            FROM articles
            WHERE importance >= 4
              AND collected_at > NOW() - INTERVAL '7 days'
            ORDER BY importance DESC LIMIT 8
        """)
        if not rows:
            send_tg(chat_id, "Aucun article critique cette semaine.")
        else:
            lines = ["🔥 *Critiques — 7 derniers jours*\n"]
            for a in rows:
                line = f"• `[{a['importance']}/5]` *{a['title']}* _{a['source']}_"
                if a.get("cve"):
                    line += f" — `{a['cve']}`"
                if a.get("cvss"):
                    line += f" CVSS {a['cvss']}"
                lines.append(line)
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
            send_tg(chat_id, f"Aucun résultat pour *{query}* (30 derniers jours).")
        else:
            lines = [f"🔍 *Résultats : {query}*\n"]
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
        send_tg(chat_id,
            f"📊 *Stats de la base*\n\n"
            f"Total articles : {r['total']}\n"
            f"Critiques (≥4) : {r['critiques']}\n"
            f"Aujourd'hui    : {r['auj']}")

    else:
        # ── Question libre : recherche + réponse Groq ──────────────────────
        question = text
        rows = db_query("""
            SELECT title, source, summary, technique, cve, cvss,
                   versions_affectees, actions, url
            FROM articles
            WHERE (title ILIKE %s OR summary ILIKE %s OR technique ILIKE %s)
              AND collected_at > NOW() - INTERVAL '30 days'
            ORDER BY importance DESC, collected_at DESC
            LIMIT 5
        """, (f"%{question}%",) * 3)

        if not rows:
            send_tg(chat_id,
                f"Aucun article trouvé pour _\"{question}\"_.\n"
                f"Essaie /search {question} ou reformule.")
            return "OK", 200

        contexte = "\n\n".join([
            f"Titre : {a['title']} ({a['source']})\n"
            f"Résumé : {a.get('summary','')}\n"
            f"Technique : {a.get('technique','N/A')}\n"
            f"CVE : {a.get('cve','N/A')} | CVSS : {a.get('cvss','N/A')}\n"
            f"Versions affectées : {a.get('versions_affectees','N/A')}\n"
            f"Action recommandée : {a.get('actions','N/A')}"
            for a in rows
        ])

        prompt = (
            f"Tu es un expert en cybersécurité et IA.\n"
            f"Question de l'utilisateur : \"{question}\"\n\n"
            f"Articles disponibles dans la base de veille :\n{contexte}\n\n"
            f"Réponds en français de façon claire et technique. "
            f"Si c'est une faille, explique : le type de vulnérabilité, "
            f"comment elle fonctionne, comment l'exploiter, et comment s'en protéger. "
            f"Sois précis et actionnable. Maximum 400 mots."
        )

        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 800, "temperature": 0.3},
                timeout=30,
            )
            answer = resp.json()["choices"][0]["message"]["content"]
            send_tg(chat_id, f"🤖 *{question}*\n\n{answer}")
        except Exception as e:
            send_tg(chat_id, f"Erreur lors de la réponse : {e}")

    return "OK", 200


@app.route('/')
def index():
    try:
        with open('public/index.html', 'r') as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    except FileNotFoundError:
        return "Dashboard non trouvé", 404
