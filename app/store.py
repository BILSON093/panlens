# -*- coding: utf-8 -*-
"""SQLite 存储层：目录树扁平化存储 + 尺寸自底向上汇总。"""
from __future__ import annotations

import os
import sqlite3
import threading

# 文件类型分类（用于色块配色与类型统计）
_CATS = {
    "video": ".mp4 .mkv .avi .mov .wmv .flv .ts .m2ts .rmvb .rm .webm .m4v .mpg .mpeg .3gp .vob .asf .f4v",
    "image": ".jpg .jpeg .png .gif .bmp .webp .heic .heif .tif .tiff .svg .raw .cr2 .nef .arw .psd .ai .dng",
    "audio": ".mp3 .flac .wav .aac .m4a .ogg .wma .ape .dsd .aiff .opus .mid",
    "doc": ".doc .docx .xls .xlsx .ppt .pptx .pdf .txt .md .csv .epub .mobi .rtf .odt .pages .key .numbers .wps .et .dps .azw3 .chm",
    "archive": ".zip .rar .7z .tar .gz .bz2 .xz .iso .tgz .cab .z01 .z02 .lz4 .zst",
    "code": ".py .js .ts .jsx .tsx .java .c .cpp .h .hpp .cs .go .rs .php .rb .sh .json .xml .yml .yaml .html .css .sql .kt .swift .ipynb .vue .lua .toml .ini",
    "app": ".exe .msi .dmg .apk .ipa .deb .rpm .appimage .jar .pkg .bat .scr",
}

EXT_TO_CAT = {}
for _cat, _exts in _CATS.items():
    for _e in _exts.split():
        EXT_TO_CAT[_e] = _cat

CATEGORIES = list(_CATS.keys())

# 演示数据的根路径。它和真实数据共用一张表，凡是统计「真实空间」的查询都要排除它，
# 否则 /演示空间 下的文件会被算进根目录的「最大文件」「类型分布」和体积汇总。
DEMO_PREFIX = "/演示空间"


def _excl_demo(root):
    """查询根在演示区之外时，排除演示数据。"""
    if root and root.startswith(DEMO_PREFIX):
        return ""
    return " AND path NOT LIKE '%s/%%'" % DEMO_PREFIX


