import http.server
import json
import os
import socketserver
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

HTML_TEMPLATE = ROOT / "index.html"
APP_JS = ROOT / "app.js"
DATA_JS = ROOT / "data.js"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 run_visualizer.py results.json")
        sys.exit(1)

    data_path = Path(sys.argv[1]).resolve()
    if not data_path.exists():
        print(f"Input file not found: {data_path}")
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("window.TRACE_DATA = ")
        json.dump(data, f, ensure_ascii=False)
        f.write(";\n")

    os.chdir(ROOT)
    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        url = f"http://127.0.0.1:{port}/index.html"
        print(f"Serving visualizer at {url}")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        httpd.serve_forever()


if __name__ == "__main__":
    main()
