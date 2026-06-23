# Veille Cyber & IA — Mode d'emploi

Stack de veille automatisée en cybersécurité et intelligence artificielle.

---

## Fonctionnement

Toutes les 4 heures, le système lit une vingtaine de flux RSS spécialisés
(CISA, BleepingComputer, arXiv, HuggingFace…), envoie chaque nouvel article à
un LLM qui le résume et lui attribue un score d'importance de 1 à 5, puis
stocke tout en base. 
Si un article dépasse 3/5, envoi d'une alerte sur Telegram. 
Chaque matin à 7h, un digest récapitulatif de la nuit est envoyé.

---

## Accès

| Interface | URL / Contact | Usage |
|---|---|---|
| Bot Telegram | `@veille_cyber_ia_bot` | Alertes, digest, questions |
| Dashboard web | `veille-cyber-ia.vercel.app` | Vue globale des articles |

---

## Bot Telegram — Commandes disponibles

### `/critiques`
Affiche les articles avec un score d'importance ≥ 4 sur les 7 derniers jours.

```
/critiques
```

Exemple de réponse :
```
Critiques — 7 derniers jours

• [5/5] CVE-2026-XXXX : RCE critique dans Apache HTTP Server — NVD
  Vulnérabilité d'exécution de code à distance exploitée activement...

• [4/5] Nouvelle campagne APT ciblant le secteur énergie — Securelist
  Groupe APT28 détecté avec un loader inédit...
```

---

### `/search [terme]`
Recherche un sujet spécifique dans les articles des 30 derniers jours.
Fonctionne sur le titre et le résumé.

```
/search ransomware
/search CVE Apache
/search agents IA
/search kubernetes
```

---

### `/stats`
Donne un aperçu rapide de l'état de la base.

```
/stats
```

Réponse :
```
Stats de la base

Total articles : 847
Critiques (≥4) : 23
Aujourd'hui    : 41
```

---

### Questions libres (sans commande)

Bot cherche dans les articles collectés et répond avec une explication technique complète générée par le LLM.

**Exemples de questions :**

```
Explique-moi la faille Apache dont tu as parlé hier
```
```
Comment fonctionne l'attaque par buffer overflow mentionnée cette semaine ?
```
```
Quels sont les risques concrets de cette CVE Kubernetes ?
```
```
C'est quoi un RAG attack sur un modèle LLM ?
```
```
Résume les nouveautés sur les agents IA du mois
```

Le bot répond avec :
- le type de vulnérabilité
- comment elle fonctionne techniquement
- les conditions d'exploitation (privilèges requis, accès réseau, interaction utilisateur)
- les versions affectées si disponibles
- les actions concrètes à prendre (patch, isolation, contournement)

> Si le bot répond "aucun article trouvé"
> utiliser `/search` d'abord pour vérifier ce qui est en base.

---

## Alertes automatiques


### Alerte immédiate (score 4 ou 5)
Envoyée dès qu'un article critique est détecté, à n'importe quelle heure.
Format :

```
🚨 Alerte Veille — 5/5

[Titre de l'article]
[Source]

[Résumé en 3-5 phrases]

📁 `CVE`
✅ Action : Appliquer le patch X.X.X immédiatement

🔗 https://...
```

### Digest quotidien (7h du matin)
Récap de toutes les dernières 24h, avec :
- une synthèse narrative générée par le LLM
- les articles critiques détaillés
- les articles importants en liste
- les infos générales

---

## Dashboard web

Accessible sur `veille-cyber-ia.vercel.app` depuis n'importe quel navigateur,
y compris mobile.

**Filtres disponibles :**
- **Tout** — les 60 articles les plus récents (7 derniers jours)
- **Critiques** — uniquement importance ≥ 4
- **Cyber** — articles catégorie cybersécurité
- **IA** — articles catégorie intelligence artificielle
- **CVE** — uniquement les CVE

Chaque carte affiche le titre (cliquable vers l'article source), le résumé,
la source, la catégorie et le score d'importance. La bordure gauche change de
couleur selon le score : rouge (5), orange (4), violet (3).

---

## Scores d'importance

| Score | Signification | Que faire |
|---|---|---|
| 1/5 | Info générale | Rien d'urgent |
| 2/5 | Sujet intéressant | À garder en tête |
| 3/5 | Important | Lire dans la journée |
| 4/5 | Critique | Agir dans les 24h |
| 5/5 | Alerte maximale | Agir immédiatement |

Un score 5 correspond à une CVE CVSS ≥ 9, un 0-day exploité activement,
ou une campagne APT ciblant des infrastructures critiques.

---

## Workflows automatiques

| Workflow | Fréquence | Ce qu'il fait |
|---|---|---|
| `collecte.yml` | Toutes les 4h | Lit les flux RSS, analyse avec Groq, stocke en base, envoie les alertes critiques |
| `digest.yml` | Tous les jours à 7h | Génère et envoie le résumé quotidien sur Telegram |

---

## Sources surveillées

### Cybersécurité
CISA · NVD (CVE) · The Hacker News · BleepingComputer · SANS ISC ·
Krebs on Security · Microsoft Security Blog · Google Project Zero ·
Rapid7 · Securelist (Kaspersky)

### Intelligence Artificielle
Anthropic · OpenAI · Hugging Face · DeepMind · arXiv cs.AI ·
arXiv cs.CR · Papers With Code · AI News

---

## Stack technique

| Composant | Service | Coût |
|---|---|---|
| Orchestration | GitHub Actions | Gratuit |
| LLM analyse | Groq API (llama-3.1-8b) | Gratuit |
| LLM digest | Groq API (llama-3.3-70b) | Gratuit |
| Base de données | Neon PostgreSQL + pgvector | Gratuit |
| Notifications | Telegram Bot API | Gratuit |
| Dashboard + bot web | Vercel | Gratuit |
