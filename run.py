# -*- coding: utf-8 -*-
"""启动入口：python run.py"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
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


def running_instance(host, port):
    """该端口上是否已经跑着本程序。"""
    try:
        with urllib.request.urlopen(
            "http://%s:%d/api/session" % (host, port), timeout=2
        ) as r:
            return r.status == 200
    except Exception:
        return False


def open_browser(url):
    """尽量把浏览器打开，逐级兜底。"""
    if sys.platform.startswith("win"):
        try:
            os.startfile(url)  # Windows 上最可靠
            return True
        except Exception:
            pass
    try:
        if webbrowser.open(url, new=2):
            return True
    except Exception:
        pass
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen('start "" "%s"' % url, shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="PanLens - 百度网盘空间分析器")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    # 已经有一个实例在跑：直接开浏览器，避免又起一个占用新端口
    if running_instance(args.host, args.port):
        url = "http://%s:%d/" % (args.host, args.port)
        print("  检测到 PanLens 已在运行：%s" % url)
        if not args.no_browser:
            open_browser(url)
        return 0

    port = args.port
    for _ in range(30):
        if port_free(args.host, port):
            break
        port += 1

    httpd = serve(args.host, port)
    url = "http://%s:%d/" % (args.host, port)

    if not args.no_browser:

        def go():
            time.sleep(0.8)  # 等 serve_forever 起来
            if not open_browser(url):
                print("  没能自动打开浏览器，请手动访问：%s" % url)

        threading.Thread(target=go, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已退出")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main() or 0)
