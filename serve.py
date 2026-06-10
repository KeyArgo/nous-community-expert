#!/usr/bin/env python3
"""Local dev server for Nous Community Expert (static files only)."""
import http.server
import os
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
os.chdir(os.path.dirname(os.path.abspath(__file__)))

class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # CORS for local dev
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def log_message(self, format, *args):
        pass  # Silence logs

print(f"Serving on http://localhost:{PORT}")
http.server.HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
