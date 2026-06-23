from flask import Flask, jsonify, request
import os, psycopg2, requests, hmac, hashlib

app = Flask(__name__)

DATABASE_URL       = os.environ.get("DATABASE_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")

# ─── Helpers DB ───────────────────────────────────────────────────────────────

def db_query(sql, params=()):
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


# ─── Helpers Telegram ─────────────────────────────────────────────────────────

def send_tg(chat_id, text):
    """Envoie un message Markdown en découpant si besoin"""
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id":    chat_id,
                    "text":       chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            # Si le Markdown plante, renvoyer en texte brut
            if not r.json().get("ok"):
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk,
                          "disable_web_page_preview": True},
                    timeout=10,
                )
        except Exception as e:
            print(f"send_tg error: {e}")


# ─── Route : API articles (dashboard) ────────────────────────────────────────

@app.route('/api/articles')
def articles():
    rows = db_query("""
        SELECT url, title, source, category, importance,
               summary, cve, cvss, collected_at
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


# ─── Route : webhook Telegram ─────────────────────────────────────────────────

@app.route('/api/telegram', methods=['POST'])
def telegram():
    body    = request.get_json(silent=True) or {}
    msg     = body.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text    = (msg.get("text") or "").strip()

    if not chat_id or not text:
        return "OK", 200

    # ── /start ou /help ────────────────────────────────────────────────────
    if text in ("/start", "/help"):
        send_tg(chat_id,
            "*Commandes disponibles :*\n\n"
            "/critiques — Articles critiques (7j)\n"
            "/services — Incidents sur tes services (GitHub, Docker, Gmail…)\n"
            "/deeplearning — Derniers papers DL / JEPA / modèles\n"
            "/search [terme] — Rechercher un sujet\n"
            "/stats — Statistiques de la base\n"
            "/help — Cette aide\n\n"
            "💡 Tu peux aussi poser une question libre :\n"
            "_\"Explique-moi la faille Apache mentionnée\"\n"
            "\"C'est quoi JEPA de Meta ?\"\n"
            "\"Dernier incident GitHub ?\"_"
        )

    # ── /critiques ─────────────────────────────────────────────────────────
    elif text == "/critiques":
        rows = db_query("""
            SELECT title, source, importance, summary, cve, cvss, url
            FROM articles
            WHERE importance >= 4
              AND collected_at > NOW() - INTERVAL '7 days'
            ORDER BY importance DESC LIMIT 8
        """)
        if not rows:
            send_tg(chat_id, "Aucun article critique cette semaine. ✅")
        else:
            lines = ["🔥 *Critiques — 7 derniers jours*\n"]
            for a in rows:
                line = f"• `[{a['importance']}/5]` *{a['title']}* _{a['source']}_"
                if a.get("cve"):
                    line += f"\n  🔖 `{a['cve']}`"
                    if a.get("cvss"):
                        line += f" — CVSS *{a['cvss']}*"
                lines.append(line)
                if a.get("summary"):
                    lines.append(f"  {a['summary'][:130]}")
                lines.append(f"  🔗 {a['url']}")
            send_tg(chat_id, "\n".join(lines))

    # ── /services — incidents sur les services surveillés ──────────────────
    elif text == "/services":
        rows = db_query("""
            SELECT title, source, importance, summary, url, collected_at
            FROM articles
            WHERE (
                title ILIKE ANY(ARRAY[
                    '%github%','%docker%','%spotify%','%gmail%','%google%',
                    '%snapchat%','%snap%','%exegol%','%kubernetes%','%gitlab%',
                    '%france connect%','%ameli%','%impots%','%anssi%','%gouv.fr%',
                    '%service-public%'
                ])
                OR 'service-surveillé' = ANY(tags)
                OR source ILIKE ANY(ARRAY[
                    '%github%','%docker%','%spotify%','%snap%','%anssi%','%cert%'
                ])
            )
            AND collected_at > NOW() - INTERVAL '7 days'
            ORDER BY importance DESC, collected_at DESC
            LIMIT 10
        """)
        if not rows:
            send_tg(chat_id, "Aucun incident détecté sur tes services cette semaine. ✅")
        else:
            lines = ["🛠 *Incidents & actus — services surveillés (7j)*\n"]
            for a in rows:
                lines.append(f"• `[{a['importance']}/5]` *{a['title']}*")
                lines.append(f"  _{a['source']}_")
                if a.get("summary"):
                    lines.append(f"  {a['summary'][:120]}")
                lines.append(f"  🔗 {a['url']}")
            send_tg(chat_id, "\n".join(lines))

    # ── /deeplearning — derniers papers DL, JEPA, modèles ─────────────────
    elif text in ("/deeplearning", "/dl"):
        rows = db_query("""
            SELECT title, source, importance, summary, url, category
            FROM articles
            WHERE (
                category IN ('Deep Learning','LLM','Paper','Open Source AI','Agent IA','JEPA')
                OR 'deep-learning' = ANY(tags)
                OR title ILIKE ANY(ARRAY[
                    '%deep learning%','%neural network%','%transformer%',
                    '%jepa%','%diffusion%','%llm%','%foundation model%',
                    '%self-supervised%','%world model%','%mamba%','%ssm%',
                    '%mixture of experts%','%moe%','%rlhf%','%fine-tun%',
                    '%multimodal%','%embedding%','%lora%','%qlora%'
                ])
                OR source ILIKE ANY(ARRAY[
                    '%arxiv%','%papers with code%','%distill%','%gradient%',
                    '%deepmind%','%meta ai%','%huggingface%'
                ])
            )
            AND collected_at > NOW() - INTERVAL '7 days'
            ORDER BY importance DESC, collected_at DESC
            LIMIT 10
        """)
        if not rows:
            send_tg(chat_id,
                "Aucun paper / article deep learning cette semaine.\n"
                "Essaie `/search jepa` ou `/search transformer`.")
        else:
            lines = ["🧠 *Deep Learning & modèles — 7 derniers jours*\n"]
            for a in rows:
                lines.append(f"• `[{a['importance']}/5]` *{a['title']}*")
                lines.append(f"  _{a['source']}_ — `{a.get('category','')}`")
                if a.get("summary"):
                    lines.append(f"  {a['summary'][:130]}")
                lines.append(f"  🔗 {a['url']}")
            send_tg(chat_id, "\n".join(lines))

    # ── /search [terme] ────────────────────────────────────────────────────
    elif text.startswith("/search "):
        query = text[8:].strip()
        rows = db_query("""
            SELECT title, source, importance, summary, url FROM articles
            WHERE (title ILIKE %s OR summary ILIKE %s
                   OR %s = ANY(tags) OR %s = ANY(technologies))
              AND collected_at > NOW() - INTERVAL '30 days'
            ORDER BY importance DESC LIMIT 6
        """, (f"%{query}%", f"%{query}%", query.lower(), query.lower()))
        if not rows:
            send_tg(chat_id, f"Aucun résultat pour *{query}* (30 derniers jours).")
        else:
            lines = [f"🔍 *Résultats : {query}*\n"]
            for a in rows:
                lines.append(f"• `[{a['importance']}/5]` *{a['title']}* _{a['source']}_")
                if a.get("summary"):
                    lines.append(f"  {a['summary'][:100]}")
                lines.append(f"  🔗 {a['url']}")
            send_tg(chat_id, "\n".join(lines))

    # ── /stats ─────────────────────────────────────────────────────────────
    elif text == "/stats":
        rows = db_query("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE importance >= 4) AS critiques,
                COUNT(*) FILTER (WHERE collected_at > NOW() - INTERVAL '24h') AS auj,
                COUNT(*) FILTER (WHERE category IN ('Deep Learning','LLM','Paper','JEPA')
                                   OR 'deep-learning' = ANY(tags)) AS dl_papers,
                COUNT(*) FILTER (WHERE 'service-surveillé' = ANY(tags)
                                   OR source ILIKE '%github%'
                                   OR source ILIKE '%docker%') AS services
            FROM articles
        """)
        r = rows[0]
        send_tg(chat_id,
            f"📊 *Stats de la base*\n\n"
            f"Total articles  : {r['total']}\n"
            f"Critiques (≥4)  : {r['critiques']}\n"
            f"Aujourd'hui     : {r['auj']}\n"
            f"Deep Learning   : {r['dl_papers']}\n"
            f"Services suivis : {r['services']}")

    # ── Question libre ─────────────────────────────────────────────────────
    else:
        question = text

        # Recherche élargie : titre, résumé, technique, tags, technologies
        rows = db_query("""
            SELECT title, source, summary, technique, cve, cvss,
                   versions_affectees, actions, url, category
            FROM articles
            WHERE (
                title       ILIKE %s
                OR summary  ILIKE %s
                OR technique ILIKE %s
                OR tags::text ILIKE %s
                OR technologies::text ILIKE %s
            )
            AND collected_at > NOW() - INTERVAL '30 days'
            ORDER BY importance DESC, collected_at DESC
            LIMIT 5
        """, (f"%{question}%",) * 5)

        if not rows:
            send_tg(chat_id,
                f"Aucun article trouvé pour _\"{question}\"_ dans les 30 derniers jours.\n"
                f"Essaie `/search {question}` ou reformule le terme clé.")
            return "OK", 200

        contexte = "\n\n".join([
            f"Titre : {a['title']} ({a['source']})\n"
            f"Catégorie : {a.get('category','')}\n"
            f"Résumé : {a.get('summary','')}\n"
            f"Technique : {a.get('technique','N/A')}\n"
            f"CVE : {a.get('cve','N/A')} | CVSS : {a.get('cvss','N/A')}\n"
            f"Versions : {a.get('versions_affectees','N/A')}\n"
            f"Action : {a.get('actions','N/A')}"
            for a in rows
        ])

        # Prompt adapté selon le type de question
        is_security_q = any(kw in question.lower() for kw in [
            "faille", "cve", "exploit", "vuln", "patch", "ransomware", "apt",
            "attaque", "malware", "zero-day", "breach", "hack"
        ])
        is_dl_q = any(kw in question.lower() for kw in [
            "jepa", "transformer", "llm", "diffusion", "neural", "deep learning",
            "modèle", "paper", "architecture", "entraîn", "fine-tun"
        ])

        if is_security_q:
            instruction = (
                "Si c'est une faille, explique : le type de vulnérabilité, "
                "comment elle fonctionne, comment l'exploiter, et comment s'en protéger. "
                "Sois précis et actionnable."
            )
        elif is_dl_q:
            instruction = (
                "Si c'est une architecture ou un paper de deep learning, explique : "
                "le principe, l'innovation par rapport à l'existant, les résultats clés, "
                "et les cas d'usage pratiques. Sois pédagogue et précis."
            )
        else:
            instruction = (
                "Réponds de façon claire et technique. "
                "Explique les points clés, l'impact, et les actions recommandées si pertinent."
            )

        prompt = (
            f"Tu es un expert en cybersécurité et IA.\n"
            f"Question : \"{question}\"\n\n"
            f"Articles disponibles :\n{contexte}\n\n"
            f"{instruction} Maximum 400 mots. Réponds en français."
        )

        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model":       "llama-3.3-70b-versatile",
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  800,
                    "temperature": 0.3,
                },
                timeout=30,
            )
            answer = resp.json()["choices"][0]["message"]["content"]
            send_tg(chat_id, f"🤖 *{question}*\n\n{answer}")
        except Exception as e:
            send_tg(chat_id, f"⚠️ Erreur lors de la réponse : {e}")

    return "OK", 200


