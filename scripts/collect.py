#!/usr/bin/env python3
"""
Collecteur de veille Cyber & IA
Exécuté par GitHub Actions toutes les 4 heures
"""

import os, json, time, re
import feedparser
import psycopg2
import requests
from datetime import datetime, timezone

# ─── Configuration ────────────────────────────────────────────────────────────

GROQ_API_KEY         = os.environ["GROQ_API_KEY"]
DATABASE_URL         = os.environ["DATABASE_URL"]
TELEGRAM_BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]

GROQ_MODEL_FAST      = "llama-3.1-8b-instant"   # 14 400 req/jour
GROQ_URL             = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DELAY_SECONDS   = 2.5  # Respect des 30 req/min

# ─── Flux RSS par domaine ─────────────────────────────────────────────────────

RSS_FEEDS = {
    "Cyber": [
        ("CISA",             "https://www.cisa.gov/cybersecurity-advisories/all.xml"),
        ("NVD",              "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml"),
        ("The Hacker News",  "https://thehackernews.com/feeds/posts/default"),
        ("BleepingComputer", "https://www.bleepingcomputer.com/feed/"),
        ("SANS ISC",         "https://isc.sans.edu/rssfeed_full.xml"),
        ("Krebs on Security","https://krebsonsecurity.com/feed/"),
        ("Microsoft SecBlog","https://www.microsoft.com/en-us/security/blog/feed/"),
        ("Google Proj Zero", "https://googleprojectzero.blogspot.com/feeds/posts/default"),
        ("Rapid7",           "https://www.rapid7.com/blog/feed"),
        ("Securelist",       "https://securelist.com/feed/"),
    ],
    "IA": [
        ("Anthropic",        "https://www.anthropic.com/rss.xml"),
        ("OpenAI",           "https://openai.com/blog/rss.xml"),
        ("HuggingFace",      "https://huggingface.co/blog/feed.xml"),
        ("DeepMind",         "https://deepmind.google/blog/rss.xml"),
        ("arXiv cs.AI",      "https://arxiv.org/rss/cs.AI"),
        ("arXiv cs.CR",      "https://arxiv.org/rss/cs.CR"),
        ("Papers With Code", "https://paperswithcode.com/latest/rss"),
        ("AI News",          "https://www.artificialintelligence-news.com/feed/"),
    ],
}

# ─── Prompt d'analyse ─────────────────────────────────────────────────────────

PROMPT_TEMPLATE = """Tu es un expert en cybersécurité et IA. Analyse cet article.
Réponds UNIQUEMENT avec un JSON valide, sans texte avant ou après.

{{
  "resume": "Décris : QUI est vulnérable, COMMENT fonctionne l'attaque techniquement (vecteur, mécanisme d'exploitation, type : RCE/SQLi/XSS/buffer overflow/etc.), et QUEL est l'impact concret",
  "technique": "Détail technique : type de vulnérabilité, condition d'exploitation, privilèges requis, interaction utilisateur nécessaire. Null si pas de faille.",
  "cve": "CVE-XXXX-XXXX si mentionné, sinon null",
  "cvss": "Score numérique CVSS si mentionné, sinon null",
  "versions_affectees": "Produits et versions concernés, sinon null",
  "importance": 3,
  "categorie": "CVE|Zero-Day|Threat Intel|Ransomware|APT|Cloud Security|LLM|Open Source AI|Agent IA|Paper|Outil|Réglementation|Autre",
  "technologies": ["tech1"],
  "tags": ["tag1", "tag2"],
  "actions": "Action concrète (patcher vers X.X, isoler le service, bloquer le port...) ou null"
}}

Importance : 1=info, 2=intéressant, 3=important, 4=critique, 5=alerte max (CVSS≥9, 0-day actif)

Titre   : {title}
Source  : {source}
Contenu : {content}"""


# ─── Fonctions utilitaires ────────────────────────────────────────────────────

def get_db():
    """Connexion Neon avec timeout pour le cold start (5s de réveil possible)"""
    return psycopg2.connect(DATABASE_URL, connect_timeout=15)


