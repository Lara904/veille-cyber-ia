#!/usr/bin/env python3
"""
Digest quotidien de veille — envoyé sur Telegram à 7h
"""

import os, json, requests
import psycopg2
from datetime import datetime

GROQ_API_KEY       = os.environ["GROQ_API_KEY"]
DATABASE_URL       = os.environ["DATABASE_URL"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

GROQ_URL           = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL_QUALITY = "llama-3.3-70b-versatile"  # 1 seul appel/jour → qualité max


def get_articles_24h() -> list[dict]:
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT title, source, summary, importance, category, tags, url
            FROM articles
            WHERE collected_at > NOW() - INTERVAL '24 hours'
            ORDER BY importance DESC, collected_at DESC
            LIMIT 40
        """)
        cols = ["title", "source", "summary", "importance", "category", "tags", "url"]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return rows


def generate_synthesis(articles: list[dict]) -> str:
    """Génère une synthèse narrative de la veille avec Groq 70b"""
    articles_text = "\n".join([
        f"[{a['importance']}/5] {a['title']} ({a['source']}) — {a['summary'][:200]}"
        for a in articles[:20]
    ])

    prompt = f"""Tu es un expert en cybersécurité et IA. 
Voici les articles de veille du jour. Génère une synthèse claire en français, 
en 5-8 points clés, en commençant par les éléments les plus critiques.
Utilise des emojis pertinents. Sois direct et actionnable.

Articles :
{articles_text}

Réponds avec la synthèse UNIQUEMENT, sans introduction ni conclusion."""

    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL_QUALITY,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000,
            "temperature": 0.3,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def format_digest(articles: list[dict], synthesis: str) -> str:
    """Formate le message Telegram du digest"""
    now = datetime.now()
    date_fr = now.strftime("%A %d %B %Y").capitalize()

    critical  = [a for a in articles if a["importance"] >= 4]
    important = [a for a in articles if a["importance"] == 3]
    info      = [a for a in articles if a["importance"] <= 2]

    lines = [
        f"📊 *Digest de veille — {date_fr}*",
        f"_{len(articles)} articles analysés_\n",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
        synthesis,
        "\n━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if critical:
        lines.append(f"\n🔥 *Critiques ({len(critical)})*")
        for a in critical[:5]:
            lines.append(f"• `[{a['importance']}/5]` *{a['title']}* — {a['source']}")
            if a.get("summary"):
                lines.append(f"  _{a['summary'][:120]}_")

    if important:
        lines.append(f"\n⚠️ *Importants ({len(important)})*")
        for a in important[:5]:
            lines.append(f"• *{a['title']}* — {a['source']}")

    if info:
        lines.append(f"\nℹ️ *Infos ({len(info)})*")
        lines.extend([f"• {a['title']} — {a['source']}" for a in info[:5]])

    return "\n".join(lines)


def send_telegram(text: str):
    """Envoie un message, découpe si > 4096 caractères"""
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )


def main():
    print("Génération du digest quotidien...")
    articles = get_articles_24h()

    if not articles:
        send_telegram("📊 *Digest du jour*\n\n_Aucun article collecté dans les dernières 24h._")
        print("Aucun article — message vide envoyé.")
        return

    print(f"{len(articles)} articles trouvés. Appel Groq 70b...")
    synthesis = generate_synthesis(articles)

    digest = format_digest(articles, synthesis)
    send_telegram(digest)
    print(f"✅ Digest envoyé : {len(articles)} articles, {len(digest)} caractères")


if __name__ == "__main__":
    main()
