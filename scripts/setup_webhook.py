#!/usr/bin/env python3
"""
Configure le webhook Telegram pour pointer vers ton URL Vercel.
À exécuter UNE SEULE FOIS après le déploiement.

Usage :
  TELEGRAM_BOT_TOKEN=xxx VERCEL_URL=https://veille-cyber-ia.vercel.app python scripts/setup_webhook.py
"""

import os, requests, sys

TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BASE_URL = os.environ.get("VERCEL_URL", "").rstrip("/")

if not TOKEN or not BASE_URL:
    print("❌  Variables manquantes : TELEGRAM_BOT_TOKEN et VERCEL_URL sont requis.")
    sys.exit(1)

WEBHOOK_URL = f"{BASE_URL}/api/telegram"

# ── 1. Supprimer l'ancien webhook ──────────────────────────────────────────
r = requests.post(
    f"https://api.telegram.org/bot{TOKEN}/deleteWebhook",
    json={"drop_pending_updates": True},
    timeout=10,
)
print("deleteWebhook :", r.json())

# ── 2. Enregistrer le nouveau webhook ──────────────────────────────────────
r = requests.post(
    f"https://api.telegram.org/bot{TOKEN}/setWebhook",
    json={
        "url": WEBHOOK_URL,
        "max_connections": 10,
        "allowed_updates": ["message"],
    },
    timeout=10,
)
data = r.json()
print("setWebhook    :", data)

if data.get("ok"):
    print(f"\n✅  Webhook configuré → {WEBHOOK_URL}")
else:
    print(f"\n❌  Erreur : {data.get('description')}")
    sys.exit(1)

# ── 3. Vérifier ────────────────────────────────────────────────────────────
r = requests.get(
    f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo",
    timeout=10,
)
info = r.json().get("result", {})
print("\nWebhook info :")
print(f"  URL            : {info.get('url')}")
print(f"  En attente     : {info.get('pending_update_count', 0)}")
print(f"  Dernière erreur: {info.get('last_error_message', 'aucune')}")
