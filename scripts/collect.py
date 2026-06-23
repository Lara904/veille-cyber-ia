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
        # ── Sources existantes ──────────────────────────────────────────────
        ("CISA",              "https://www.cisa.gov/cybersecurity-advisories/all.xml"),
        ("NVD",               "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml"),
        ("The Hacker News",   "https://thehackernews.com/feeds/posts/default"),
        ("BleepingComputer",  "https://www.bleepingcomputer.com/feed/"),
        ("SANS ISC",          "https://isc.sans.edu/rssfeed_full.xml"),
        ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
        ("Microsoft SecBlog", "https://www.microsoft.com/en-us/security/blog/feed/"),
        ("Google Proj Zero",  "https://googleprojectzero.blogspot.com/feeds/posts/default"),
        ("Rapid7",            "https://www.rapid7.com/blog/feed"),
        ("Securelist",        "https://securelist.com/feed/"),
        # ── Services utilisés — statut & incidents ──────────────────────────
        ("GitHub Status",     "https://www.githubstatus.com/history.rss"),
        ("GitHub Blog Sec",   "https://github.blog/category/security/feed/"),
        ("Docker Blog",       "https://www.docker.com/blog/feed/"),
        # ── France / ANSSI ──────────────────────────────────────────────────
        ("ANSSI Alertes",     "https://www.cert.ssi.gouv.fr/alerte/feed/"),
        ("ANSSI Avis",        "https://www.cert.ssi.gouv.fr/avis/feed/"),
        ("ANSSI Actualités",  "https://www.ssi.gouv.fr/actualite/feed/"),
        ("LeMagIT Sécu",      "https://www.lemagit.fr/rss/Security.xml"),
    ],
    "IA": [
        # ── Sources existantes ──────────────────────────────────────────────
        ("Anthropic",         "https://www.anthropic.com/rss.xml"),
        ("OpenAI",            "https://openai.com/blog/rss.xml"),
        ("HuggingFace",       "https://huggingface.co/blog/feed.xml"),
        ("DeepMind",          "https://deepmind.google/blog/rss.xml"),
        ("arXiv cs.AI",       "https://arxiv.org/rss/cs.AI"),
        ("arXiv cs.CR",       "https://arxiv.org/rss/cs.CR"),
        ("Papers With Code",  "https://paperswithcode.com/latest/rss"),
        ("AI News",           "https://www.artificialintelligence-news.com/feed/"),
        # ── Deep Learning & recherche fondamentale ──────────────────────────
        ("arXiv cs.LG",       "https://arxiv.org/rss/cs.LG"),   # Machine Learning
        ("arXiv cs.NE",       "https://arxiv.org/rss/cs.NE"),   # Neural & Evolutionary
        ("arXiv cs.CV",       "https://arxiv.org/rss/cs.CV"),   # Computer Vision
        ("arXiv cs.CL",       "https://arxiv.org/rss/cs.CL"),   # NLP / LLMs
        ("Distill.pub",       "https://distill.pub/rss.xml"),
        ("The Gradient",      "https://thegradient.pub/rss/"),
        ("ML Safety",         "https://www.mlsafety.org/rss"),
        ("Yann LeCun Blog",   "https://yann.lecun.com/ex/rss.xml"),
        ("Meta AI",           "https://ai.meta.com/blog/rss/"),
        ("Yannic Kilcher",    "https://www.ykilcher.com/feed.xml"),
    ],
    # ── Nouvelle catégorie : services & plateformes ──────────────────────────
    "Services": [
        ("GitHub Changelog",  "https://github.blog/changelog/feed/"),
        ("GitHub Advisory",   "https://github.com/advisories.atom"),
        ("Docker Security",   "https://docs.docker.com/security/feed/"),
        ("Spotify Engineering","https://engineering.atspotify.com/feed/"),
        ("Google Workspace",  "https://workspace.google.com/blog/feed"),
        ("Snap Engineering",  "https://eng.snap.com/rss.xml"),
        ("Exegol (GitHub)",   "https://github.com/ThePorgs/Exegol/releases.atom"),
        ("ServiceNow Sécu",   "https://www.servicenow.com/blogs/security.rss"),
    ],
}

