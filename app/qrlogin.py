# -*- coding: utf-8 -*-
"""百度账号扫码登录（passport 二维码通道）。

真实接口语义（2026-11 抓包校准）：
    GET /v2/api/getqrcode            -> {sign, imgurl}
    GET /v2/api/qrcode?sign=...      -> 二维码 PNG
    GET /channel/unicast?channel_id=<sign>&callback=cb     [长轮询，约 30s 一次]
        {"errno":1}                                          还没扫码
        {"errno":0,"channel_v":"{\"status\":1}"}              已扫码，等待手机确认
        {"errno":0,"channel_v":"{\"status\":0,\"v\":\"...\"}"} 已确认，v = 临时 BDUSS
    GET /v3/login/main/qrbdusslogin?bduss=<临时BDUSS>       用临时 BDUSS 换正式 Cookie
        -> Set-Cookie 里带 BDUSS / STOKEN

注意 status 的取值容易搞反：0 = 已确认，非 0 = 仅扫码未确认。
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REFERER = "https://pan.baidu.com/"

QR_NEW = "https://passport.baidu.com/v2/api/getqrcode"
QR_IMG = "https://passport.baidu.com/v2/api/qrcode"
QR_POLL = "https://passport.baidu.com/channel/unicast"
QR_EXCHANGE = "https://passport.baidu.com/v3/login/main/qrbdusslogin"

QR_TTL = 300          # 二维码最长等待秒数
REUSE_TTL = 240       # 复用同一张二维码的时间窗口（多标签页共用）


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _grab(text, key):
    """从非严格 JSON / JS 对象文本里抠出字段值。"""
    m = re.search(r"['\"]%s['\"]\s*:\s*['\"]([^'\"]+)['\"]" % re.escape(key), text)
    return m.group(1) if m else ""


class QrLogin:
    """一个实例同时只维护一个有效二维码会话（多标签页共用）。"""

    def __init__(self, on_success=None, log_dir="data"):
        self.on_success = on_success
        self.log_dir = log_dir
        self.lock = threading.RLock()
        self.token = 0
        self.sign = ""
        self.gid = ""
        self.img = ""
        self.status = "idle"  # idle|loading|waiting|scanned|confirmed|expired|error|cancelled
        self.hint = ""
        self.user = ""
        self.began = 0.0
        self.polls = 0
        self.last_poll = 0.0
        self.finishing = False
        self.alive = False
        self.last_raw = ""

    # ------------------------------------------------------------ 日志
    def _log(self, msg):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            path = os.path.join(self.log_dir, "qr.log")
            if os.path.exists(path) and os.path.getsize(path) > 256 * 1024:
                open(path, "w", encoding="utf-8").close()
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("%s %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
        except Exception:
            pass

    # ------------------------------------------------------------ 状态
    def _ensure_alive(self):
        with self.lock:
            if self.status in ("waiting", "scanned") and not self.alive and self.sign:
                self.alive = True
                token, sign, gid = self.token, self.sign, self.gid
            else:
                return
        self._log("poll revive sign=%s" % sign)
        threading.Thread(target=self._poll_loop, args=(token, sign, gid), daemon=True).start()

    def state(self):
        self._ensure_alive()
        with self.lock:
            return {
                "status": self.status,
                "hint": self.hint,
                "img": self.img,
                "user": self.user,
                "polls": self.polls,
                "alive": self.alive,
                "age": round(time.time() - self.began, 1) if self.began else 0,
            }

    def _set(self, token, **kw):
        with self.lock:
            if token != self.token:
                return False
            for k, v in kw.items():
                setattr(self, k, v)
            return True

    # ------------------------------------------------------------ HTTP
    @staticmethod
    def _fetch(url, params, timeout=45):
        qs = urllib.parse.urlencode(params, safe="")
        req = urllib.request.Request(
            url + "?" + qs,
            headers={
                "User-Agent": UA,
                "Referer": REFERER,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _json(self, url, params, timeout=45):
        raw = self._fetch(url, params, timeout).decode("utf-8", "replace").strip()
        # 兼容 JSONP：cb({"errno":1})
        if raw and not raw.startswith("{"):
            i, j = raw.find("("), raw.rfind(")")
            if 0 <= i < j:
                raw = raw[i + 1:j]
        return json.loads(raw)

    # ------------------------------------------------------------ 生成二维码
    def start(self, force=False):
        """生成（或复用）二维码。force=True 时强制换一张。"""
        with self.lock:
            reuse = bool(
                not force
                and self.img
                and self.status in ("waiting", "scanned")
                and self.began
                and time.time() - self.began < REUSE_TTL
            )
        if reuse:
            # 注意：不能在持锁时调用 state()（会导致自死锁）
            return self.state()

        with self.lock:
            self.token += 1
            token = self.token
            self.status = "loading"
            self.hint = "正在生成二维码…"
            self.img = ""
            self.user = ""
            self.sign = ""
            self.polls = 0
            self.alive = False
            self.finishing = False
            self.last_raw = ""
            self.gid = str(uuid.uuid4()).upper()
            self.began = time.time()
            gid = self.gid

        try:
            ts = int(time.time() * 1000)
            data = self._json(
                QR_NEW,
                {
                    "lp": "pc",
                    "qrloginfrom": "pc",
                    "gid": gid,
                    "apiver": "v3",
                    "tpl": "netdisk",
                    "tt": ts,
                    "_": ts,
                },
                timeout=20,
            )
            errno = data.get("errno", 0)
            if errno not in (0, None):
                raise RuntimeError("接口返回 errno=%s" % errno)
            sign = data.get("sign") or ""
            if not sign:
                raise RuntimeError("没有拿到二维码 sign")
            img = self._fetch(
                QR_IMG, {"sign": sign, "lp": "pc", "qrloginfrom": "pc"}, timeout=20
            )
            with self.lock:
                if token != self.token:
                    return self.state()
                self.sign = sign
                self.img = "data:image/png;base64," + base64.b64encode(img).decode()
                self.status = "waiting"
                self.hint = "请用百度 APP 扫描二维码"
            self._log("new sign=%s gid=%s" % (sign, gid))
        except Exception as exc:  # noqa: BLE001
            self._set(token, status="error", hint="二维码生成失败：%s" % exc)
            self._log("new FAILED %r" % exc)
            return self.state()

        with self.lock:
            if token == self.token:
                self.alive = True
        threading.Thread(
            target=self._poll_loop, args=(token, sign, gid), daemon=True
        ).start()
        return self.state()

    def cancel(self):
        with self.lock:
            self.token += 1
            self.status = "idle"
            self.hint = ""
            self.alive = False
        return self.state()

    # ------------------------------------------------------------ 长轮询
    def _poll_loop(self, token, sign, gid):
        deadline = time.time() + QR_TTL
        with self.lock:
            if token == self.token:
                self.alive = True
        self._log("poll start sign=%s" % sign)
        while True:
            with self.lock:
                if token != self.token:
                    # 已被新会话取代：不要动 alive，否则会误伤新线程
                    self._log("poll exit: superseded")
                    return
                if self.status in ("confirmed", "expired", "error", "cancelled"):
                    self.alive = False
                    return
            if time.time() > deadline:
                self._set(token, status="expired", hint="二维码已超时，请刷新")
                self._log("poll exit: ttl")
                return

            try:
                data = self._json(
                    QR_POLL,
                    {
                        "channel_id": sign,
                        "tpl": "netdisk",
                        "gid": gid,
                        "apiver": "v3",
                        "tt": int(time.time() * 1000),
                        "callback": "cb",
                        "_": int(time.time() * 1000),
                    },
                    timeout=45,
                )
            except Exception as exc:  # noqa: BLE001
                self._log("poll err %r" % exc)
                time.sleep(1.2)
                continue

            with self.lock:
                if token != self.token:
                    continue
                self.polls += 1
                self.last_poll = time.time()

            errno = data.get("errno", 0)
            raw = json.dumps(data, ensure_ascii=False)[:200]
            with self.lock:
                self.last_raw = raw

            if errno == 1:  # 无事件（长轮询超时）
                continue
            if errno != 0:
                self._log("poll errno=%s raw=%s" % (errno, raw))
                time.sleep(1.0)
                continue

            cv = data.get("channel_v") or {}
            if isinstance(cv, str):
                text = cv.strip()
                if text[:3].lower() == "%7b":
                    text = urllib.parse.unquote(text)
                try:
                    cv = json.loads(text)
                except Exception:
                    cv = {}
            status = cv.get("status")
            try:
                status = int(status)
            except (TypeError, ValueError):
                status = None

            if status == 0:  # 已确认，v 是临时 BDUSS
                with self.lock:
                    if self.finishing:
                        return
                    self.finishing = True
                temp = (cv.get("v") or "").strip()
                user = (cv.get("u") or "").strip()
                if not temp:
                    self._log("confirmed but empty v: %s" % raw)
                    self._set(token, status="error", hint="确认成功但没拿到凭证，请改用 Cookie 登录")
                    return
                self._set(token, status="scanned", hint="已确认，正在获取登录凭证…", user=user)
                self._log("confirmed v_len=%d raw=%s" % (len(temp), raw))
                self._finish(token, temp, user)
                return

            # status 非 0：已扫码，等待手机确认
            self._set(token, status="scanned", hint="已扫码，请在手机上点击「确认登录」")
            self._log("scanned raw=%s" % raw)

    # ------------------------------------------------------------ 兑换凭证
    def _finish(self, token, temp_bduss, user):
        try:
            bduss, stoken, extra = self._exchange(temp_bduss)
        except Exception as exc:  # noqa: BLE001
            self._log("exchange fail %r" % exc)
            bduss, stoken, extra = "", "", ""

        if not bduss:
            # 兜底：临时 BDUSS 本身通常也能直接用于 /api/list
            bduss, stoken, extra = temp_bduss, "", ""

        try:
            if self.on_success:
                self.on_success(bduss, stoken, user, extra)
        except Exception as exc:  # noqa: BLE001
            self._log("on_success fail %r" % exc)
            self._set(token, status="error", hint="登录凭证无效：%s" % exc)
            return

        self._set(token, status="confirmed", hint="登录成功", user=user or "")
        self._log("login OK bduss_len=%d stoken=%s" % (len(bduss), "yes" if stoken else "no"))

    def _exchange(self, temp_bduss):
        """用临时 BDUSS 换正式 Cookie，返回 (bduss, stoken)。"""
        params = {
            "bduss": temp_bduss,
            "u": "https://pan.baidu.com/",
            "tpl": "netdisk",
            "v": int(time.time() * 1000),
            "tt": int(time.time() * 1000),
            "clientfrom": "",
            "bdusstoken": "",
            "channel_id": "",
            "stoken": "",
        }
        url = QR_EXCHANGE + "?" + urllib.parse.urlencode(params, safe="")
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Referer": REFERER, "Accept": "*/*"}
        )
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            resp = opener.open(req, timeout=20)
            headers, body = resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            headers, body = exc.headers, exc.read()

        cookies = {}
        for line in headers.get_all("Set-Cookie") or []:
            head = line.split(";")[0]
            if "=" in head:
                k, v = head.split("=", 1)
                k, v = k.strip(), v.strip()
                if k in ("PASSID", "UBI", "UBI_BFESS"):  # 与网盘无关，丢掉
                    continue
                cookies[k] = v

        text = body.decode("utf-8", "replace") if body else ""
        bduss = cookies.get("BDUSS", "") or _grab(text, "bduss")
        stoken = cookies.get("STOKEN", "") or _grab(text, "stoken")
        # 组装尽量完整的 Cookie，部分网页接口（如 /api/quota）对字段敏感
        order = ["BDUSS", "STOKEN", "PTOKEN", "BAIDUID", "BAIDUID_BFESS"]
        parts = []
        for k in order:
            if cookies.get(k):
                parts.append("%s=%s" % (k, cookies[k]))
        for k, v in cookies.items():
            if k not in order:
                parts.append("%s=%s" % (k, v))
        extra = "; ".join(parts)
        self._log(
            "exchange code=%s bduss=%s stoken=%s"
            % (_grab(text, "code") or cookies.get("code", "?"), "ok" if bduss else "-",
               "ok" if stoken else "-")
        )
        return bduss, stoken, extra
