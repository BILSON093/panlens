# -*- coding: utf-8 -*-
"""本地 HTTP 服务：提供前端页面 + JSON API（含百度网盘代理）。"""
from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import demo, report
from .baidu import AuthError, BaiduPan, PanError, normalize_cookie
from .qrlogin import QrLogin
from .scanner import Scanner
from .store import Store

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "app", "web")
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")


class State:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.store = Store(os.path.join(DATA_DIR, "tree.db"))
        self.scanner = Scanner(self.store)
        self.api = None
        self.mode = ""
        self.account = ""
        self.lock = threading.Lock()
        self.qr = QrLogin(on_success=self._qr_success)
        self._load_config()
        self._probe_thread = threading.Thread(target=self._probe, daemon=True)
        self._probe_thread.start()

    # ------------------------------------------------ 配置持久化
    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            if cfg.get("mode") == "cookie" and cfg.get("cookie"):
                self.api = BaiduPan("cookie", cookie=cfg["cookie"])
                self.mode = "cookie"
            elif cfg.get("mode") == "token" and cfg.get("token"):
                self.api = BaiduPan("token", token=cfg["token"])
                self.mode = "token"
        except Exception:
            pass

    def save_config(self, mode, cookie="", token=""):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump({"mode": mode, "cookie": cookie, "token": token}, fh)
        except Exception:
            pass

    def clear_config(self):
        try:
            os.remove(CONFIG_PATH)
        except OSError:
            pass

    def _probe(self):
        """后台校验已保存的登录态，失败则清空。"""
        api = self.api
        if not api:
            return
        try:
            api.list_dir("/", page_size=1)
            info = api.login_info() or {}
            if info.get("username"):
                self.account = info["username"]
        except AuthError:
            self.api = None
            self.clear_config()
        except PanError:
            pass

    # ------------------------------------------------ 登录
    def login(self, mode, cookie="", token=""):
        if mode == "cookie":
            api = BaiduPan("cookie", cookie=normalize_cookie(cookie))
        else:
            if not token.strip():
                raise PanError("access_token 不能为空")
            api = BaiduPan("token", token=token.strip())
        api.list_dir("/", page_size=1)  # 以列表接口校验登录态
        try:
            quota = api.quota()
        except PanError:
            quota = None
        self.api = api
        self.mode = mode
        self.account = api.user_name() if mode == "token" else ""
        self.save_config(mode, cookie, token)
        return quota

    def logout(self):
        self.api = None
        self.mode = ""
        self.account = ""
        self.qr.cancel()
        self.clear_config()

    def _qr_success(self, bduss, stoken, user, extra=""):
        """扫码确认回调：用兑换到的 Cookie 建立会话并落盘。"""
        if extra:
            cookie = extra
        else:
            cookie = "BDUSS=" + bduss
            if stoken:
                cookie += "; STOKEN=" + stoken
        api = BaiduPan("cookie", cookie=cookie)
        try:
            api.list_dir("/", page_size=1)  # 校验登录态（容量接口可能不认扫码会话）
        except AuthError as exc:
            raise PanError("扫码成功但登录态校验失败：%s" % exc)
        except PanError:
            pass  # 网络抖动不阻断
        self.api = api
        self.mode = "cookie"
        self.account = user or ""
        self.save_config("cookie", cookie, "")


STATE = None  # type: State


# ---------------------------------------------------------------- 树构建
def _node(row):
    return {
        "path": row["path"],
        "name": row["name"],
        "isdir": row["isdir"],
        "size": row["size"],
        "nfiles": row["nfiles"],
        "mtime": row["mtime"],
        "cat": row["cat"],
    }


def build_tree(store, path, depth=2, cap=600):
    root_row = store.row(path)
    if not root_row:
        return None
    root = _node(root_row)
    index = {path: root}
    frontier = [path]
    for _level in range(max(1, depth)):
        dirs = [p for p in frontier if index[p]["isdir"]]
        if not dirs:
            break
        grouped = store.children_many(dirs, cap=cap)
        nxt = []
        for p in dirs:
            kids = grouped.get(p, [])
            if len(kids) >= cap:
                total = store.totals_for(p)
                got_size = sum(k["size"] for k in kids)
                got_nfiles = sum(k["nfiles"] for k in kids)
                rest = total["count"] - len(kids)
                if rest > 0:
                    kids.append(
                        {
                            "name": "其他 %d 项" % rest,
                            "path": p,
                            "isdir": True,
                            "size": max(total["size"] - got_size, 0),
                            "nfiles": max(total["nfiles"] - got_nfiles, 0),
                            "mtime": 0,
                            "cat": "other",
                            "agg": True,
                        }
                    )
            seen = set()
            children = []
            for k in kids:
                if k["path"] in seen:
                    continue
                seen.add(k["path"])
                node = _node(k) if "agg" not in k else dict(k)
                children.append(node)
                if node["isdir"] and not node.get("agg"):
                    index[node["path"]] = node
                    nxt.append(node["path"])
            index[p]["children"] = children
        frontier = nxt
    return root


