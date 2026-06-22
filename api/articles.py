import json, os, psycopg2
from http.server import BaseHTTPRequestHandler

DATABASE_URL = os.environ["DATABASE_URL"]

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT url, title, source, category, importance, summary, collected_at
                FROM articles
                WHERE collected_at > NOW() - INTERVAL '7 days'
                ORDER BY importance DESC, collected_at DESC
                LIMIT 60
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()

        for r in rows:
            r["collected_at"] = r["collected_at"].isoformat()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(rows).encode())

    def log_message(self, *args):
        pass