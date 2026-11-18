# -*- coding: utf-8 -*-
"""
百度网盘 API 客户端。

支持两种鉴权方式：
1. 网页版 Cookie（BDUSS + STOKEN）：把浏览器里 pan.baidu.com 的 Cookie 整段贴进来即可，
   无需注册开放平台应用，开箱即用。
2. 开放平台 access_token（OAuth2）：更"正规"，但需要先去 https://pan.baidu.com/union
   注册应用并申请网盘权限。

两种方式共享同一个 list 语义，上层扫描器不关心区别。
"""
from __future__ import annotations

import gzip
import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

API_LIST_WEB = "https://pan.baidu.com/api/list"
API_LIST_XPAN = "https://pan.baidu.com/rest/2.0/xpan/file"
API_QUOTA_WEB = "https://pan.baidu.com/api/quota"
API_NAS_XPAN = "https://pan.baidu.com/rest/2.0/xpan/nas"
API_FILEMANAGER = "https://pan.baidu.com/api/filemanager"
API_LOGIN_STATUS = "https://pan.baidu.com/api/loginStatus"

# 需要用户重新登录的错误码
AUTH_ERRNOS = {-6, 4, 5, 6}
# 可以重试的临时错误码（系统繁忙 / 参数校验瞬时失败 / 请求过快）
TRANSIENT_ERRNOS = {-1, 2, 31066, 31023, 31064, 9019, 12}
# 目录不存在
NOTEXIST_ERRNOS = {-9, -7, 112}


class PanError(Exception):
    """网盘接口通用异常"""

    def __init__(self, message, errno=None):
        super().__init__(message)
        self.errno = errno


class AuthError(PanError):
    """登录态失效"""


class NotExistError(PanError):
    """路径不存在"""


def normalize_cookie(raw: str) -> str:
    """把用户粘贴的内容整理成可用 Cookie。

    允许三种输入：
      - 完整 Cookie 串：'BAIDUID=xxx; BDUSS=yyy; STOKEN=zzz'
      - 只有 BDUSS 的值
      - 'BDUSS=yyy; STOKEN=zzz'
    """
    raw = (raw or "").strip().strip("'\"")
    if not raw:
        raise AuthError("Cookie 为空")
    # 去掉可能的换行
    raw = " ".join(raw.split())
    if "BDUSS=" in raw:
        cookie = raw
    else:
        cookie = "BDUSS=" + raw
    if "BDUSS=" not in cookie:
        raise AuthError("Cookie 里没有找到 BDUSS")
    import re as _re
    m = _re.search(r"BDUSS=([^;\s]+)", cookie)
    if not m or len(m.group(1)) < 16:
        raise AuthError("BDUSS 看起来不完整（正常约 192 个字符），请回到浏览器把整段 Cookie 重新复制一次")
    return cookie


