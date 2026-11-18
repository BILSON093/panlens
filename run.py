# -*- coding: utf-8 -*-
"""启动入口：python run.py"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser

from app.server import serve


def port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def main():
    ap = argparse.ArgumentParser(description="PanLens - 百度网盘空间分析器")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    port = args.port
    for _ in range(30):
        if port_free(args.host, port):
            break
        port += 1

    httpd = serve(args.host, port)
    url = "http://%s:%d/" % (args.host, port)
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已退出")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main() or 0)