class Handler(BaseHTTPRequestHandler):
    server_version = "PanSpaceAnalyzer/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ 基础
    def log_message(self, fmt, *args):  # 静默
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _serve_static(self, rel):
        rel = urllib.parse.unquote(rel).lstrip("/")
        if not rel:
            rel = "index.html"
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(full):
            self._send_text("404 Not Found", 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ 路由
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._api_get(path, qs)
            else:
                self._serve_static(path)
        except AuthError as exc:
            self._send_json({"ok": False, "auth": True, "error": str(exc)}, 401)
        except PanError as exc:
            self._send_json({"ok": False, "error": str(exc), "errno": exc.errno}, 502)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/qr/new":
                body = self._body()
                st = STATE.qr.start(force=bool(body.get("force")))
                self._send_json(dict(ok=True, **st))
            elif path == "/api/qr/cancel":
                st = STATE.qr.cancel()
                self._send_json(dict(ok=True, **st))
            elif path == "/api/login":
                body = self._body()
                quota = STATE.login(
                    body.get("mode", "cookie"),
                    cookie=body.get("cookie", ""),
                    token=body.get("token", ""),
                )
                self._send_json({"ok": True, "quota": quota, "account": STATE.account})
            elif path == "/api/logout":
                STATE.logout()
                self._send_json({"ok": True})
            elif path == "/api/scan":
                body = self._body()
                if STATE.api is None:
                    raise PanError("请先登录百度网盘")
                root = body.get("path") or "/"
                started = STATE.scanner.start(
                    STATE.api, root, int(body.get("workers") or 4)
                )
                self._send_json({"ok": True, "started": started})
            elif path == "/api/scan/cancel":
                STATE.scanner.cancel()
                self._send_json({"ok": True})
            elif path == "/api/delete":
                body = self._body()
                raw = body.get("paths") or []
                paths = [p for p in raw if isinstance(p, str) and p.startswith("/") and p != "/"]
                if not paths:
                    raise PanError("没有收到有效路径")
                if STATE.api is None:
                    raise PanError("请先登录百度网盘")
                data = STATE.api.delete(paths)
                errno = data.get("errno", 0)
                if errno not in (0,):
                    raise PanError("删除失败（errno=%s）%s" % (errno, data.get("errmsg") or ""))
                STATE.store.remove_paths(paths)
                STATE.store.rollup("/")
                self._send_json({"ok": True, "deleted": len(paths)})
            elif path == "/api/demo":
                demo.build(STATE.store)
                self._send_json({"ok": True, "path": demo.DEMO_ROOT})
            else:
                self._send_json({"ok": False, "error": "未知接口"}, 404)
        except AuthError as exc:
            self._send_json({"ok": False, "auth": True, "error": str(exc)}, 401)
        except PanError as exc:
            self._send_json({"ok": False, "error": str(exc), "errno": exc.errno}, 502)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def _api_get(self, path, qs):
        store = STATE.store
        if path == "/api/session":
            self._send_json(
                {
                    "ok": True,
                    "loggedIn": STATE.api is not None,
                    "mode": STATE.mode,
                    "account": STATE.account,
                    "hasData": store.has_data(),
                    "scanned": (store.row("/") or {}).get("size", 0),
                    "demoPath": demo.DEMO_ROOT,
                }
            )
        elif path == "/api/qr/status":
            st = STATE.qr.state()
            self._send_json(dict(ok=True, **st))
        elif path == "/api/quota":
            if STATE.api is None:
                self._send_json({"ok": False, "error": "未登录"}, 401)
                return
            self._send_json({"ok": True, "quota": STATE.api.quota()})
        elif path == "/api/children":
            p = (qs.get("path") or ["/"])[0] or "/"
            depth = int((qs.get("depth") or ["2"])[0])
            node = build_tree(store, p, depth=min(max(depth, 1), 4))
            if node is None:
                self._send_json({"ok": True, "node": None, "empty": True})
            else:
                self._send_json({"ok": True, "node": node})
        elif path == "/api/scan/status":
            self._send_json({"ok": True, "status": STATE.scanner.status()})
        elif path == "/api/top":
            limit = int((qs.get("limit") or ["30"])[0])
            root = (qs.get("path") or [""])[0] or None
            if root and root == "/":
                root = "/"
            self._send_json({"ok": True, "items": store.top_files(limit, root)})
        elif path == "/api/kinds":
            p = (qs.get("path") or ["/"])[0] or "/"
            self._send_json({"ok": True, "items": store.kinds(p)})
        elif path == "/api/search":
            q = (qs.get("q") or [""])[0].strip()
            if not q:
                self._send_json({"ok": True, "items": []})
            else:
                self._send_json({"ok": True, "items": store.search(q, 150)})
        elif path == "/api/export":
            p = (qs.get("path") or ["/"])[0] or "/"
            fmt = (qs.get("fmt") or ["csv"])[0].lower()
            if fmt == "html":
                self._export_html(p)
            else:
                self._export_csv(p)
        else:
            self._send_json({"ok": False, "error": "未知接口"}, 404)

    def _export_html(self, root):
        """导出自包含的 HTML 空间报告。"""
        body = report.build(STATE.store, root, depth=4, per_level=30)
        data = body.encode("utf-8")
        name = root.rstrip("/").split("/")[-1] or "root"
        fname = urllib.parse.quote(name + "-空间报告.html")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + fname)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _export_csv(self, root):
        import csv
        import tempfile

        name = root.rstrip("/").split("/")[-1] or "root"
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8-sig", newline=""
        )
        writer = csv.writer(tmp)
        writer.writerow(["路径", "类型", "大小(字节)", "大小", "修改时间", "分类"])
        for p, isdir, size, mtime, cat in STATE.store.iter_files(root):
            writer.writerow(
                [
                    p,
                    "文件夹" if isdir else "文件",
                    size,
                    human(size),
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else "",
                    cat,
                ]
            )
        tmp.close()
        with open(tmp.name, "rb") as fh:
            data = fh.read()
        os.unlink(tmp.name)
        fname = urllib.parse.quote(name + "-空间清单.csv")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + fname)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def human(n):
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return ("%.2f %s" % (f, u)) if u != "B" else ("%d B" % f)
        f /= 1024
    return "%.2f PB" % f


def serve(host="127.0.0.1", port=8765):
    global STATE
    STATE = State()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print("  PanLens 已启动 -> http://%s:%d" % (host, port))
    print("  按 Ctrl+C 停止")
    return httpd