def url_exists(conn, url: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM articles WHERE url = %s LIMIT 1", (url,))
        return cur.fetchone() is not None


def insert_article(conn, data: dict):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO articles
                (url, title, source, category, published_at,
                 summary, importance, tags, technologies, actions, raw_content)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
        """, (
            data["url"],
            data["title"],
            data["source"],
            data.get("categorie", "Autre"),
            data.get("published_at"),
            data.get("resume"),
            data.get("importance", 1),
            data.get("tags", []),
            data.get("technologies", []),
            data.get("actions"),
            data.get("raw_content", ""),
        ))
        conn.commit()


def extract_json(text: str) -> dict:
    """Extrait le JSON d'une réponse LLM même avec des artefacts"""
    text = text.strip()

    # Tentative directe
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Entre backticks ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Premier objet JSON dans le texte
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"JSON introuvable dans : {text[:300]}")


def analyze_with_groq(title: str, source: str, content: str) -> dict:
    """Appelle Groq pour analyser un article"""
    prompt = PROMPT_TEMPLATE.format(
        title=title,
        source=source,
        content=content[:3000],
    )

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL_FAST,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 600,
            "temperature": 0.1,
        },
        timeout=30,
    )

    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]
    return extract_json(raw)


def send_telegram(message: str):
    """Envoie un message Telegram (gère les messages > 4096 caractères)"""
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
    for chunk in chunks:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )


def get_article_content(entry) -> str:
    """Extrait le meilleur contenu disponible d'une entrée RSS"""
    # Ordre de priorité : content > summary > description > title
    content_list = getattr(entry, "content", [])
    if content_list:
        return content_list[0].get("value", "")
    for attr in ("summary", "description", "title"):
        val = getattr(entry, attr, "")
        if val:
            return val
    return ""


# ─── Programme principal ──────────────────────────────────────────────────────

def main():
    print(f"\n{'='*50}")
    print(f"Démarrage collecte : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    conn = get_db()
    new_count = 0
    error_count = 0
    critical_alerts = []

    for domain, feeds in RSS_FEEDS.items():
        print(f"\n[{domain}]")

        for source_name, feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url, request_headers={"User-Agent": "Mozilla/5.0"})
                entries = feed.entries[:12]  # Max 12 articles par flux
                print(f"  {source_name}: {len(entries)} entrées")

                for entry in entries:
                    url = getattr(entry, "link", None)
                    title = getattr(entry, "title", "Sans titre").strip()

                    if not url:
                        continue

                    # Vérifier si l'article est déjà en base
                    if url_exists(conn, url):
                        continue

                    # Extraire le contenu
                    content = get_article_content(entry)
                    published = getattr(entry, "published", None)

                    # Analyse avec Groq
                    try:
                        analysis = analyze_with_groq(title, source_name, content)
                    except Exception as e:
                        print(f"    ⚠ Groq error pour '{title[:50]}': {e}")
                        error_count += 1
                        time.sleep(GROQ_DELAY_SECONDS)
                        continue

                    # Préparer les données pour insertion
                    article_data = {
                        "url": url,
                        "title": title,
                        "source": source_name,
                        "published_at": published,
                        "raw_content": content[:5000],
                        **analysis,
                    }

                    insert_article(conn, article_data)
                    new_count += 1
                    imp = analysis.get("importance", 1)
                    print(f"    ✓ [{imp}/5] {title[:60]}")

                    # Alerte immédiate si critique (importance ≥ 4)
                    if imp >= 4:
                        critical_alerts.append(article_data)

                    # Respecter la limite 30 req/min de Groq
                    time.sleep(GROQ_DELAY_SECONDS)

            except Exception as e:
                print(f"  ✗ Erreur flux {source_name}: {e}")
                continue

    conn.close()

    # ── Envoyer les alertes critiques sur Telegram ─────────────────────────
    for alert in critical_alerts:
        score = alert.get("importance", "?")
        msg = (
            f"🚨 *Alerte Veille — {score}/5*\n\n"
            f"*{alert['title']}*\n"
            f"_{alert['source']}_\n\n"
            f"{alert.get('resume', '')}\n\n"
            f"📁 `{alert.get('categorie', 'Autre')}`"
        )
        if alert.get("actions"):
            msg += f"\n\n✅ *Action :* {alert['actions']}"
        msg += f"\n\n🔗 {alert['url']}"
        send_telegram(msg)

    # ── Résumé de la collecte ──────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"✅ Nouveaux articles : {new_count}")
    print(f"🚨 Alertes critiques : {len(critical_alerts)}")
    print(f"⚠  Erreurs Groq     : {error_count}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
