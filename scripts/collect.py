#!/usr/bin/env python3
"""
Collecteur de veille Cyber & IA
Exécuté par GitHub Actions toutes les 4 heures
"""

import os, json, time, re, socket
import feedparser
import psycopg2
import requests
from datetime import datetime, timezone

socket.setdefaulttimeout(15)  # Empêche un flux RSS muet de bloquer le script indéfiniment

# ─── Configuration ────────────────────────────────────────────────────────────

GROQ_API_KEY         = os.environ["GROQ_API_KEY"]
DATABASE_URL         = os.environ["DATABASE_URL"]
TELEGRAM_BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]

GROQ_MODEL_FAST      = "qwen/qwen3.8-27b"  # Free tier : 30 RPM, 1K RPD, 8 000 TPM, 2 000 000 TPD (vs 200K pour gpt-oss-20b)
GROQ_URL             = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DELAY_SECONDS   = 15.0  # Respect du TPM (8 000 tokens/min), pas seulement du RPM
GROQ_MAX_TOKENS      = 600

MAX_ARTICLES_PER_RUN = 60  # Sécurité : ~15 min avec le délai de 15s, sous le timeout du workflow (30 min)
QUOTA_PAR_DOMAINE = {
    "Cyber":    28,
    "IA":       28,
    "Services":  4,
}

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
        # ── Labs officiels ───────────────────────────────────────────────────
        ("OpenAI News",        "https://openai.com/news/rss.xml"),
        ("Anthropic News",     "https://rsshub.bestblogs.dev/anthropic/news"),
        ("Anthropic Research", "https://rsshub.bestblogs.dev/anthropic/research"),
        ("Google DeepMind",    "https://deepmind.google/blog/feed/basic/"),
        ("Google Research",    "https://research.google/blog/rss/"),
        ("Meta AI",            "https://ai.meta.com/blog/rss/"),
        ("Mistral AI",         "https://mistral.ai/news/rss/"),
        ("Microsoft Research", "https://www.microsoft.com/en-us/research/feed/"),
        ("Apple ML Research",  "https://machinelearning.apple.com/rss.xml"),
        ("HuggingFace Blog",   "https://huggingface.co/blog/feed.xml"),
        ("HuggingFace Papers", "https://huggingface.co/papers/rss.xml"),

        # ── arXiv — URLs corrigées ───────────────────────────────────────────
        ("arXiv cs.AI",        "https://rss.arxiv.org/rss/cs.AI"),
        ("arXiv cs.LG",        "https://rss.arxiv.org/rss/cs.LG"),
        ("arXiv cs.CL",        "https://rss.arxiv.org/rss/cs.CL"),
        ("arXiv cs.CV",        "https://rss.arxiv.org/rss/cs.CV"),
        ("arXiv cs.CR",        "https://rss.arxiv.org/rss/cs.CR"),

        # ── Actu IA généraliste ──────────────────────────────────────────────
        ("The Verge AI",       "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml"),
        ("TechCrunch AI",      "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("VentureBeat AI",     "https://venturebeat.com/category/ai/feed/"),
        ("MIT Tech Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
        ("AI News",            "https://www.artificialintelligence-news.com/feed/"),

        # ── Blogs techniques influents ───────────────────────────────────────
        ("Simon Willison",     "https://simonwillison.net/atom/everything/"),
        ("Latent Space",       "https://www.latent.space/feed"),
        ("The Gradient",       "https://thegradient.pub/rss/"),
        ("Papers With Code",   "https://paperswithcode.com/latest/rss"),
        ("Distill.pub",        "https://distill.pub/rss.xml"),
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

PROMPT_TEMPLATE = """Réponds UNIQUEMENT avec le JSON ci-dessous, sans texte avant ou après, sans markdown, sans explication. Le JSON doit être complet et fermé.
Tu es un expert en cybersécurité et IA. Analyse cet article.
Réponds UNIQUEMENT avec un JSON valide, sans texte avant ou après.

{{
  "resume": "Décris en 2-3 phrases MAX : QUI est vulnérable / concerné, QUOI se passe techniquement (vecteur, mécanisme, type de vulnérabilité ou avancée technique), et QUEL est l'impact concret",
  "technique": "Pour les failles : type de vulnérabilité, condition d'exploitation, privilèges requis, interaction utilisateur. Pour le DL : architecture, méthode, benchmark. Sois concis (1-2 phrases). Null si non applicable.",
  "cve": "CVE-XXXX-XXXX si mentionné, sinon null",
  "cvss": "Score numérique CVSS si mentionné, sinon null",
  "versions_affectees": "Produits, versions ou modèles concernés, sinon null",
  "importance": 3,
  "categorie": "CVE|Zero-Day|Threat Intel|Ransomware|APT|Cloud Security|LLM|Deep Learning|JEPA|Open Source AI|Agent IA|Paper|Outil|Réglementation|Services|Autre",
  "technologies": ["tech1"],
  "tags": ["tag1", "tag2"],
  "actions": "Action concrète et courte (patcher, mettre à jour, surveiller, lire le paper...) ou null"
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


class QuotaExhausted(Exception):
    """Levée quand le quota journalier (RPD/TPD) Groq est épuisé — inutile de retenter dans ce run."""
    pass


def _parse_reset_duration(s: str):
    """Parse une durée type '2m59.56s', '45s', '1h2m3s' renvoyée par Groq → secondes (float)."""
    if not s:
        return None
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", s.strip())
    if not m:
        return None
    h, mn, sec = m.groups()
    total = 0.0
    if h:
        total += int(h) * 3600
    if mn:
        total += int(mn) * 60
    if sec:
        total += float(sec)
    return total if total > 0 else None


def _call_groq(prompt: str):
    """Un seul appel HTTP à Groq, retourne l'objet response brut"""
    return requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL_FAST,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": GROQ_MAX_TOKENS,
            "temperature": 0.1,
        },
        timeout=30,
    )