# ─── Mots-clés pour détecter les articles sur les services surveillés ─────────

SERVICE_KEYWORDS = [
    # État français
    "france connect", "franceconnect", "ameli", "impots.gouv", "service-public",
    "dgsi", "anssi", "dsnp", "cnil", "ministère", "gouv.fr",
    # Outils & plateformes
    "github", "docker", "spotify", "snapchat", "snap", "gmail", "google workspace",
    "exegol", "portainer", "kubernetes", "k8s", "gitlab",
]

# ─── Mots-clés deep learning ──────────────────────────────────────────────────

DL_KEYWORDS = [
    "deep learning", "neural network", "transformer", "attention mechanism",
    "jepa", "i-jepa", "v-jepa", "world model", "self-supervised",
    "diffusion model", "generative model", "foundation model",
    "reinforcement learning", "rlhf", "dpo", "ppo",
    "llm", "large language model", "vision language model", "vlm",
    "bert", "gpt", "llama", "mistral", "gemma", "phi",
    "mamba", "state space model", "ssm",
    "graph neural network", "gnn", "convnet", "cnn",
    "backpropagation", "gradient descent", "fine-tuning", "lora", "qlora",
    "mixture of experts", "moe", "sparse activation",
    "embedding", "vector database", "rag", "retrieval augmented",
    "multimodal", "clip", "dalle", "stable diffusion",
]

# ─── Prompt d'analyse ─────────────────────────────────────────────────────────

PROMPT_TEMPLATE = """Tu es un expert en cybersécurité et IA. Analyse cet article.
Réponds UNIQUEMENT avec un JSON valide, sans texte avant ou après.

{{
  "resume": "Décris : QUI est vulnérable / concerné, QUOI se passe techniquement (vecteur, mécanisme, type de vulnérabilité ou avancée technique), et QUEL est l'impact concret",
  "technique": "Pour les failles : type de vulnérabilité, condition d'exploitation, privilèges requis, interaction utilisateur. Pour le DL : architecture, méthode, benchmark. Null si non applicable.",
  "cve": "CVE-XXXX-XXXX si mentionné, sinon null",
  "cvss": "Score numérique CVSS si mentionné, sinon null",
  "versions_affectees": "Produits, versions ou modèles concernés, sinon null",
  "importance": 3,
  "categorie": "CVE|Zero-Day|Threat Intel|Ransomware|APT|Cloud Security|LLM|Deep Learning|JEPA|Open Source AI|Agent IA|Paper|Outil|Réglementation|Services|Autre",
  "technologies": ["tech1"],
  "tags": ["tag1", "tag2"],
  "actions": "Action concrète (patcher, mettre à jour, surveiller, lire le paper...) ou null"
}}

Importance : 1=info, 2=intéressant, 3=important, 4=critique, 5=alerte max (CVSS≥9, 0-day actif, breach majeur)

Titre   : {title}
Source  : {source}
Contenu : {content}"""


# ─── Fonctions utilitaires ────────────────────────────────────────────────────

def get_db():
    """Connexion Neon avec timeout pour le cold start"""
    return psycopg2.connect(DATABASE_URL, connect_timeout=15)


def url_exists(conn, url: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM articles WHERE url = %s LIMIT 1", (url,))
        return cur.fetchone() is not None


def insert_article(conn, data: dict):
    """Insertion complète avec tous les champs (technique, cve, cvss, versions)"""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO articles
                (url, title, source, category, published_at,
                 summary, importance, tags, technologies, actions,
                 raw_content, technique, cve, cvss, versions_affectees)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
            data.get("technique"),
            data.get("cve"),
            data.get("cvss"),
            data.get("versions_affectees"),
        ))
        conn.commit()