# ─── Dashboard web ────────────────────────────────────────────────────────────

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
           background: #222; color: #aaa; cursor: pointer; white-space: nowrap;
           font-size: .85rem; transition: all .2s; }
    .btn.active { background: #7f77dd; color: #fff; border-color: #7f77dd; }
    #articles { padding: 0 1rem 1rem; }
    .card { background: #1a1a1a; border-radius: 10px; padding: 1rem;
            margin-bottom: .8rem; border-left: 3px solid #333; }
    .card[data-imp="5"] { border-left-color: #e24b4a; }
    .card[data-imp="4"] { border-left-color: #ef9f27; }
    .card[data-imp="3"] { border-left-color: #7f77dd; }
    .card h3 { font-size: .95rem; margin-bottom: .4rem; }
    .card p  { font-size: .82rem; color: #999; line-height: 1.5; }
    .meta    { font-size: .75rem; color: #555; margin-top: .4rem; }
    .badge   { display: inline-block; padding: 2px 8px; border-radius: 10px;
               font-size: .7rem; background: #2a2a2a; color: #777; margin-right: 4px; }
    .badge.cve { background: #3a1a1a; color: #e24b4a; }
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
    <button class="btn" onclick="doFilter('deeplearning',this)">&#x1F9E0; Deep Learning</button>
    <button class="btn" onclick="doFilter('services',this)">&#x1F6E0; Services</button>
    <button class="btn" onclick="doFilter('CVE',this)">CVE</button>
  </div>
  <div id="loading">Chargement...</div>
  <div id="articles"></div>
  <script>
    let all = [];
    const DL_CATS = ['Deep Learning','LLM','Paper','Open Source AI','Agent IA','JEPA'];
    const SVC_KW  = ['github','docker','spotify','gmail','snapchat','exegol',
                      'kubernetes','anssi','france connect'];

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
      if (f === 'all')         return render(all);
      if (f === 'critique')    return render(all.filter(a => a.importance >= 4));
      if (f === 'deeplearning')return render(all.filter(a =>
        DL_CATS.includes(a.category) ||
        (a.title||'').toLowerCase().match(/deep learning|jepa|transformer|llm|diffusion|neural/)));
      if (f === 'services')    return render(all.filter(a =>
        SVC_KW.some(k => (a.title||'').toLowerCase().includes(k)) ||
        SVC_KW.some(k => (a.source||'').toLowerCase().includes(k))));
      render(all.filter(a => (a.category || '').includes(f)));
    }

    function render(articles) {
      document.getElementById('loading').style.display = 'none';
      document.getElementById('articles').innerHTML = articles.length
        ? articles.map(a => `
          <div class="card" data-imp="${a.importance}">
            <h3><a href="${a.url}" target="_blank"
                   style="color:inherit;text-decoration:none">${a.title}</a></h3>
            <p>${a.summary || ''}</p>
            <div class="meta">
              <span class="badge">${a.source || ''}</span>
              <span class="badge">${a.category || ''}</span>
              <span class="badge">&#x2605; ${a.importance}/5</span>
              ${a.cve ? `<span class="badge cve">${a.cve}</span>` : ''}
              <span style="float:right">
                ${new Date(a.collected_at).toLocaleDateString('fr-FR')}
              </span>
            </div>
          </div>`).join('')
        : '<p style="text-align:center;padding:2rem;color:#555">Aucun article</p>';
    }

    load();
  </script>
</body>
</html>""", 200, {'Content-Type': 'text/html; charset=utf-8'}