def analyze_with_groq(title: str, source: str, content: str) -> dict:
    """Appelle Groq pour analyser un article, avec retry sur 429 et détection de troncature.

    Distingue un simple dépassement TPM/RPM (courte fenêtre, ~1 min) d'un épuisement
    du quota journalier RPD/TPD (fenêtre longue) : dans le second cas, on lève
    QuotaExhausted pour que main() arrête le run au lieu de marteler l'API pendant 10 min.
    """
    prompt = PROMPT_TEMPLATE.format(
        title=title,
        source=source,
        content=content[:1500],
    )

    response = _call_groq(prompt)

    if response.status_code == 429:
        # Retry-After côté HTTP standard, sinon en-têtes spécifiques Groq
        wait_req = _parse_reset_duration(response.headers.get("x-ratelimit-reset-requests"))
        wait_tok = _parse_reset_duration(response.headers.get("x-ratelimit-reset-tokens"))
        retry_after = wait_req or wait_tok or float(response.headers.get("retry-after", 20))

        # > 90s : c'est presque certainement le quota journalier (RPD/TPD), pas le TPM/RPM.
        # Retenter ne sert à rien dans le budget de ce run (timeout GitHub Actions = 30 min).
        if retry_after > 90:
            raise QuotaExhausted(
                f"Quota Groq épuisé (reset dans {retry_after:.0f}s ≈ {retry_after/60:.1f} min) — "
                f"probablement le quota journalier RPD/TPD, pas juste le TPM."
            )

        print(f"    ⏳ Rate limit (TPM/RPM), attente {retry_after:.0f}s...")
        time.sleep(retry_after)
        response = _call_groq(prompt)  # un seul retry

        if response.status_code == 429:
            # Toujours bloqué après le retry → on considère aussi que c'est le quota journalier
            raise QuotaExhausted("Rate limit persistant après retry — quota journalier probable.")

    response.raise_for_status()
    data = response.json()

    choice = data["choices"][0]
    finish_reason = choice.get("finish_reason")
    raw = choice["message"]["content"]

    if not raw or not raw.strip():
        raise ValueError("Réponse vide de Groq")

    if finish_reason == "length":
        raise ValueError("Réponse tronquée par max_tokens (finish_reason=length)")

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
    new_count             = 0
    error_count           = 0
    critical_alerts       = []
    quota_exhausted       = False

    max_par_domaine = MAX_ARTICLES_PER_RUN // len(RSS_FEEDS)
    print(f"Quota : {max_par_domaine} articles max par domaine ({', '.join(RSS_FEEDS.keys())})")

    for domain, feeds in RSS_FEEDS.items():
        if quota_exhausted:
            break

        print(f"\n[{domain}]")
        articles_traites_domaine = 0

        for source_name, feed_url in feeds:
            if quota_exhausted or articles_traites_domaine >= max_par_domaine:
                if articles_traites_domaine >= max_par_domaine:
                    print(f"  ⏸ Quota du domaine {domain} atteint ({max_par_domaine}), sources restantes ignorées.")
                break

            try:
                feed    = feedparser.parse(feed_url, request_headers={"User-Agent": "Mozilla/5.0"})
                limit   = 20 if "arxiv" in feed_url.lower() else 12
                entries = feed.entries[:limit]
                print(f"  {source_name}: {len(entries)} entrées")

                for entry in entries:
                    if quota_exhausted or articles_traites_domaine >= max_par_domaine:
                        break

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
                    except QuotaExhausted as e:
                        print(f"    🛑 {e}")
                        print(f"    🛑 Arrêt du run — quota Groq épuisé, inutile de continuer.")
                        quota_exhausted = True
                        break
                    except Exception as e:
                        print(f"    ⚠ Groq error pour '{title[:50]}': {e}")
                        error_count += 1
                        time.sleep(GROQ_DELAY_SECONDS)
                        continue

                    # Boost minimum pour les papers arXiv (souvent sous-notés)
                    if source_name.startswith("arXiv") and analysis.get("importance", 1) < 3:
                        analysis["importance"] = 3

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
                    articles_traites_domaine += 1
                    imp = analysis.get("importance", 1)
                    print(f"    ✓ [{imp}/5] {title[:60]}")

                    if imp >= 4:
                        critical_alerts.append(article_data)

                    time.sleep(GROQ_DELAY_SECONDS)

            except Exception as e:
                print(f"  ✗ Erreur flux {source_name}: {e}")
                continue

            if quota_exhausted:
                break

    if quota_exhausted:
        print(f"\n{'='*50}")
        print("⚠️  Run interrompu : quota Groq journalier probablement épuisé.")
        print("   Vérifie https://console.groq.com/settings/limits pour confirmer.")
        print(f"{'='*50}")

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
