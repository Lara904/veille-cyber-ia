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
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Veille Cyber & IA</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #0f0f0f; color: #e0e0e0; }
    header { background: #1a1a2e; padding: 1rem; text-align: center; }
    header h1 { color: #7f77dd; font-size: 1.2rem; }
    .filters { display: flex; gap: .5rem; padding: 1rem; overflow-x: auto; }
    .btn { padding: .4rem .8rem; border-radius: 20px; border: 1px solid #333;
           background: #222; color: #aaa; cursor: pointer; white-space: nowrap; font-size: .85rem; }
    .btn.active { background: #7f77dd; color: #fff; border-color: #7f77dd; }
    #articles { padding: 0 1rem 1rem; }
    .card { background: #1a1a1a; border-radius: 10px; padding: 1rem; margin-bottom: .8rem;
            border-left: 3px solid #333; }
    .card[data-imp="5"] { border-left-color: #e24b4a; }
    .card[data-imp="4"] { border-left-color: #ef9f27; }
    .card[data-imp="3"] { border-left-color: #7f77dd; }
    .card h3 { font-size: .95rem; margin-bottom: .4rem; }
    .card p  { font-size: .82rem; color: #999; line-height: 1.5; }
    .meta    { font-size: .75rem; color: #555; margin-top: .4rem; }
    .badge   { display: inline-block; padding: 2px 8px; border-radius: 10px;
               font-size: .7rem; background: #2a2a2a; color: #777; margin-right: 4px; }
    #loading { text-align: center; padding: 2rem; color: #555; }
  </style>
</head>
<body>
  <header><h1>&#x1F50D; Veille Cyber &amp; IA</h1></header>
  <div class="filters">
    <button class="btn active" onclick="doFilter('all',this)">Tout</button>
    <button class="btn" onclick="doFilter('critique',this)">&#x1F525; Critiques</button>
    <button class="btn" onclick="doFilter('Cyber',this)">&#x1F6E1; Cyber</button>
    <button class="btn" onclick="doFilter('IA',this)">&#x1F916; IA</button>
    <button class="btn" onclick="doFilter('CVE',this)">CVE</button>
  </div>
  <div id="loading">Chargement...</div>
  <div id="articles"></div>
  <script>
    let all = [];
    async function load() {
      try {
        const r = await fetch('/api/articles');
        all = await r.json();
        render(all);
      } catch(e) {
        document.getElementById('loading').textContent = 'Erreur : ' + e;
      }
    }
    function doFilter(f, btn) {
      document.querySelectorAll('.btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      if (f === 'all')      return render(all);
      if (f === 'critique') return render(all.filter(a => a.importance >= 4));
      render(all.filter(a => (a.category || '').includes(f)));
    }
    function render(articles) {
      document.getElementById('loading').style.display = 'none';
      document.getElementById('articles').innerHTML = articles.length ? articles.map(a => `
        <div class="card" data-imp="${a.importance}">
          <h3><a href="${a.url}" target="_blank" style="color:inherit;text-decoration:none">${a.title}</a></h3>
          <p>${a.summary || ''}</p>
          <div class="meta">
            <span class="badge">${a.source || ''}</span>
            <span class="badge">${a.category || ''}</span>
            <span class="badge">&#x2605; ${a.importance}/5</span>
            <span style="float:right">${new Date(a.collected_at).toLocaleDateString('fr-FR')}</span>
          </div>
        </div>`).join('') :
        '<p style="text-align:center;padding:2rem;color:#555">Aucun article</p>';
    }
    load();
  </script>
</body>
</html>""", 200, {'Content-Type': 'text/html; charset=utf-8'}