def extract_json(text: str) -> dict:
    """Extrait le JSON d'une réponse LLM même avec des artefacts"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
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
    content_list = getattr(entry, "content", [])
    if content_list:
        return content_list[0].get("value", "")
    for attr in ("summary", "description", "title"):
        val = getattr(entry, attr, "")
        if val:
            return val
    return ""


def is_service_related(title: str, content: str) -> bool:
    """Détecte si l'article concerne un service surveillé"""
    text = (title + " " + content).lower()
    return any(kw in text for kw in SERVICE_KEYWORDS)


def is_deep_learning(title: str, content: str) -> bool:
    """Détecte si l'article porte sur le deep learning"""
    text = (title + " " + content).lower()
    return any(kw in text for kw in DL_KEYWORDS)


# ─── Programme principal ──────────────────────────────────────────────────────

def main():
    print(f"\n{'='*50}")
    print(f"Démarrage collecte : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    conn = get_db()
    new_count     = 0
    error_count   = 0
    critical_alerts = []

    for domain, feeds in RSS_FEEDS.items():
        print(f"\n[{domain}]")

        for source_name, feed_url in feeds:
            try:
                feed    = feedparser.parse(feed_url, request_headers={"User-Agent": "Mozilla/5.0"})
                entries = feed.entries[:12]
                print(f"  {source_name}: {len(entries)} entrées")

                for entry in entries:
                    url   = getattr(entry, "link", None)
                    title = getattr(entry, "title", "Sans titre").strip()

                    if not url:
                        continue
                    if url_exists(conn, url):
                        continue

                    content   = get_article_content(entry)
                    published = getattr(entry, "published", None)

                    # ── Boost d'importance si service ou DL détecté ────────
                    service_flag = is_service_related(title, content)
                    dl_flag      = is_deep_learning(title, content)

                    try:
                        analysis = analyze_with_groq(title, source_name, content)
                    except Exception as e:
                        print(f"    ⚠ Groq error pour '{title[:50]}': {e}")
                        error_count += 1
                        time.sleep(GROQ_DELAY_SECONDS)
                        continue

                    # Surclasser en Services si le flag est actif
                    if service_flag and domain == "Cyber":
                        analysis.setdefault("tags", [])
                        if "service-surveillé" not in analysis["tags"]:
                            analysis["tags"].append("service-surveillé")

                    # Surclasser en Deep Learning si le flag est actif
                    if dl_flag and domain == "IA":
                        analysis.setdefault("tags", [])
                        if "deep-learning" not in analysis["tags"]:
                            analysis["tags"].append("deep-learning")
                        # Pousser la catégorie si générique
                        if analysis.get("categorie") in ("Autre", "Open Source AI", None):
                            analysis["categorie"] = "Deep Learning"

                    article_data = {
                        "url":        url,
                        "title":      title,
                        "source":     source_name,
                        "published_at": published,
                        "raw_content": content[:5000],
                        **analysis,
                    }

                    insert_article(conn, article_data)
                    new_count += 1
                    imp = analysis.get("importance", 1)
                    print(f"    ✓ [{imp}/5] {title[:60]}")

                    if imp >= 4:
                        critical_alerts.append(article_data)

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
        if alert.get("cve"):
            msg += f"\n🔖 `{alert['cve']}`"
            if alert.get("cvss"):
                msg += f" — CVSS *{alert['cvss']}*"
        if alert.get("actions"):
            msg += f"\n\n✅ *Action :* {alert['actions']}"
        msg += f"\n\n🔗 {alert['url']}"
        send_telegram(msg)

    # ── Résumé ──────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"✅ Nouveaux articles : {new_count}")
    print(f"🚨 Alertes critiques : {len(critical_alerts)}")
    print(f"⚠  Erreurs Groq     : {error_count}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
