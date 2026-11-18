# -*- coding: utf-8 -*-
"""扫描器：并发遍历网盘目录树，落库并做体积汇总。"""
from __future__ import annotations

import random
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .baidu import AuthError, NotExistError, PanError
from .store import categorize


class Scanner:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.reset()
        self.thread = None

    # -------------------------------------------------------------- 状态
    def reset(self):
        with self.lock:
            self.running = False
            self.done = False
            self.cancelled = False
            self.error = None
            self.root = "/"
            self.dir_done = 0
            self.dir_total = 0
            self.file_count = 0
            self.total_bytes = 0
            self.current = ""
            self.started_at = 0.0
            self.finished_at = 0.0
            self.errors = []

    def status(self):
        with self.lock:
            if self.started_at:
                elapsed = (self.finished_at or time.time()) - self.started_at
            else:
                elapsed = 0
            return {
                "running": self.running,
                "done": self.done,
                "cancelled": self.cancelled,
                "error": self.error,
                "root": self.root,
                "dirDone": self.dir_done,
                "dirTotal": self.dir_total,
                "fileCount": self.file_count,
                "totalBytes": self.total_bytes,
                "current": self.current,
                "elapsed": round(elapsed, 1),
                "errors": self.errors[-8:],
            }

    # -------------------------------------------------------------- 控制
    def start(self, api, root="/", workers=4):
        with self.lock:
            if self.running:
                return False
        self.reset()
        with self.lock:
            self.running = True
            self.root = root
            self.started_at = time.time()
        self.thread = threading.Thread(
            target=self._run, args=(api, root, workers), daemon=True
        )
        self.thread.start()
        return True

    def cancel(self):
        with self.lock:
            self.cancelled = True

    # -------------------------------------------------------------- 主流程
    def _run(self, api, root, workers):
        try:
            self.store.clear_subtree(root)
            name = root.rstrip("/").split("/")[-1] or "/"
            self.store.upsert_many(
                [(root, "", name, 1, 0, 0, int(time.time()), 0, "other")]
            )

            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending = {pool.submit(self._list_one, api, root): (root, 0)}
                with self.lock:
                    self.dir_total = 1

                while pending:
                    if self.cancelled:
                        break
                    done, _ = wait(
                        list(pending), return_when=FIRST_COMPLETED, timeout=1.0
                    )
                    for fut in done:
                        item = pending.pop(fut, None)
                        if item is None:
                            continue
                        path, depth = item
                        self._consume(fut, path, depth, api, pool, pending)

            self.store.rollup(root)
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.error = "%s: %s" % (type(exc).__name__, exc)
        finally:
            with self.lock:
                self.running = False
                self.done = True
                self.finished_at = time.time()

    @staticmethod
    def _list_one(api, path):
        # 轻微抖动，避免对接口造成瞬时压力
        time.sleep(random.uniform(0.01, 0.05))
        return api.list_dir(path)

    def _consume(self, fut, path, depth, api, pool, pending):
        try:
            children = fut.result()
        except AuthError as exc:
            with self.lock:
                self.error = "登录失效：%s" % exc
                self.cancelled = True
            return
        except NotExistError:
            children = []
        except PanError as exc:
            with self.lock:
                self.errors.append({"path": path, "msg": str(exc)})
            children = []
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.errors.append({"path": path, "msg": repr(exc)})
            children = []

        rows = []
        subdirs = []
        bytes_here = 0
        child_depth = depth + 1
        for item in children:
            isdir = item["isdir"]
            cat = "other" if isdir else categorize(item["name"])
            rows.append(
                (
                    item["path"],
                    path,
                    item["name"],
                    isdir,
                    0 if isdir else item["size"],
                    0 if isdir else 1,
                    item["mtime"],
                    child_depth,
                    cat,
                )
            )
            if isdir:
                subdirs.append(item["path"])
            else:
                bytes_here += item["size"]

        if rows:
            self.store.upsert_many(rows)

        with self.lock:
            self.dir_done += 1
            self.current = path
            self.file_count += len(children) - len(subdirs)
            self.total_bytes += bytes_here
            self.dir_total += len(subdirs)

        if self.cancelled:
            return
        for sub in subdirs:
            pending[pool.submit(self._list_one, api, sub)] = (sub, child_depth)


def new_scanner(store):
    return Scanner(store)