def categorize(name: str) -> str:
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return "other"
    return EXT_TO_CAT.get(name[dot:].lower(), "other")


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path    TEXT PRIMARY KEY,
    parent  TEXT NOT NULL,
    name    TEXT NOT NULL,
    isdir   INTEGER NOT NULL,
    size    INTEGER NOT NULL DEFAULT 0,
    nfiles  INTEGER NOT NULL DEFAULT 0,
    mtime   INTEGER NOT NULL DEFAULT 0,
    depth   INTEGER NOT NULL DEFAULT 0,
    cat     TEXT NOT NULL DEFAULT 'other'
);
CREATE INDEX IF NOT EXISTS idx_files_parent ON files(parent, size DESC);
CREATE INDEX IF NOT EXISTS idx_files_size   ON files(size DESC);
CREATE INDEX IF NOT EXISTS idx_files_cat    ON files(cat);
CREATE INDEX IF NOT EXISTS idx_files_name   ON files(name);
"""


class Store:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.lock = threading.RLock()
        self.con = sqlite3.connect(db_path, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA temp_store=MEMORY")
        with self.lock:
            self.con.executescript(SCHEMA)
            self.con.commit()

    # -------------------------------------------------------------- 写入
    def clear_subtree(self, root: str):
        pat = root.rstrip("/") + "/%"
        with self.lock:
            self.con.execute("DELETE FROM files WHERE path = ? OR path LIKE ?", (root, pat))
            self.con.commit()

    def upsert_many(self, rows):
        """rows: [(path, parent, name, isdir, size, nfiles, mtime, depth, cat), ...]"""
        if not rows:
            return
        with self.lock:
            self.con.executemany(
                "INSERT OR REPLACE INTO files(path,parent,name,isdir,size,nfiles,mtime,depth,cat)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self.con.commit()

    def remove_paths(self, paths):
        """删除若干路径（含其子树）的本地索引。"""
        with self.lock:
            for p in paths:
                pat = p.rstrip("/") + "/%"
                self.con.execute("DELETE FROM files WHERE path=? OR path LIKE ?", (p, pat))
            self.con.commit()

    def rollup(self, root: str):
        """自底向上汇总：目录体积 = 直属文件体积 + 子目录体积。"""
        pat = root.rstrip("/") + "/%"
        with self.lock:
            dirs = self.con.execute(
                "SELECT path,parent FROM files WHERE isdir=1 AND (path = ? OR path LIKE ?)"
                + _excl_demo(root) + " ORDER BY depth DESC",
                (root, pat),
            ).fetchall()
            if not dirs:
                return
            direct = {
                r[0]: (r[1] or 0, r[2] or 0)
                for r in self.con.execute(
                    "SELECT parent, SUM(size), SUM(nfiles) FROM files "
                    "WHERE isdir=0 AND (path = ? OR path LIKE ?)" + _excl_demo(root) +
                    " GROUP BY parent",
                    (root, pat),
                ).fetchall()
            }

            totals = {}
            for path, _parent in dirs:
                d = direct.get(path, (0, 0))
                totals[path] = [d[0], d[1]]

            # dirs 已按 depth 降序，子目录必定先于父目录处理
            for path, parent in dirs:
                if parent == path:
                    continue
                cur = totals.get(path)
                if cur is None:
                    continue
                node = totals.get(parent)
                if node is None:
                    totals[parent] = [cur[0], cur[1]]
                else:
                    node[0] += cur[0]
                    node[1] += cur[1]

            updates = [(v[0], v[1], k) for k, v in totals.items()]
            self.con.executemany("UPDATE files SET size=?, nfiles=? WHERE path=?", updates)
            self.con.commit()

    # -------------------------------------------------------------- 读取
    def row(self, path):
        with self.lock:
            cur = self.con.execute(
                "SELECT path,parent,name,isdir,size,nfiles,mtime,cat FROM files WHERE path=?",
                (path,),
            )
            r = cur.fetchone()
        if not r:
            return None
        return {
            "path": r[0],
            "parent": r[1],
            "name": r[2],
            "isdir": bool(r[3]),
            "size": r[4],
            "nfiles": r[5],
            "mtime": r[6],
            "cat": r[7],
        }

    def children(self, path, limit=None):
        with self.lock:
            sql = (
                "SELECT path,name,isdir,size,nfiles,mtime,cat FROM files "
                "WHERE parent=? AND path<>parent ORDER BY isdir DESC, size DESC, name ASC"
            )
            if limit:
                sql += " LIMIT %d" % int(limit)
            rows = self.con.execute(sql, (path,)).fetchall()
        return [
            {
                "path": r[0],
                "name": r[1],
                "isdir": bool(r[2]),
                "size": r[3],
                "nfiles": r[4],
                "mtime": r[5],
                "cat": r[6],
            }
            for r in rows
        ]

    def children_many(self, parents, cap=600):
        """批量取多个目录的子项，按 parent 分组，每个目录最多 cap 条。"""
        if not parents:
            return {}
        ph = ",".join("?" * len(parents))
        sql = (
            "SELECT parent,path,name,isdir,size,nfiles,mtime,cat FROM ("
            "  SELECT parent,path,name,isdir,size,nfiles,mtime,cat,"
            "         ROW_NUMBER() OVER (PARTITION BY parent ORDER BY isdir DESC, size DESC, name ASC) rn"
            "  FROM files WHERE parent IN (%s) AND path<>parent"
            ") WHERE rn <= ? ORDER BY parent, rn"
        ) % ph
        with self.lock:
            rows = self.con.execute(sql, tuple(parents) + (int(cap),)).fetchall()
        grouped = {}
        for r in rows:
            grouped.setdefault(r[0], []).append(
                {
                    "path": r[1],
                    "name": r[2],
                    "isdir": bool(r[3]),
                    "size": r[4],
                    "nfiles": r[5],
                    "mtime": r[6],
                    "cat": r[7],
                }
            )
        return grouped

    def totals_for(self, parent):
        with self.lock:
            r = self.con.execute(
                "SELECT COUNT(*), COALESCE(SUM(size),0), COALESCE(SUM(nfiles),0) FROM files WHERE parent=? AND path<>parent",
                (parent,),
            ).fetchone()
        return {"count": r[0], "size": r[1], "nfiles": r[2]}

    def search(self, q, limit=200):
        like = "%" + q.replace("%", "").replace("_", " ") + "%"
        with self.lock:
            rows = self.con.execute(
                "SELECT path,parent,name,isdir,size,nfiles,mtime,cat FROM files "
                "WHERE name LIKE ? AND isdir=0 ORDER BY size DESC LIMIT ?",
                (like, int(limit)),
            ).fetchall()
        return [
            {
                "path": r[0],
                "parent": r[1],
                "name": r[2],
                "isdir": bool(r[3]),
                "size": r[4],
                "nfiles": r[5],
                "mtime": r[6],
                "cat": r[7],
            }
            for r in rows
        ]

    def top_files(self, limit=40, root=None):
        with self.lock:
            if root:
                pat = root.rstrip("/") + "/%"
                rows = self.con.execute(
                    "SELECT path,parent,name,size,mtime,cat FROM files "
                    "WHERE isdir=0 AND (path LIKE ?)" + _excl_demo(root) +
                    " ORDER BY size DESC LIMIT ?",
                    (pat, int(limit)),
                ).fetchall()
            else:
                rows = self.con.execute(
                    "SELECT path,parent,name,size,mtime,cat FROM files "
                    "WHERE isdir=0 ORDER BY size DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        return [
            {"path": r[0], "parent": r[1], "name": r[2], "size": r[3], "mtime": r[4], "cat": r[5]}
            for r in rows
        ]

    def kinds(self, root):
        pat = root.rstrip("/") + "/%"
        with self.lock:
            rows = self.con.execute(
                "SELECT cat, SUM(size), COUNT(*) FROM files "
                "WHERE isdir=0 AND path LIKE ?" + _excl_demo(root) +
                " GROUP BY cat ORDER BY SUM(size) DESC",
                (pat,),
            ).fetchall()
        return [{"cat": r[0], "size": r[1] or 0, "count": r[2]} for r in rows]

    def has_data(self):
        with self.lock:
            n = self.con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return n > 0

    def iter_files(self, root):
        pat = root.rstrip("/") + "/%"
        with self.lock:
            cur = self.con.execute(
                "SELECT path,isdir,size,mtime,cat FROM files WHERE path LIKE ?"
                + _excl_demo(root) + " ORDER BY path",
                (pat,),
            )
            for r in cur:
                yield r

    def close(self):
        with self.lock:
            self.con.close()