class BaiduPan:
    def __init__(self, mode="cookie", cookie="", token="", timeout=25):
        self.mode = mode
        self.cookie = normalize_cookie(cookie) if mode == "cookie" and cookie else cookie
        self.token = token
        self.timeout = timeout
        self._lock_time = 0.0
        self._login_info = None

    # ------------------------------------------------------------------ 基础请求
    def _headers(self):
        h = {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://pan.baidu.com/disk/home",
            "Connection": "keep-alive",
        }
        if self.mode == "cookie" and self.cookie:
            h["Cookie"] = self.cookie
        return h

    def _request(self, url, params, tries=4):
        query = urllib.parse.urlencode(params, safe="/")
        full = url + ("&" if "?" in url else "?") + query
        last_err = None

        for attempt in range(tries):
            try:
                req = urllib.request.Request(full, headers=self._headers())
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8", "replace"))
                if not isinstance(data, dict):
                    raise PanError("接口返回格式异常")
            except urllib.error.HTTPError as exc:
                # 尽量把接口真实错误码/文案透出来，而不是只说 HTTP 400
                payload = {}
                try:
                    payload = json.loads(exc.read().decode("utf-8", "replace"))
                except Exception:
                    payload = {}
                api_errno = payload.get("errno") if isinstance(payload, dict) else None
                api_msg = ""
                if isinstance(payload, dict):
                    api_msg = payload.get("errmsg") or payload.get("error_msg") or ""
                if api_errno in AUTH_ERRNOS:
                    raise AuthError(api_msg or "登录状态无效或已过期，请重新登录", api_errno)
                hint = {
                    400: "请求被接口拒绝，通常是 Cookie 不完整或本机网络被拦截（可尝试换网络或改用开放平台 Token）",
                    401: "身份验证失败，Cookie / Token 可能已过期",
                    403: "访问被拒绝，可能触发了网盘风控，请稍后重试",
                    429: "请求过于频繁，已被限流，请稍后再试",
                    500: "网盘服务端异常，请稍后重试",
                    502: "网盘服务暂时不可用，请稍后重试",
                    503: "网盘服务暂时不可用，请稍后重试",
                }.get(exc.code, "网络请求失败")
                last_err = PanError(
                    "HTTP %s · %s" % (exc.code, api_msg or hint), api_errno or exc.code
                )
                if exc.code in (403, 429, 500, 502, 503):
                    time.sleep(1.2 * (attempt + 1) + random.random())
                    continue
                raise last_err
            except (urllib.error.URLError, socket.timeout, TimeoutError, json.JSONDecodeError) as exc:
                reason = getattr(exc, "reason", exc)
                last_err = PanError("无法连接百度网盘（%s），请检查本机网络/代理" % reason)
                time.sleep(0.6 * (attempt + 1) + random.random() * 0.3)
                continue

            errno = data.get("errno", 0)
            if errno == 0:
                return data
            if errno in AUTH_ERRNOS:
                raise AuthError("登录状态无效或已过期，请重新粘贴 Cookie / Token", errno)
            if errno in NOTEXIST_ERRNOS:
                raise NotExistError("路径不存在或无权访问", errno)
            if errno in TRANSIENT_ERRNOS and attempt < tries - 1:
                time.sleep(0.8 * (attempt + 1) + random.random() * 0.4)
                continue
            raise PanError(data.get("errmsg") or ("接口返回 errno=%s" % errno), errno)

        raise last_err or PanError("请求失败")

    # ------------------------------------------------------------------ 目录列举
    def list_dir(self, path, page_size=1000):
        """列出目录下所有条目（自动翻页），返回归一化后的 list[dict]。"""
        if self.mode == "cookie":
            return self._list_web(path, page_size)
        return self._list_xpan(path, page_size)

    def _normalize(self, item, parent):
        name = item.get("server_filename") or item.get("filename") or ""
        if not name:
            return None
        p = item.get("path") or ""
        if not p:
            p = (parent.rstrip("/") + "/" + name) if parent != "/" else "/" + name
        isdir = 1 if (item.get("isdir") in (1, True, "1")) else 0
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        try:
            mtime = int(item.get("server_mtime") or item.get("local_mtime") or 0)
        except (TypeError, ValueError):
            mtime = 0
        return {
            "name": name,
            "path": p,
            "isdir": isdir,
            "size": 0 if isdir else size,
            "mtime": mtime,
        }

    def _list_web(self, path, page_size):
        out = []
        page = 1
        while True:
            data = self._request(
                API_LIST_WEB,
                {
                    "dir": path,
                    "order": "name",
                    "desc": 0,
                    "showempty": 0,
                    "web": 1,
                    "page": page,
                    "num": page_size,
                    "channel": "chunlei",
                    "app_id": 250528,
                    "clienttype": 0,
                },
            )
            chunk = data.get("list") or []
            for it in chunk:
                norm = self._normalize(it, path)
                if norm:
                    out.append(norm)
            if len(chunk) < page_size:
                break
            page += 1
            if page > 500:  # 50 万条目上限，防御性退出
                break
        return out

    def _list_xpan(self, path, page_size):
        out = []
        start = 0
        while True:
            data = self._request(
                API_LIST_XPAN,
                {
                    "method": "list",
                    "access_token": self.token,
                    "dir": path,
                    "start": start,
                    "limit": page_size,
                    "order": "name",
                    "web": 1,
                },
            )
            chunk = data.get("list") or []
            for it in chunk:
                norm = self._normalize(it, path)
                if norm:
                    out.append(norm)
            if len(chunk) < page_size:
                break
            start += page_size
            if start > 500000:
                break
        return out

    # ------------------------------------------------------------------ 账号信息
    def quota(self):
        """返回 {total, used, free}（字节）。"""
        if self.mode == "cookie":
            data = self._request(
                API_QUOTA_WEB, {"checkexpire": 1, "checkfree": 1, "web": 1}
            )
        else:
            data = self._request(
                API_NAS_XPAN, {"method": "quota", "access_token": self.token}
            )
        total = int(data.get("total") or 0)
        used = int(data.get("used") or 0)
        return {"total": total, "used": used, "free": max(total - used, 0)}

    def user_name(self):
        if self.mode != "token":
            return ""
        try:
            data = self._request(
                API_NAS_XPAN, {"method": "uinfo", "access_token": self.token}
            )
            return data.get("netdisk_name") or data.get("baidu_name") or ""
        except PanError:
            return ""

    # ------------------------------------------------------------------ 登录信息 / 删除
    def login_info(self):
        """返回 {bdstoken, username, vip_level, ...}，进程内缓存。"""
        if self._login_info is not None:
            return self._login_info
        if self.mode == "token":
            self._login_info = {"bdstoken": "", "username": self.user_name()}
            return self._login_info
        data = self._request(
            API_LOGIN_STATUS,
            {"clienttype": 0, "web": 1, "channel": "chunlei", "app_id": 250528},
        )
        info = data.get("login_info") or {}
        self._login_info = info
        return info

    def bdstoken(self):
        tok = (self.login_info() or {}).get("bdstoken") or ""
        if not tok:
            raise PanError("拿不到 bdstoken，无法执行删除")
        return tok

    def delete(self, paths):
        """把路径移入网盘回收站（不是永久删除）。"""
        paths = [p for p in (paths or []) if isinstance(p, str)]
        if not paths:
            raise PanError("没有要删除的路径")
        for p in paths:
            if not p.startswith("/") or p == "/" or ".." in p:
                raise PanError("非法路径：%s" % p)
        filelist = json.dumps(paths, ensure_ascii=False)

        if self.mode == "token":
            params = {
                "method": "filemanager",
                "opera": "delete",
                "async": 0,
                "access_token": self.token,
                "filelist": filelist,
                "web": 1,
            }
            return self._request(API_LIST_XPAN, params)

        params = {
            "opera": "delete",
            "async": 2,
            "onnest": "fail",
            "bdstoken": self.bdstoken(),
            "filelist": filelist,
            "channel": "chunlei",
            "web": 1,
            "app_id": 250528,
            "clienttype": 0,
            "site_referer": "https://pan.baidu.com/disk/main",
        }
        try:
            return self._request(API_FILEMANAGER, params)
        except AuthError:
            raise PanError(
                "当前登录会话没有删除权限。扫码登录拿到的凭据只够读取，"
                "请用「网页 Cookie」或开放平台 Token 方式登录后再删除"
            )

    def verify(self):
        """登录校验：能拿到容量信息即认为有效。"""
        q = self.quota()
        return q
