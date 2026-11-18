# -*- coding: utf-8 -*-
"""演示数据：无需登录也能看到可视化效果。"""
from __future__ import annotations

import random
import time

from .store import categorize

DEMO_ROOT = "/演示空间"
GB = 1024 ** 3
MB = 1024 ** 2
KB = 1024

_MOVIES = [
    "星际穿越.2014.2160p.UHD.BluRay.x265.mkv",
    "盗梦空间.2010.1080p.BluRay.x264.mkv",
    "沙丘2.2024.2160p.WEB-DL.H265.mkv",
    "奥本海默.2023.2160p.BluRay.REMUX.mkv",
    "银翼杀手2049.2017.1080p.mkv",
    "流浪地球2.2023.IMAX.2160p.mkv",
    "疯狂的麦克斯.狂暴之路.2015.mkv",
    "沙丘.2021.2160p.HDR.mkv",
]
_SHOWS = ["权力的游戏", "绝命毒师", "西部世界", "黑镜"]
_MUSIC = ["周杰伦-叶惠美", "陈奕迅-U87", "久石让-Summer", "Einaudi-Le Onde", "Hans Zimmer-Time"]
_DOCS = ["毕业设计论文.docx", "简历-2026.pdf", "项目立项书.pptx", "财务报表-2026.xlsx",
         "答辩PPT.pptx", "需求文档.md", "接口设计.pdf", "测试报告.docx"]
_CODE = ["smart-community-property-system.zip", "spring-boot-demo.zip", "dataset.csv", "backup.sql"]
_APPS = ["VisualStudioSetup.exe", "NavicatPremium.dmg", "Adobe.Photoshop.2024.7z",
         "JDK21-windows.exe", "Ubuntu24.04-desktop.iso"]
_ARCHIVE = ["全家福-原始素材.7z", "手机备份-2025.zip", "旧硬盘归档.rar"]


def build(store, root=DEMO_ROOT):
    """生成一棵贴近真实的演示目录树，写入 store 并做汇总。返回总字节数。"""
    rng = random.Random(20261118)
    store.clear_subtree(root)
    now = int(time.time())
    store.upsert_many([(root, "", "演示空间", 1, 0, 0, now, 0, "other")])
    rows = []
    stat = {"bytes": 0}

    def join(p, n):
        return (p.rstrip("/") + "/" + n) if p != "/" else "/" + n

    def mkdir(parent, name, depth):
        p = join(parent, name)
        rows.append((p, parent, name, 1, 0, 0, now, depth, "other"))
        return p

    def add_files(parent, depth, specs):
        for name, size in specs:
            rows.append(
                (join(parent, name), parent, name, 0, size, 1,
                 now - rng.randint(0, 90_000_000), depth, categorize(name))
            )
            stat["bytes"] += size

    def rsize(lo, hi):
        return rng.randint(lo, hi)

    # ---------------------------------------------------------------- 影视
    tv = mkdir(root, "影视", 1)
    add_files(mkdir(tv, "电影", 2), 3, [(m, rsize(6 * GB, 42 * GB)) for m in _MOVIES])

    series = mkdir(tv, "剧集", 2)
    for show in _SHOWS:
        sp = mkdir(series, show, 3)
        eps = []
        for season in (1, 2):
            for ep in range(1, 11):
                eps.append(("S%02dE%02d.1080p.WEB-DL.mkv" % (season, ep), rsize(700 * MB, 2400 * MB)))
        add_files(sp, 4, eps)

    add_files(mkdir(tv, "纪录片", 2), 3,
              [("地球脉动.S%02d.mkv" % i, rsize(1 * GB, 9 * GB)) for i in range(1, 9)])
    add_files(mkdir(tv, "动漫", 2), 3,
              [("进击的巨人.EP%02d.mkv" % i, rsize(300 * MB, 1200 * MB)) for i in range(1, 26)])

    # ---------------------------------------------------------------- 照片
    photos = mkdir(root, "照片", 1)
    for album in ["2023-三亚", "2024-毕业旅行", "2025-日常", "相机原图"]:
        ap = mkdir(photos, album, 2)
        specs = []
        for i in range(rng.randint(40, 240)):
            if i % 17 == 0:
                specs.append(("DSC_%04d.RAW" % i, rsize(18 * MB, 42 * MB)))
            else:
                specs.append(("IMG_%04d.jpg" % i, rsize(2 * MB, 9 * MB)))
        add_files(ap, 3, specs)
        add_files(mkdir(ap, "视频", 3), 4,
                  [("VID_%04d.mp4" % i, rsize(60 * MB, 900 * MB)) for i in range(rng.randint(4, 12))])

    # ---------------------------------------------------------------- 学习
    study = mkdir(root, "学习", 1)
    for nm in ["考研资料", "算法笔记", "英语听力", "公开课"]:
        sp = mkdir(study, nm, 2)
        specs = []
        for i in range(rng.randint(8, 46)):
            ext = rng.choice([".pdf", ".mp4", ".md", ".docx", ".pptx"])
            specs.append(("第%02d讲%s" % (i + 1, ext), rsize(300 * KB, 900 * MB)))
        add_files(sp, 3, specs)

    # ---------------------------------------------------------------- 工作
    work = mkdir(root, "工作", 1)
    for nm in ["项目源码", "合同扫描件", "周报归档", "设计稿"]:
        wp = mkdir(work, nm, 2)
        add_files(wp, 3, [(f, rsize(20 * KB, 120 * MB)) for f in _DOCS + _CODE])
        add_files(mkdir(wp, "2026", 3), 4, [(f, rsize(30 * KB, 60 * MB)) for f in _DOCS])

    # ---------------------------------------------------------------- 软件
    apps = mkdir(root, "软件", 1)
    add_files(mkdir(apps, "安装包", 2), 3, [(f, rsize(80 * MB, 7 * GB)) for f in _APPS])
    add_files(mkdir(apps, "归档", 2), 3, [(f, rsize(300 * MB, 12 * GB)) for f in _ARCHIVE])

    # ---------------------------------------------------------------- 音乐
    music = mkdir(root, "音乐", 1)
    for nm in ["无损收藏", "现场版", "OST"]:
        mp = mkdir(music, nm, 2)
        add_files(mp, 3, [(m + "-%02d.flac" % i, rsize(18 * MB, 70 * MB))
                          for i, m in enumerate(_MUSIC * 4)])

    store.upsert_many(rows)
    store.rollup(root)
    return stat["bytes"]
