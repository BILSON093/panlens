/* 网盘空间分析器 - 前端逻辑 */
(function () {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const NS = 'http://www.w3.org/2000/svg';

  const CAT = {
    video:   { c: '#c9524a', n: '视频' },
    image:   { c: '#d99a3c', n: '图片' },
    audio:   { c: '#9a6fd0', n: '音频' },
    doc:     { c: '#3f83c4', n: '文档' },
    archive: { c: '#2f9e7d', n: '压缩包' },
    code:    { c: '#5aa84f', n: '代码' },
    app:     { c: '#b25f8a', n: '应用' },
    other:   { c: '#5c6b7f', n: '其他' }
  };
  const DIR_FILL = '#2a3a4f';

  const S = {
    path: '/', node: null, depth: 4, loggedIn: false, quota: null,
    sortKey: 'size', sortDir: -1, scanning: false, timer: null, hasData: false,
    group: null   // 点开「其他 N 项」时的临时视图 {label, items}
  };
  const AGG = new Set(); // 已展开的「其他 N 项」聚合块

  /* ---------------------------------------------------------------- 工具 */
  function fmt(n) {
    if (n === null || n === undefined) return '—';
    if (n < 1024) return n + ' B';
    const u = ['KB', 'MB', 'GB', 'TB', 'PB'];
    let v = n, i = -1;
    do { v /= 1024; i++; } while (v >= 1024 && i < u.length - 1);
    const d = v >= 100 ? 0 : v >= 10 ? 1 : 2;
    return v.toFixed(d) + ' ' + u[i];
  }

  function fmtNum(n) { return (n || 0).toLocaleString('zh-CN'); }

  function fmtDate(ts) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    const p = x => String(x).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }

  function fmtSpan(sec) {
    if (!sec) return '0s';
    if (sec < 60) return sec.toFixed(0) + 's';
    const m = Math.floor(sec / 60);
    return m + 'm ' + Math.round(sec % 60) + 's';
  }

  function colorOf(node) {
    if (!node.isdir) return (CAT[node.cat] || CAT.other).c;
    return DIR_FILL;
  }

  let toastTimer = null;
  function toast(msg, kind) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = 'toast' + (kind ? ' ' + kind : '');
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 4200);
  }

  async function api(url, opts) {
    const res = await fetch(url, opts);
    let j;
    try { j = await res.json(); } catch (e) { throw new Error('服务无响应'); }
    if (!j.ok) {
      const err = new Error(j.error || ('HTTP ' + res.status));
      err.auth = !!j.auth;
      throw err;
    }
    return j;
  }

  /* ---------------------------------------------------------------- 面包屑 */
  function renderCrumbs() {
    const box = $('#crumbs');
    box.innerHTML = '';
    const parts = S.path.split('/').filter(Boolean);
    const push = (label, path, cur) => {
      const el = document.createElement('span');
      el.className = 'crumb' + (cur ? ' cur' : '');
      el.textContent = label;
      el.title = path;
      if (!cur) el.onclick = () => navigate(path);
      box.appendChild(el);
      return el;
    };
    push('我的网盘', '/', parts.length === 0);
    let acc = '';
    parts.forEach((p, i) => {
      const sep = document.createElement('span');
      sep.className = 'sep';
      sep.textContent = '›';
      box.appendChild(sep);
      acc += '/' + p;
      push(p, acc, i === parts.length - 1);
    });
    if (S.group) {
      const sep = document.createElement('span');
      sep.className = 'sep';
      sep.textContent = '›';
      box.appendChild(sep);
      const chip = document.createElement('span');
      chip.className = 'crumb cur group';
      chip.textContent = S.group.label + '  ✕';
      chip.title = '退出该分组视图';
      chip.onclick = () => { S.group = null; renderCrumbs(); renderTreemap(); renderTable(); renderStats(); };
      box.appendChild(chip);
    }
    box.scrollLeft = box.scrollWidth;
  }

  /* ---------------------------------------------------------------- Treemap */
  function el(tag, attrs) {
    const n = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  const meas = document.createElement('canvas').getContext('2d');
  function fitText(text, maxW, font) {
    if (maxW <= 4) return '';
    meas.font = font;
    if (meas.measureText(text).width <= maxW) return text;
    let lo = 0, hi = text.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (meas.measureText(text.slice(0, mid) + '…').width <= maxW) lo = mid; else hi = mid - 1;
    }
    return lo > 0 ? text.slice(0, lo) + '…' : '';
  }

  function worstRatio(row, area, short) {
    if (!row.length || area <= 0 || short <= 0) return Infinity;
    let max = 0, min = Infinity;
    for (const d of row) { if (d.area > max) max = d.area; if (d.area < min) min = d.area; }
    if (min <= 0) return Infinity;
    const s2 = area * area;
    return Math.max((short * short * max) / s2, s2 / (short * short * min));
  }

  /* 经典 squarified treemap，返回相对坐标 */
  function squarify(items, W, H) {
    const out = [];
    const total = items.reduce((a, b) => a + b.size, 0);
    if (total <= 0 || W <= 1 || H <= 1) return out;
    const scale = (W * H) / total;
    const data = items.map(it => ({ it, area: Math.max(it.size * scale, 1e-6) }));
    let x = 0, y = 0, w = W, h = H, i = 0;
    while (i < data.length && w > 0.6 && h > 0.6) {
      const short = Math.min(w, h);
      const row = [];
      let rowArea = 0, best = Infinity;
      while (i < data.length) {
        const cand = data[i];
        const newArea = rowArea + cand.area;
        const r = worstRatio(row.concat([cand]), newArea, short);
        if (row.length === 0 || r <= best) {
          row.push(cand); rowArea = newArea; best = r; i++;
        } else break;
      }
      if (w >= h) {
        const strip = Math.min(rowArea / h, w);
        let yy = y;
        for (const d of row) {
          const hh = rowArea > 0 ? (d.area / rowArea) * h : 0;
          out.push({ node: d.it, x, y: yy, w: strip, h: hh });
          yy += hh;
        }
        x += strip; w -= strip;
      } else {
        const strip = Math.min(rowArea / w, h);
        let xx = x;
        for (const d of row) {
          const ww = rowArea > 0 ? (d.area / rowArea) * w : 0;
          out.push({ node: d.it, x: xx, y, w: ww, h: strip });
          xx += ww;
        }
        y += strip; h -= strip;
      }
    }
    return out;
  }

  let budget = 0;

  function drawLevel(children, box, level, g, keyBase, cellCap) {
    if (box.w < 3 || box.h < 3 || budget <= 0) return;
    let items = children.filter(c => c.size > 0);
    if (!items.length) return;
    items.sort((a, b) => b.size - a.size);

    // 保证每个画出来的格子都有足够面积（否则会出现没地方写字的细条）：
    // 面积不足以放下一个标签的条目，统一并入「其他 N 项」，不再画成碎条。
    const boxArea = Math.max(box.w * box.h, 1);
    const MIN_AREA = 620;                       // 单格最小面积（约 25×25），低于此值并入「其他」
    const totalSize = items.reduce((a, b) => a + b.size, 0) || 1;
    const minShare = MIN_AREA / boxArea;
    const kept = [];
    const dropped = [];
    let dropSize = 0, dropFiles = 0;
    const limit = cellCap || 420;   // 展开聚合块时用较小的上限，保证每格都够大能写字
    const useMin = cellCap ? 0 : minShare;  // 展开时只按数量截断，不再按最小面积丢弃
    for (const it of items) {
      if (it.size / totalSize >= useMin && kept.length < limit) {
        kept.push(it);
      } else {
        dropped.push(it);
        dropSize += it.size;
        dropFiles += it.nfiles || 0;
      }
    }
    if (dropped.length) {
      const key = (keyBase || '') + '#' + level;
      const open = AGG.has(key);
      kept.push({
        name: '其他 ' + dropped.length + ' 项' + (open ? ' ▾' : ' ▸'),
        path: '', isdir: true, agg: true, aggKey: key, nfiles: dropFiles,
        cat: 'other', size: dropSize, _hidden: dropped, open: open
      });
    }
    items = kept;

    const rects = squarify(items, box.w, box.h);
    for (const r of rects) {
      if (budget-- <= 0) return;
      const gx = box.x + r.x, gy = box.y + r.y;
      const gw = Math.max(r.w - 1, 0.5), gh = Math.max(r.h - 1, 0.5);
      const n = r.node;
      const isAggOpen = !!n.agg && !!n.open && !!(n._hidden && n._hidden.length);
      const isDir = (n.isdir && !n.agg) || isAggOpen;
      const kids = isAggOpen ? n._hidden : (n.agg ? null : n.children);
      // 子项平均可用面积太小时不再细分，否则会画出一堆无法标注的细条
      const perChild = (gw * gh) / Math.max((kids && kids.length) || 1, 1);
      // 聚合块被点开时，允许它再细分一层（内部会再次按面积聚合，实现逐层展开）
      const canDrill = kids && kids.length && gw > 52 && gh > 34 &&
        (isAggOpen || (level + 1 < S.depth && perChild > 260));

      if (canDrill) {
        const grp = el('g');
        const bg = el('rect', {
          x: gx, y: gy, width: gw, height: gh,
          fill: '#1b2534', stroke: '#31445c', 'stroke-width': 1, rx: 2
        });
        bg.classList.add('rect');
        bg.__n = n;
        grp.appendChild(bg);

        const inner = { x: gx + 3, y: gy + 16, w: Math.max(gw - 6, 1), h: Math.max(gh - 19, 1) };
        drawLevel(kids, inner, level + 1, grp, isAggOpen ? n.aggKey : n.path,
                  isAggOpen ? 15 : 0);

        // 标题栏：左侧文件夹名，右侧占用空间（宽度够时再带占比）
        const rootSize = (S.node && S.node.size) || 0;
        const pct = rootSize > 0 ? (n.size / rootSize) * 100 : 0;
        let sizeTxt = fmt(n.size);
        if (pct > 0) {
          sizeTxt += ' · ' + (pct < 1 ? pct.toFixed(2) : pct < 10 ? pct.toFixed(1) : pct.toFixed(0)) + '%';
        }
        const sizeFont = '600 10px "Segoe UI", "Microsoft YaHei", sans-serif';
        meas.font = sizeFont;
        const avail = gw - 10;
        const minName = 46; // 至少给名称留这么多宽度，宁可不要尺寸
        if (meas.measureText(sizeTxt).width + minName + 10 > avail) {
          sizeTxt = fmt(n.size); // 放不下占比就去掉
        }
        if (meas.measureText(sizeTxt).width + minName + 10 > avail) {
          sizeTxt = '';          // 连大小都放不下，只留名称
        }
        const nameMax = sizeTxt ? avail - meas.measureText(sizeTxt).width - 9 : avail;

        const head = el('text', {
          x: gx + 5, y: gy + 11.5,
          fill: '#b9d1ea', 'font-size': 10.5, 'font-weight': 600,
          'pointer-events': 'none'
        });
        const nameFont = '600 10.5px "Segoe UI", "Microsoft YaHei", sans-serif';
        let nameText = fitText(n.name, nameMax, nameFont);
        if (!nameText) {
          // 名称连一个字都放不下时，优先保名称、放弃尺寸
          sizeTxt = '';
          nameText = fitText(n.name, avail, nameFont);
        }
        head.textContent = nameText;
        grp.appendChild(head);

        if (sizeTxt) {
          const sz = el('text', {
            x: gx + gw - 5, y: gy + 11.5,
            fill: '#8fb3d8', 'font-size': 10, 'font-weight': 600,
            'text-anchor': 'end', 'pointer-events': 'none'
          });
          sz.textContent = sizeTxt;
          grp.appendChild(sz);
        }
        g.appendChild(grp);
      } else {
        const collapsed = isDir && !(n.children && n.children.length);
        const fill = isDir ? (collapsed ? '#294059' : DIR_FILL) : (CAT[n.cat] || CAT.other).c;
        const rect = el('rect', {
          x: gx, y: gy, width: gw, height: gh,
          fill: fill, stroke: 'rgba(0,0,0,.35)', 'stroke-width': 1, rx: 1.5
        });
        rect.classList.add('rect');
        rect.__n = n;
        g.appendChild(rect);

        if (collapsed && gw > 8 && gh > 8) {
          g.appendChild(el('rect', {
            x: gx, y: gy, width: gw, height: gh,
            fill: 'url(#hatch)', 'pointer-events': 'none', rx: 1.5
          }));
        }

        drawLeafLabel(n, gx, gy, gw, gh, isDir, g);
      }
    }
  }

  /* 叶子标签：横向 → 竖排 → 首字，三级降级，尽量不留空白格 */
  function drawLeafLabel(n, gx, gy, gw, gh, isDir, g) {
    const nameFill = isDir ? '#dbe8f7' : 'rgba(255,255,255,.95)';

    // 1) 横向标签（空间足够时带第二行体积）
    if (gw > 17 && gh > 8) {
      const pad = gw > 40 ? 4 : 3;
      const twoLine = gh > 28 && gw > 40;
      const fs = twoLine ? 10.5 : (gw > 34 ? 9 : 8.5);
      const txt = fitText(n.name, gw - pad * 2, fs + 'px "Segoe UI", "Microsoft YaHei", sans-serif');
      if (txt) {
        const label = el('text', {
          x: gx + pad,
          y: twoLine ? gy + 13 : gy + Math.min(gh - 2.5, gh / 2 + 3.5),
          fill: nameFill, 'font-size': fs, 'pointer-events': 'none'
        });
        label.textContent = txt;
        g.appendChild(label);
        if (twoLine) {
          const sz = el('text', {
            x: gx + pad, y: gy + 26,
            fill: isDir ? 'rgba(195,212,232,.62)' : 'rgba(255,255,255,.72)',
            'font-size': 9.5, 'pointer-events': 'none'
          });
          sz.textContent = fitText(fmt(n.size), gw - pad * 2, '9.5px "Segoe UI", sans-serif');
          g.appendChild(sz);
        }
        return;
      }
    }

    // 2) 细高格子：竖排名称
    if (gh > 20 && gw > 6) {
      const v = fitText(n.name, gh - 8, '8.5px "Segoe UI", "Microsoft YaHei", sans-serif');
      if (v) {
        const label = el('text', {
          transform: 'translate(' + (gx + gw - 3.5) + ',' + (gy + gh - 3) + ') rotate(-90)',
          fill: nameFill, 'font-size': 8.5, 'pointer-events': 'none'
        });
        label.textContent = v;
        g.appendChild(label);
        return;
      }
    }

    // 3) 实在放不下：只画首字，至少能分辨，详细信息靠悬停
    if (gw > 9 && gh > 7) {
      const one = (n.name || '').trim().slice(0, 1);
      if (one) {
        const label = el('text', {
          x: gx + 2.5, y: gy + Math.min(gh - 2, 9.5),
          fill: nameFill, 'font-size': 8, 'pointer-events': 'none'
        });
        label.textContent = one;
        g.appendChild(label);
      }
    }
  }

  function renderTreemap() {
    const wrap = $('#wrap'), svg = $('#svg');
    const W = wrap.clientWidth, H = wrap.clientHeight;
    svg.innerHTML = '';
    const list = S.group ? S.group.items : (S.node && S.node.children);
    if (!list || !list.length) {
      svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
      return;
    }
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    budget = 4200;
    const defs = el('defs');
    const pat = el('pattern', {
      id: 'hatch', width: 7, height: 7, patternUnits: 'userSpaceOnUse',
      patternTransform: 'rotate(45)'
    });
    pat.appendChild(el('line', { x1: 0, y1: 0, x2: 0, y2: 7, stroke: 'rgba(255,255,255,.075)', 'stroke-width': 2 }));
    defs.appendChild(pat);
    svg.appendChild(defs);
    const g = el('g');
    svg.appendChild(g);
    drawLevel(list, { x: 0, y: 0, w: W, h: H }, 0, g, S.group ? '@grp' : S.path);
  }

  /* ---------------------------------------------------------------- 交互：悬停 / 点击 */
  function bindTreemapEvents() {
    const svg = $('#svg');
    const tip = $('#tip');

    svg.addEventListener('mouseover', e => {
      const t = e.target;
      if (!t.__n) return;
      const n = t.__n;
      const total = (S.node && S.node.size) || 1;
      const pct = (n.size / total * 100);
      tip.innerHTML =
        '<div class="tn"></div><div class="tp"></div><div class="tm">' +
        '<span>大小 <b>' + fmt(n.size) + '</b></span>' +
        '<span>占当前 <b>' + (pct < 0.01 ? '<0.01' : pct.toFixed(2)) + '%</b></span>' +
        (n.isdir ? '<span><b>' + fmtNum(n.nfiles) + '</b> 个文件</span>' : '') +
        '</div>';
      tip.querySelector('.tn').textContent = n.name;
      tip.querySelector('.tp').textContent = n.agg
        ? '（点击展开 / 收起这 ' + ((n._hidden && n._hidden.length) || 0) + ' 项）'
        : (n.path || '');
      tip.hidden = false;
      t.classList.add('hot');
      svg.classList.add('dim');
    });

    svg.addEventListener('mousemove', e => {
      if (tip.hidden) return;
      const pad = 14;
      let x = e.clientX + pad, y = e.clientY + pad;
      const r = tip.getBoundingClientRect();
      if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - pad;
      if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - pad;
      tip.style.left = Math.max(4, x) + 'px';
      tip.style.top = Math.max(4, y) + 'px';
    });

    svg.addEventListener('mouseout', e => {
      const t = e.target;
      if (t.__n) { t.classList.remove('hot'); svg.classList.remove('dim'); }
      tip.hidden = true;
    });

    svg.addEventListener('click', e => {
      const t = e.target;
      if (!t.__n) return;
      const n = t.__n;
      if (n.isdir && !n.agg && n.path) {
        navigate(n.path);
      } else if (n.agg) {
        const items = (n._hidden || []).slice();
        if (!items.length) return;
        SEL.clear();
        S.group = { label: n.name.replace(/[▸▾]/g, '').trim(), items: items };
        updateSelBar();
        renderCrumbs();
        renderTreemap();
        renderTable();
        renderStats();
      } else {
        toast(n.name + ' · ' + fmt(n.size));
      }
    });
  }

  /* ---------------------------------------------------------------- 列表 */
  function sortItems(items) {
    const k = S.sortKey, d = S.sortDir;
    items.sort((a, b) => {
      if (k === 'name') return a.name.localeCompare(b.name, 'zh-CN') * d;
      if (a.isdir !== b.isdir) return a.isdir ? -1 : 1;
      return ((a[k] || 0) - (b[k] || 0)) * d;
    });
  }

  /* ---------------------------------------------------------------- 选择与删除 */
  const SEL = new Map(); // path -> item

  function updateSelBar() {
    const bar = $('#selbar');
    if (!SEL.size) { bar.hidden = true; return; }
    let bytes = 0, dirs = 0;
    for (const it of SEL.values()) {
      bytes += it.size || 0;
      if (it.isdir) dirs++;
    }
    bar.hidden = false;
    $('#selInfo').textContent = '已选 ' + SEL.size + ' 项' +
      (dirs ? '（含 ' + dirs + ' 个文件夹）' : '') + ' · ' + fmt(bytes);
  }

  function toggleSel(it, tr) {
    if (!it.path || it.agg) {
      toast('「其他 N 项」是聚合块，请先进入所在文件夹再选择');
      return;
    }
    if (SEL.has(it.path)) SEL.delete(it.path);
    else SEL.set(it.path, it);
    if (tr) tr.classList.toggle('picked', SEL.has(it.path));
    updateSelBar();
  }

  function renderTable() {
    const tb = $('#tbody');
    tb.innerHTML = '';
    const src = S.group ? S.group.items : (S.node && S.node.children);
    if (!src || !src.length) {
      tb.innerHTML = '<tr><td colspan="6" style="color:#55677c;padding:14px 2px;font-size:12px">' +
        (S.node ? '这个目录是空的' : '还没有数据，点右上角「登录网盘」或「先看演示数据」') + '</td></tr>';
      return;
    }
    const total = S.group
      ? (S.group.items.reduce((a, b) => a + (b.size || 0), 0) || 1)
      : (S.node.size || 1);
    const items = src.slice();
    sortItems(items);
    const frag = document.createDocumentFragment();
    for (const it of items) {
      const tr = document.createElement('tr');
      if (it.isdir) tr.className = 'folder';
      if (it.agg) tr.className = 'agg';
      if (SEL.has(it.path)) tr.classList.add('picked');
      const pct = it.size / total * 100;

      const td0 = document.createElement('td');
      td0.innerHTML = '<div class="ckwrap"><input type="checkbox" class="ck"></div>';
      const ck = td0.querySelector('input');
      ck.checked = SEL.has(it.path);
      ck.disabled = !it.path || !!it.agg;
      ck.onclick = ev => { ev.stopPropagation(); toggleSel(it, tr); };

      const td1 = document.createElement('td');
      td1.innerHTML = '<div class="nm"><i class="dot"></i><span></span></div>';
      td1.querySelector('.dot').style.background = colorOf(it);
      td1.querySelector('span').textContent = it.name;
      td1.querySelector('span').title = it.path || it.name;

      const td2 = document.createElement('td');
      td2.className = 'sz';
      td2.textContent = fmt(it.size);

      const td3 = document.createElement('td');
      td3.innerHTML = '<div class="pc"><i></i><span></span></div>';
      td3.querySelector('i').style.width = Math.max(pct, 0.6) + '%';
      td3.querySelector('i').style.background = it.isdir ? '#3b608a' : colorOf(it);
      td3.querySelector('span').textContent = (pct < 0.01 ? '<0.01' : pct.toFixed(1)) + '%';

      const td4 = document.createElement('td');
      td4.className = 'ct';
      td4.textContent = it.isdir ? fmtNum(it.nfiles) : '—';

      const td5 = document.createElement('td');
      td5.className = 'tm';
      td5.textContent = fmtDate(it.mtime);

      tr.append(td0, td1, td2, td3, td4, td5);
      tr.onclick = () => {
        if (it.isdir && !it.agg && it.path) navigate(it.path);
        else toggleSel(it, tr);
      };
      frag.appendChild(tr);
    }
    tb.appendChild(frag);
  }

  function openDelModal() {
    if (!SEL.size) return;
    const items = Array.from(SEL.values()).sort((a, b) => (b.size || 0) - (a.size || 0));
    const bytes = items.reduce((a, b) => a + (b.size || 0), 0);
    $('#delStat').innerHTML = '共 <b>' + items.length + '</b> 项，合计 <b>' + fmt(bytes) + '</b>';
    const list = $('#delList');
    list.innerHTML = '';
    items.slice(0, 40).forEach(it => {
      const row = document.createElement('div');
      row.className = 'delrow';
      row.innerHTML = '<span class="n"></span><span class="s"></span>';
      const n = row.querySelector('.n');
      n.textContent = it.isdir ? it.name + ' /' : it.name;
      n.title = it.path || it.name;
      row.querySelector('.s').textContent = fmt(it.size);
      list.appendChild(row);
    });
    if (items.length > 40) {
      const row = document.createElement('div');
      row.className = 'delrow more';
      row.innerHTML = '<span class="n">…… 其余 ' + (items.length - 40) + ' 项</span><span class="s"></span>';
      list.appendChild(row);
    }
    $('#delErr').hidden = true;
    $('#delMask').hidden = false;
  }

  async function doDelete() {
    const paths = Array.from(SEL.keys()).filter(Boolean);
    if (!paths.length) return;
    const btn = $('#delOk');
    btn.disabled = true;
    btn.textContent = '删除中…';
    try {
      const j = await api('/api/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths })
      });
      SEL.clear();
      updateSelBar();
      $('#delMask').hidden = true;
      toast('已移入网盘回收站：' + j.deleted + ' 项', 'ok');
      await navigate(S.path);
      refreshQuota();
    } catch (e) {
      const err = $('#delErr');
      err.textContent = e.message;
      err.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = '确认删除';
    }
  }

  /* ---------------------------------------------------------------- 侧栏 */
  function renderStats() {
    const n = S.node;
    $('#stSize').textContent = n ? fmt(n.size) : '—';
    $('#stFiles').textContent = n ? fmtNum(n.nfiles) : '—';
    $('#stItems').textContent = n && n.children ? fmtNum(n.children.length) : '—';
    const q = S.quota;
    if (n && q && q.total) {
      $('#stPct').textContent = (n.size / q.total * 100).toFixed(1) + '%';
    } else {
      $('#stPct').textContent = '—';
    }
    const lg = $('#legend');
    if (lg) lg.hidden = !(n && n.children && n.children.length);
    const scanBtn = $('#btnScan');
    if (scanBtn) scanBtn.textContent = (S.hasData || n) ? '重新扫描' : '扫描';
    if (S.group) {
      const gt = S.group.items.reduce((a, b) => a + (b.size || 0), 0);
      $('#vizTitle').innerHTML = '<b>' + escapeHtml(S.group.label) + '</b> · ' + fmt(gt) +
        '（临时分组视图，点面包屑 × 退出）';
    } else {
      $('#vizTitle').innerHTML = n
        ? '<b>' + escapeHtml(n.name || '/') + '</b> · ' + fmt(n.size) + ' · ' + fmtNum(n.nfiles) + ' 个文件'
        : '尚未加载数据';
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  async function loadKinds() {
    try {
      const j = await api('/api/kinds?path=' + encodeURIComponent(S.path));
      const items = j.items.filter(x => x.size > 0);
      const total = items.reduce((a, b) => a + b.size, 0) || 1;
      const bar = $('#kindBar'), list = $('#kindList');
      bar.innerHTML = ''; list.innerHTML = '';
      if (!items.length) {
        list.innerHTML = '<div style="color:#5b6b7e;font-size:11.5px">暂无文件</div>';
        return;
      }
      for (const it of items) {
        const c = (CAT[it.cat] || CAT.other).c;
        const seg = document.createElement('i');
        seg.style.width = (it.size / total * 100) + '%';
        seg.style.background = c;
        seg.title = (CAT[it.cat] || CAT.other).n + ' ' + fmt(it.size);
        bar.appendChild(seg);

        const row = document.createElement('div');
        row.className = 'kindrow';
        row.innerHTML = '<i class="kd"></i><span class="kn"></span><span class="kv"></span><span class="kp"></span>';
        row.querySelector('.kd').style.background = c;
        row.querySelector('.kn').textContent = (CAT[it.cat] || CAT.other).n;
        row.querySelector('.kv').textContent = fmt(it.size);
        row.querySelector('.kp').textContent = (it.size / total * 100).toFixed(1) + '%';
        list.appendChild(row);
      }
    } catch (e) { /* 忽略 */ }
  }

  async function loadTop() {
    try {
      const j = await api('/api/top?limit=12&path=' + encodeURIComponent(S.path));
      const box = $('#topList');
      box.innerHTML = '';
      if (!j.items.length) {
        box.innerHTML = '<div style="color:#5b6b7e;font-size:11.5px">暂无文件</div>';
        return;
      }
      for (const it of j.items) {
        const row = document.createElement('div');
        row.className = 'item';
        row.innerHTML = '<i class="id"></i><div class="in"><b></b><span></span></div><div class="iv"></div>';
        row.querySelector('.id').style.background = (CAT[it.cat] || CAT.other).c;
        row.querySelector('b').textContent = it.name;
        row.querySelector('span').textContent = it.parent;
        row.querySelector('span').title = it.path;
        row.querySelector('.iv').textContent = fmt(it.size);
        row.onclick = () => navigate(it.parent);
        box.appendChild(row);
      }
    } catch (e) { /* 忽略 */ }
  }

  async function refreshQuota() {
    if (!S.loggedIn) {
      $('#qUsed').textContent = '未登录';
      $('#qFree').textContent = '登录后显示容量';
      $('#qbarFill').style.width = '0%';
      $('#qBigFill').style.width = '0%';
      $('#quotaMini').hidden = true;
      return;
    }
    try {
      const j = await api('/api/quota');
      S.quota = j.quota;
      const q = j.quota;
      const pct = q.total ? (q.used / q.total * 100) : 0;
      $('#qUsed').textContent = fmt(q.used) + ' / ' + fmt(q.total);
      $('#qFree').textContent = '剩余 ' + fmt(q.free) + ' · 已用 ' + pct.toFixed(1) + '%';
      $('#qbarFill').style.width = pct + '%';
      $('#qBigFill').style.width = pct + '%';
      $('#qBigFill').style.display = '';
      $('#quotaMini').hidden = false;
      $('#quotaText').textContent = fmt(q.used) + ' / ' + fmt(q.total);
      if (S.node) renderStats();
    } catch (e) {
      // 容量接口不可用（扫码登录的会话常见）：用扫描结果兜底，别误显示“未登录”
      const scanned = (S.path === '/' && S.node ? S.node.size : 0) || S.scannedTotal || 0;
      $('#qUsed').textContent = scanned ? fmt(scanned) : '—';
      $('#qFree').textContent = scanned
        ? '已扫描内容合计 · 容量接口暂不可用'
        : '容量接口暂不可用';
      $('#qbarFill').style.width = '0%';
      $('#qBigFill').style.display = 'none';
      $('#quotaMini').hidden = true;
    }
  }

  /* ---------------------------------------------------------------- 导航 */
  async function navigate(path) {
    if (S.path !== (path || '/')) { SEL.clear(); AGG.clear(); }
    S.group = null;
    S.path = path || '/';
    updateSelBar();
    renderCrumbs();
    try {
      const j = await api('/api/children?path=' + encodeURIComponent(S.path) + '&depth=' + S.depth);
      S.node = j.node;
    } catch (e) {
      if (e.auth) { handleAuthLost(); return; }
      toast(e.message, 'err');
      S.node = null;
    }
    if (S.path === '/' && S.node) S.scannedTotal = S.node.size;
    const hasKids = S.node && S.node.children && S.node.children.length;
    $('#empty').hidden = !!hasKids;
    renderTreemap();
    renderTable();
    renderStats();
    loadKinds();
    loadTop();
  }

  function goUp() {
    if (S.path === '/' || !S.path) return;
    const parts = S.path.split('/').filter(Boolean);
    parts.pop();
    navigate('/' + parts.join('/'));
  }

  function handleAuthLost() {
    S.loggedIn = false;
    updateAuthUI();
    toast('登录状态已失效，请重新登录', 'err');
    openModal();
  }

  /* ---------------------------------------------------------------- 登录 */
  function updateAuthUI() {
    const btn = $('#btnLogin');
    if (S.loggedIn) {
      btn.textContent = '退出登录';
      btn.classList.remove('primary');
      btn.classList.add('ghost');
    } else {
      btn.textContent = '登录网盘';
      btn.classList.add('primary');
      btn.classList.remove('ghost');
    }
  }

  let modalTab = 'qr';
  function openModal() {
    $('#mask').hidden = false;
    $('#mErr').hidden = true;
    setTab(modalTab);
  }
  function closeModal() { $('#mask').hidden = true; stopQr(); }

  function setTab(tab) {
    modalTab = tab;
    $$('.tab').forEach(t => t.classList.toggle('on', t.dataset.tab === tab));
    $$('.pane').forEach(p => { p.hidden = p.dataset.pane !== tab; });
    const submit = $('#mSubmit');
    if (submit) submit.hidden = (tab === 'qr');
    if (tab === 'qr') startQr(false);
    else stopQr();
  }

  /* ---------------------------------------------------------- 扫码登录 */
  let qrTimer = null;
  let qrCache = { status: 'idle', img: '', hint: '' };

  function stopQr() {
    if (qrTimer) { clearInterval(qrTimer); qrTimer = null; }
  }

  async function startQr(force) {
    stopQr();
    const dead = ['expired', 'error', 'confirmed', 'idle'];
    if (force || !qrCache.img || dead.indexOf(qrCache.status) >= 0) {
      paintQr({ status: 'loading', img: '', hint: '正在生成二维码…' });
      try {
        const j = await api('/api/qr/new', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force: !!force })
        });
        qrCache = j;
        paintQr(j);
      } catch (e) {
        paintQr({ status: 'error', img: '', hint: e.message });
        return;
      }
    } else {
      paintQr(qrCache);
    }
    qrTimer = setInterval(refreshQr, 1300);
  }

  async function refreshQr() {
    try {
      const j = await api('/api/qr/status');
      qrCache = j;
      paintQr(j);
    } catch (e) { /* 网络抖动忽略 */ }
  }

  function paintQr(st) {
    const img = $('#qrImg'), ov = $('#qrOv'), dot = $('#qrDot'), tip = $('#qrTip');
    if (!img) return;
    if (st.img && img.getAttribute('src') !== st.img) img.src = st.img;
    img.hidden = !st.img;

    const bad = (st.status === 'expired' || st.status === 'error');
    ov.hidden = !bad;
    if (bad) {
      $('#qrOvText').textContent = st.status === 'error' ? (st.hint || '二维码生成失败') : '二维码已过期';
    }

    dot.className = '';
    if (st.status === 'waiting') {
      dot.className = 'ok';
      tip.textContent = st.hint || '等待扫码…';
    } else if (st.status === 'scanned') {
      dot.className = 'warn';
      tip.textContent = st.hint || '已扫码，请在手机上点击「确认登录」';
    } else if (st.status === 'loading') {
      tip.textContent = '正在生成二维码…';
    } else if (st.status === 'confirmed') {
      dot.className = 'ok';
      tip.textContent = '登录成功，正在进入…';
      stopQr();
      onQrLogin(st);
    } else if (st.status === 'expired') {
      dot.className = 'err';
      tip.textContent = '二维码已过期，请点击刷新';
    } else if (st.status === 'error') {
      dot.className = 'err';
      tip.textContent = st.hint || '二维码生成失败';
    } else if (st.status === 'cancelled') {
      tip.textContent = '已取消';
    } else {
      tip.textContent = st.hint || '正在准备…';
    }
  }

  async function onQrLogin(st) {
    stopQr();
    S.loggedIn = true;
    updateAuthUI();
    closeModal();
    toast('登录成功' + (st.user ? '：' + st.user : '') + '，开始扫描…', 'ok');
    await refreshQuota();
    startScan();
  }

  async function submitLogin() {
    const btn = $('#mSubmit');
    const err = $('#mErr');
    err.hidden = true;
    const payload = { mode: modalTab };
    if (modalTab === 'cookie') {
      payload.cookie = $('#inpCookie').value.trim();
      if (!payload.cookie) { showErr('请先粘贴 Cookie'); return; }
    } else {
      payload.token = $('#inpToken').value.trim();
      if (!payload.token) { showErr('请先粘贴 access_token'); return; }
    }
    btn.disabled = true;
    btn.textContent = '验证中…';
    try {
      await api('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      S.loggedIn = true;
      updateAuthUI();
      closeModal();
      await refreshQuota();
      toast('登录成功，开始扫描…', 'ok');
      startScan();
    } catch (e) {
      showErr(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '登录并扫描';
    }
  }

  function showErr(msg) {
    const err = $('#mErr');
    err.textContent = msg;
    err.hidden = false;
  }

  /* ---------------------------------------------------------------- 扫描 */
  async function startScan() {
    if (S.scanning) return;
    try {
      await api('/api/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: '/', workers: 4 })
      });
    } catch (e) {
      if (e.auth) { handleAuthLost(); return; }
      toast(e.message, 'err');
      return;
    }
    S.scanning = true;
    $('#btnScan').disabled = true;
    $('#scanbar').hidden = false;
    $('#sbTitle').textContent = '正在扫描…';
    $('#sbSub').textContent = '正在枚举根目录';
    $('#sbFill').style.width = '2%';
    $('#sbStat').textContent = '0 个文件夹';
    pollScan();
  }

  function pollScan() {
    clearInterval(S.timer);
    S.timer = setInterval(async () => {
      let st;
      try {
        const j = await api('/api/scan/status');
        st = j.status;
      } catch (e) { return; }
      const pct = st.dirTotal ? Math.min(99, st.dirDone / st.dirTotal * 100) : 3;
      $('#sbFill').style.width = pct + '%';
      $('#sbStat').textContent =
        fmtNum(st.dirDone) + '/' + fmtNum(st.dirTotal) + ' 文件夹 · ' +
        fmtNum(st.fileCount) + ' 文件 · ' + fmt(st.totalBytes) + ' · ' + fmtSpan(st.elapsed);
      $('#sbSub').textContent = st.current || '准备中';
      $('#sbTitle').textContent = st.running ? '正在扫描…' : (st.error ? '扫描中断' : '扫描完成');

      if (!st.running) {
        clearInterval(S.timer);
        S.scanning = false;
        $('#btnScan').disabled = false;
        $('#sbFill').style.width = '100%';
        setTimeout(() => { $('#scanbar').hidden = true; }, 900);
        if (st.error) toast(st.error, 'err');
        else if (st.errors && st.errors.length) toast('扫描完成，但有 ' + st.errors.length + ' 个目录失败（可能无权限）', 'err');
        else toast('扫描完成：' + fmtNum(st.fileCount) + ' 个文件，' + fmt(st.totalBytes), 'ok');
        S.hasData = true;
        await navigate('/');   // 先加载新树，S.scannedTotal 才会更新
        await refreshQuota();  // 再刷新容量（含兜底显示）
      }
    }, 650);
  }

  /* ---------------------------------------------------------------- 搜索 */
  let searchTimer = null;
  function bindSearch() {
    const inp = $('#search');
    inp.addEventListener('input', () => {
      clearTimeout(searchTimer);
      const q = inp.value.trim();
      if (!q) { loadTop(); return; }
      searchTimer = setTimeout(async () => {
        try {
          const j = await api('/api/search?q=' + encodeURIComponent(q));
          const box = $('#searchList');
          box.innerHTML = '';
          if (!j.items.length) {
            box.innerHTML = '<div style="color:#5b6b7e;font-size:11.5px">没有匹配的文件</div>';
            return;
          }
          for (const it of j.items.slice(0, 40)) {
            const row = document.createElement('div');
            row.className = 'item';
            row.innerHTML = '<i class="id"></i><div class="in"><b></b><span></span></div><div class="iv"></div>';
            row.querySelector('.id').style.background = (CAT[it.cat] || CAT.other).c;
            row.querySelector('b').textContent = it.name;
            row.querySelector('span').textContent = it.path;
            row.querySelector('.iv').textContent = fmt(it.size);
            row.onclick = () => navigate(it.parent);
            box.appendChild(row);
          }
        } catch (e) { /* 忽略 */ }
      }, 280);
    });
  }

  /* ---------------------------------------------------------------- 事件绑定 */
  function bindEvents() {
    $('#btnUp').onclick = goUp;
    $('#btnLogin').onclick = () => {
      if (S.loggedIn) {
        api('/api/logout', { method: 'POST' }).then(() => {
          S.loggedIn = false;
          updateAuthUI();
          refreshQuota();
          toast('已退出登录');
        });
      } else {
        openModal();
      }
    };
    $('#btnScan').onclick = () => {
      if (!S.loggedIn) { openModal(); return; }
      startScan();
    };
    $('#mClose').onclick = closeModal;
    $('#mask').onclick = e => { if (e.target === $('#mask')) closeModal(); };
    $('#mSubmit').onclick = submitLogin;
    $$('.tab').forEach(t => t.onclick = () => setTab(t.dataset.tab));

    $('#ckAll').onchange = e => {
      SEL.clear();
      if (e.target.checked && S.node && S.node.children) {
        S.node.children.forEach(it => { if (it.path && !it.agg) SEL.set(it.path, it); });
      }
      renderTable();
      updateSelBar();
    };
    $('#selClear').onclick = () => { SEL.clear(); renderTable(); updateSelBar(); };
    $('#selDel').onclick = openDelModal;
    $('#delClose').onclick = () => { $('#delMask').hidden = true; };
    $('#delCancel').onclick = () => { $('#delMask').hidden = true; };
    $('#delMask').onclick = e => { if (e.target === $('#delMask')) $('#delMask').hidden = true; };
    $('#delOk').onclick = doDelete;

    $('#qrRefresh').onclick = () => startQr(true);
    $('#qrOvBtn').onclick = () => startQr(true);

    $('#mDemo').onclick = loadDemo;
    $('#emptyDemo').onclick = loadDemo;
    $('#emptyLogin').onclick = openModal;

    $('#btnExport').onclick = () => {
      if (!S.node) { toast('没有可导出的数据'); return; }
      window.location = '/api/export?path=' + encodeURIComponent(S.path);
    };
    $('#btnExportHtml').onclick = () => {
      if (!S.node) { toast('没有可导出的数据'); return; }
      toast('正在生成 HTML 报告…');
      window.location = '/api/export?fmt=html&path=' + encodeURIComponent(S.path);
    };

    $$('#depthSeg button').forEach(b => b.onclick = () => {
      $$('#depthSeg button').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      S.depth = parseInt(b.dataset.d, 10);
      if (S.node) navigate(S.path);
    });

    $$('.tblhead > div').forEach(th => th.onclick = () => {
      const k = th.dataset.k;
      if (S.sortKey === k) S.sortDir = -S.sortDir;
      else { S.sortKey = k; S.sortDir = k === 'name' ? 1 : -1; }
      renderTable();
    });

    $('#sbCancel').onclick = async () => {
      try { await api('/api/scan/cancel', { method: 'POST' }); } catch (e) { /* ignore */ }
      $('#sbTitle').textContent = '正在停止…';
    };

    document.addEventListener('keydown', e => {
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      if (e.key === 'Backspace') { e.preventDefault(); goUp(); }
      if (e.key === 'Escape') { closeModal(); $('#delMask').hidden = true; }
    });

    let rz = null;
    window.addEventListener('resize', () => {
      clearTimeout(rz);
      rz = setTimeout(renderTreemap, 120);
    });

    bindTreemapEvents();
    bindSearch();
  }

  async function loadDemo() {
    try {
      const j = await api('/api/demo', { method: 'POST' });
      closeModal();
      S.hasData = true;
      $('#empty').hidden = true;
      toast('已载入演示数据', 'ok');
      await navigate(j.path);
    } catch (e) {
      toast(e.message, 'err');
    }
  }

  /* ---------------------------------------------------------------- 启动 */
  async function init() {
    bindEvents();

    // 图例
    const legend = $('#legend');
    legend.innerHTML = '<span class="lgtog">▦ 图例</span><span class="lgitems">' +
      Object.keys(CAT).map(k => '<span><i style="background:' + CAT[k].c + '"></i>' + CAT[k].n + '</span>').join('') +
      '</span>';

    let session = null;
    try { session = await api('/api/session'); } catch (e) { toast(e.message, 'err'); }
    if (session) {
      S.loggedIn = !!session.loggedIn;
      S.hasData = !!session.hasData;
      S.scannedTotal = session.scanned || 0;
      updateAuthUI();
      await refreshQuota();
      if (S.hasData) {
        // 有历史数据：优先进根目录，其次演示目录
        try {
          const r = await api('/api/children?path=%2F&depth=' + S.depth);
          if (r.node) { await navigate('/'); return; }
        } catch (e) { /* ignore */ }
        if (session.demoPath) { await navigate(session.demoPath); return; }
      }
    } else {
      updateAuthUI();
    }
    $('#empty').hidden = false;
    renderStats();
    renderTable();
    loadKinds();
    loadTop();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
