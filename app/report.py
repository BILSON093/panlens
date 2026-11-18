# -*- coding: utf-8 -*-
"""导出 HTML 空间分析报告。

生成一个**完全自包含**的静态 HTML：样式内联、无外链、无脚本，
可以直接双击打开、发给别人，或者用浏览器打印成 PDF。
"""
from __future__ import annotations

import html
import time

CAT = {
    "video": {"c": "#c9524a", "n": "视频"},
    "image": {"c": "#d99a3c", "n": "图片"},
    "audio": {"c": "#9a6fd0", "n": "音频"},
    "doc": {"c": "#3f83c4", "n": "文档"},
    "archive": {"c": "#2f9e7d", "n": "压缩包"},
    "code": {"c": "#5aa84f", "n": "代码"},
    "app": {"c": "#b25f8a", "n": "应用"},
    "other": {"c": "#5c6b7f", "n": "其他"},
}

CSS = """
:root{--bg:#f6f8fb;--card:#fff;--line:#e3e9f2;--ink:#1b2430;--dim:#64809f;--accent:#2f6fb5}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 24px 64px}
h1{font-size:22px;margin:0 0 6px;letter-spacing:.2px}
h2{font-size:15px;margin:34px 0 12px;padding-left:9px;border-left:3px solid var(--accent);
  font-weight:600}
.meta{color:var(--dim);font-size:12.5px;word-break:break-all}
.cards{display:flex;gap:14px;margin:22px 0 0;flex-wrap:wrap}
.card{flex:1 1 180px;background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px}
.card .k{font-size:12px;color:var(--dim);margin-bottom:4px}
.card .v{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.card .s{font-size:12px;color:var(--dim);margin-top:2px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.stack{display:flex;height:14px;border-radius:7px;overflow:hidden;background:#eef2f8;margin-bottom:14px}
.stack i{display:block;height:100%}
.legend{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:12.5px;color:#3c4c60}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend em{width:9px;height:9px;border-radius:3px;flex:none}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:7px 8px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:600;font-size:12px}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{height:6px;border-radius:3px;background:#eef2f8;overflow:hidden;min-width:60px}
.bar i{display:block;height:100%}
.tree{font-size:13px}
details{margin-left:14px;border-left:1px dashed #d7e0ec;padding-left:10px}
summary{cursor:pointer;padding:3px 0;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸";color:#9bb0c8;margin-right:6px;font-size:11px}
details[open]>summary::before{content:"▾"}
.row{display:flex;gap:10px;align-items:center;padding:2px 0}
.row .nm{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .sz{flex:none;font-variant-numeric:tabular-nums;color:#3c4c60;font-size:12px}
.row .pc{flex:none;width:52px;text-align:right;color:var(--dim);font-size:11.5px}
.tb{display:block;flex:none;width:92px;height:6px;border-radius:3px;background:#e9eff7;overflow:hidden}
.tb i{display:block;height:100%;border-radius:3px}
.leaf{display:flex;gap:10px;align-items:center;padding:2px 0 2px 17px;
  border-left:1px dashed #d7e0ec;margin-left:8px}
.dot{width:8px;height:8px;border-radius:3px;flex:none}
.more{color:var(--dim);font-size:12px;padding:3px 0 3px 17px}
footer{margin-top:40px;color:var(--dim);font-size:12px;text-align:center}
@media print{body{background:#fff}.panel,.card{box-shadow:none;break-inside:avoid}}
"""


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return ("%.2f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
        n /= 1024.0
    return "%.2f PB" % n


def _esc(text):
    return html.escape(str(text or ""), quote=True)


DIR_BAR = "#5b86bd"


def _bar(pct, color):
    """目录树里的比例条。"""
    return ("<span class='tb'><i style='width:%.2f%%;background:%s'></i></span>"
            % (max(pct, 0.0), color))


def _pct(part, whole):
    if not whole:
        return 0.0
    return max(0.0, min(100.0, part * 100.0 / whole))


def build(store, root="/", depth=4, per_level=30, max_nodes=2500):
    """返回完整的 HTML 字符串。"""
    node = store.row(root) or {}
    total = node.get("size") or 0
    nfiles = node.get("nfiles") or 0
    kids = store.children(root) or []
    dirs = sum(1 for k in kids if k["isdir"]) if root == "/" else None

    kinds = [k for k in (store.kinds(root) or []) if k["size"] > 0]
    kind_total = sum(k["size"] for k in kinds) or 1
    tops = store.top_files(20, root) or []

    # --- 类型分布
    stack, legend = [], []
    for k in kinds:
        meta = CAT.get(k["cat"], CAT["other"])
        w = _pct(k["size"], kind_total)
        stack.append(
            '<i style="width:%.3f%%;background:%s" title="%s"></i>'
            % (w, meta["c"], _esc(meta["n"]))
        )
        legend.append(
            '<span><em style="background:%s"></em>%s <b>%s</b> · %s 个</span>'
            % (meta["c"], _esc(meta["n"]), human(k["size"]), "{:,}".format(k["count"]))
        )

    # --- 最大文件
    top_rows = []
    for i, f in enumerate(tops, 1):
        meta = CAT.get(f["cat"], CAT["other"])
        top_rows.append(
            "<tr><td class='num'>%d</td>"
            "<td><div class='row'><i class='dot' style='background:%s'></i>"
            "<span class='nm' title='%s'>%s</span></div></td>"
            "<td class='num'>%s</td><td class='num'>%.1f%%</td>"
            "<td><div class='bar'><i style='width:%.1f%%;background:%s'></i></div></td></tr>"
            % (
                i,
                meta["c"],
                _esc(f["path"]),
                _esc(f["name"]),
                human(f["size"]),
                _pct(f["size"], total),
                _pct(f["size"], tops[0]["size"] if tops else 1),
                meta["c"],
            )
        )

    # --- 目录树
    budget = [max_nodes]
    tree_html = _tree(store, root, total, depth, per_level, budget)

    stamp = time.strftime("%Y-%m-%d %H:%M")
    title = "PanLens 空间分析报告"
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s · %(root)s</title>
<style>%(css)s</style></head>
<body><div class="wrap">
<h1>%(title)s</h1>
<div class="meta">分析目录：<b>%(root)s</b> · 生成时间 %(stamp)s · 由 PanLens 生成</div>

<div class="cards">
  <div class="card"><div class="k">总占用</div><div class="v">%(total)s</div>
    <div class="s">%(nfiles)s 个文件</div></div>
  <div class="card"><div class="k">一级条目</div><div class="v">%(nkids)s</div>
    <div class="s">其中文件夹 %(ndirs)s 个</div></div>
  <div class="card"><div class="k">最大单文件</div><div class="v">%(maxone)s</div>
    <div class="s">%(maxname)s</div></div>
</div>

<h2>类型分布</h2>
<div class="panel">
  <div class="stack">%(stack)s</div>
  <div class="legend">%(legend)s</div>
</div>

<h2>最大的文件</h2>
<div class="panel">
<table><thead><tr><th style="width:38px">#</th><th>名称</th>
<th style="width:88px;text-align:right">大小</th><th style="width:64px;text-align:right">占比</th>
<th style="width:120px"></th></tr></thead><tbody>
%(toprows)s
</tbody></table>
</div>

<h2>目录结构（前 %(depth)d 层）</h2>
<div class="panel tree">%(tree)s</div>

<footer>由 PanLens 生成 · 数据仅来自本机索引 · 报告为静态文件，不含任何账号信息</footer>
</div></body></html>""" % {
        "title": title,
        "css": CSS,
        "root": _esc(root),
        "stamp": stamp,
        "total": human(total),
        "nfiles": "{:,}".format(nfiles or 0),
        "nkids": len(kids),
        "ndirs": dirs if dirs is not None else sum(1 for k in kids if k["isdir"]),
        "maxone": human(tops[0]["size"]) if tops else "—",
        "maxname": _esc(tops[0]["name"]) if tops else "—",
        "stack": "".join(stack),
        "legend": "".join(legend) or "<span>暂无数据</span>",
        "toprows": "".join(top_rows) or "<tr><td colspan='5'>暂无文件</td></tr>",
        "depth": depth,
        "tree": tree_html,
    }


def _tree(store, root, parent_size, depth, per_level, budget):
    """递归渲染目录树，返回 HTML 片段。"""
    if depth <= 0 or budget[0] <= 0:
        return ""
    kids = store.children(root) or []
    if not kids:
        return ""
    kids = [k for k in kids if k["size"] > 0]
    kids.sort(key=lambda k: k["size"], reverse=True)
    head, tail = kids[:per_level], kids[per_level:]

    out = []
    for k in head:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        pct = _pct(k["size"], parent_size)
        meta = CAT.get(k["cat"], CAT["other"])
        if k["isdir"]:
            inner = _tree(store, k["path"], k["size"], depth - 1, per_level, budget)
            out.append(
                "<details><summary><div class='row'>"
                "<span class='nm'>%s</span>%s"
                "<span class='sz'>%s</span><span class='pc'>%.1f%%</span></div></summary>%s</details>"
                % (_esc(k["name"]), _bar(pct, DIR_BAR), human(k["size"]), pct,
                   inner or "<div class='more'>（未展开 / 无下级数据）</div>")
            )
        else:
            out.append(
                "<div class='leaf'><i class='dot' style='background:%s'></i>"
                "<span class='nm'>%s</span>%s<span class='sz'>%s</span>"
                "<span class='pc'>%.1f%%</span></div>"
                % (meta["c"], _esc(k["name"]), _bar(pct, meta["c"]), human(k["size"]), pct)
            )
    if tail:
        out.append(
            "<div class='more'>另有 %d 项，合计 %s（未逐条列出）</div>"
            % (len(tail), human(sum(x["size"] for x in tail)))
        )
    return "".join(out)
