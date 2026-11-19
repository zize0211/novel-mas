"""novel-mas 实验台 —— 零依赖本地页面。

跑法：
    $env:PYTHONPATH = 'E:\\novel-mas\\src'
    python -m novel_mas.web

然后浏览器开 http://127.0.0.1:8787

为什么用标准库 http.server：项目到现在还是 dependencies=[]，
为了一个实验界面去装 Flask 不值当。页面本身也就一屏表单，够用。
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import base as db
from .llm import FakeProvider, OpenAIProvider
from .orchestrator import Orchestrator
from .types import CurvePoint

PORT = 8787
HOST = "127.0.0.1"
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _demo_curve(prompt: str, chapters: int = 20) -> list[CurvePoint]:
    """不花钱的演示曲线：固定形状（先缓降到底，再回暖）。

    假模型不会真给 20 个点（它返回空数组），所以要给它一条能画出来的线，
    并在响应里标 source=fake —— 别让人误以为这是模型的产物。
    """
    pts: list[CurvePoint] = []
    for i in range(1, chapters + 1):
        t = (i - 1) / (chapters - 1) if chapters > 1 else 0.0
        if t < 0.6:
            target = 60 - 30 * (t / 0.6)
        else:
            target = 30 + 40 * ((t - 0.6) / 0.4)
        pts.append(CurvePoint(chapter=i, target=int(round(target)), tolerance=15))
    return pts


def _read_env() -> dict[str, str]:
    """读 .env 成 dict（只做最简单的 KEY=VALUE，不搞变量展开）。"""
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _write_env(updates: dict[str, str]) -> Path | None:
    """把改动写回 .env，写前备份。返回备份路径（原来没有 .env 时为 None）。"""
    backup: Path | None = None
    lines: list[str] = []
    if ENV_PATH.exists():
        backup = ENV_PATH.with_name(f".env.bak-{datetime.now():%Y%m%d-%H%M%S}")
        backup.write_bytes(ENV_PATH.read_bytes())
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if "=" in line and key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    return backup


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>novel-mas 实验台</title>
<style>
  :root {
    --bg: #14161c; --panel: #1b1e27; --line: #2b3040;
    --text: #d8dce8; --dim: #7d859c; --accent: #6ea8fe;
    --ok: #57c785; --warn: #e0b33c; --bad: #e06c6c;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  }
  header {
    display: flex; align-items: center; gap: 14px;
    padding: 12px 20px; border-bottom: 1px solid var(--line);
    background: var(--panel); position: sticky; top: 0; z-index: 9;
  }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; }
  header .book { margin-left: auto; display: flex; align-items: center; gap: 8px; }
  main { display: grid; grid-template-columns: 420px 1fr; gap: 16px; padding: 16px; }
  .col { display: flex; flex-direction: column; gap: 12px; }
  section { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; }
  section h2 { font-size: 13px; margin: 0 0 10px; color: var(--accent); font-weight: 600; }
  label { display: block; font-size: 12px; color: var(--dim); margin: 8px 0 4px; }
  input, textarea, select {
    width: 100%; background: #0f1117; color: var(--text);
    border: 1px solid var(--line); border-radius: 6px; padding: 7px 9px;
    font: inherit; font-size: 13px;
  }
  textarea { resize: vertical; min-height: 64px; }
  input[type=checkbox] { width: auto; }
  button {
    margin-top: 10px; background: var(--accent); color: #10131a;
    border: 0; border-radius: 6px; padding: 8px 16px;
    font: inherit; font-weight: 600; cursor: pointer;
  }
  button:disabled { opacity: .5; cursor: not-allowed; }
  button.ghost { background: transparent; color: var(--dim); border: 1px solid var(--line); font-weight: 400; }
  .row { display: flex; gap: 8px; align-items: center; }
  .row > * { flex: 1; }
  .row > button { flex: 0 0 auto; margin-top: 0; }
  .verdict { font-size: 20px; font-weight: 700; }
  .pass { color: var(--ok); } .violated, .bad { color: var(--bad); }
  .drifted, .warn { color: var(--warn); }
  .kv { display: flex; gap: 18px; flex-wrap: wrap; font-size: 13px; color: var(--dim); }
  .kv b { color: var(--text); font-weight: 600; }
  pre.text {
    background: #0f1117; border: 1px solid var(--line); border-radius: 6px;
    padding: 12px; white-space: pre-wrap; word-break: break-word;
    max-height: 420px; overflow: auto; font: 13px/1.8 "Microsoft YaHei", sans-serif;
    margin: 0;
  }
  ul.ev { margin: 8px 0 0; padding-left: 18px; color: var(--dim); font-size: 13px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }
  th { color: var(--dim); font-weight: 500; font-size: 12px; }
  .toast {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #232838; border: 1px solid var(--line); color: var(--text);
    padding: 9px 18px; border-radius: 8px; opacity: 0; transition: opacity .2s;
    pointer-events: none; max-width: 80vw;
  }
  .toast.show { opacity: 1; }
  .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid #ffffff44;
    border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .muted { color: var(--dim); font-size: 12px; }
</style>
</head>
<body>

<header>
  <h1>novel-mas 实验台</h1>
  <span class="muted">零依赖 · 本地</span>
  <div class="book">
    <label style="margin:0">书号</label>
    <input id="bookId" value="demo" style="width:140px">
    <button class="ghost" onclick="loadHistory()">刷新历史</button>
  </div>
</header>

<main>
  <div class="col">

    <section>
      <h2>① 灌世界观</h2>
      <textarea id="worldText" placeholder="一句话一条，用「。」分隔。例：月光城的雨从不停。城东有座旧钟楼。">月光城的雨从不停。城东有座旧钟楼，钟声一响，全城的人都会停下手里的活。李家在城南摆豆腐摊，已经三代。</textarea>
      <button onclick="act('worldview', {text: val('worldText')})">灌入</button>
    </section>

    <section>
      <h2>② 导入人物</h2>
      <label>名字</label>
      <input id="charName" value="李三">
      <label>人设</label>
      <textarea id="charProfile" style="min-height:56px">豆腐摊第三代，怕生，说话前会先摸一下耳朵。</textarea>
      <label>分量</label>
      <select id="charImp">
        <option value="lead">lead（主角级）</option>
        <option value="support">support（配角）</option>
        <option value="minor" selected>minor（过场）</option>
      </select>
      <button onclick="act('character', {name: val('charName'), profile: val('charProfile'), importance: val('charImp')})">导入</button>
    </section>

    <section>
      <h2>③ 设走向曲线</h2>
      <label>用一句话说清这一路的情绪走向</label>
      <textarea id="curvePrompt" style="min-height:56px">前十章平静，中段越来越压抑，最后两章回暖。</textarea>
      <label style="display:flex;align-items:center;gap:8px;margin-top:10px">
        <input type="checkbox" id="curveReal"> 用真模型生成（不勾也用假模型出个样子）
      </label>
      <label style="margin-top:12px">手填（可选，<code>章:分</code> 逗号分隔）</label>
      <input id="curvePoints" placeholder="1:50, 2:60, 3:75">
      <div class="row" style="margin-top:10px">
        <button onclick="createCurve(false)">按描述生成</button>
        <button class="ghost" onclick="createCurve(true)">按手填写入</button>
      </div>
      <div id="curveChart" class="muted" style="margin-top:12px">还没有曲线。</div>
    </section>

    <section>
      <h2>④ 写这一章</h2>
      <div class="row">
        <div><label>第几章</label><input id="chapter" value="1"></div>
        <div style="flex:2"><label>大纲</label><input id="brief" value="李三在巷口捡到一枚铜钱，抬头看见钟楼上有个人影。"></div>
      </div>
      <label style="margin-top:10px">文风</label>
      <div class="row">
        <select id="style">
          <option value="日常" selected>日常</option>
          <option value="冷峻">冷峻</option>
          <option value="口语化">口语化</option>
          <option value="文言">文言</option>
        </select>
        <div style="flex:2"><input id="styleCustom" placeholder="自定义（留空就用左边选的）"></div>
      </div>
      <label style="display:flex;align-items:center;gap:8px;margin-top:10px">
        <input type="checkbox" id="realModel"> 用真模型（要花钱，约 3000 token）
      </label>
      <button onclick="writeChapter()">写</button>
    </section>

  </div>

  <div class="col">

    <section>
      <h2>最新一章</h2>
      <div id="result" class="muted">还没跑过。左边填好，点「写」。</div>
    </section>

    <section>
      <h2>模型配置</h2>
      <div id="cfg" class="muted">读着…</div>
      <label style="margin-top:10px">base_url</label>
      <input id="cfgBase" placeholder="https://api.deepseek.com/v1">
      <label>model</label>
      <input id="cfgModel" placeholder="deepseek-chat">
      <div class="row" style="margin-top:10px">
        <button onclick="saveConfig()">保存</button>
        <button class="ghost" onclick="loadConfig()">重读</button>
      </div>
      <div class="muted" style="margin-top:8px">key 只从 .env 读，页面不保存也不回显；改完写回 .env 并留一份备份。</div>
    </section>

    <section>
      <h2>本书概况 <button class="ghost" style="float:right" onclick="loadBook()">刷新</button></h2>
      <div id="bookBox" class="muted">点「刷新」看书里有什么。</div>
    </section>

    <section>
      <h2>历史成绩</h2>
      <div id="history" class="muted">点右上「刷新历史」。</div>
    </section>

  </div>
</main>

<div id="toast" class="toast"></div>

<script>
const $ = id => document.getElementById(id);
const val = id => $(id).value.trim();

function toast(msg, ms = 2600) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), ms);
}

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

async function get(path) {
  const res = await fetch(path);
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

function busy(btn, on, label) {
  if (on) {
    btn.dataset.old = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> ' + (label || '跑着');
  } else {
    btn.disabled = false;
    btn.textContent = btn.dataset.old || '好了';
  }
}

async function act(what, payload) {
  const btn = event.currentTarget;
  busy(btn, true);
  try {
    const book = val('bookId') || 'demo';
    const r = await post('/api/' + what, {book_id: book, ...payload});
    toast(r.message || '好了');
    loadBook();
  } catch (e) {
    toast('失败：' + e.message, 5000);
  } finally {
    busy(btn, false);
  }
}

async function createCurve(fromPoints) {
  const btn = event.currentTarget;
  busy(btn, true, '生成中…');
  const box = $('curveChart');
  try {
    const book = val('bookId') || 'demo';
    const payload = {book_id: book};
    if (fromPoints) {
      const raw = val('curvePoints');
      if (!raw) throw new Error('手填框是空的，写个 1:50, 2:60 这样');
      payload.points = raw.split(',').map(s => s.trim()).filter(Boolean).map(s => {
        const parts = s.split(':').map(x => x.trim());
        return {
          chapter: parseInt(parts[0], 10),
          target: parseInt(parts[1], 10),
          tolerance: parts[2] ? parseInt(parts[2], 10) : 15,
        };
      });
    } else {
      payload.prompt = val('curvePrompt');
      payload.real_model = $('curveReal').checked;
    }
    const r = await post('/api/curve', payload);
    renderCurve(r);
    toast('曲线写好了：' + r.points.length + ' 个点（' + r.source + '）');
  } catch (e) {
    box.innerHTML = '<span class="bad">失败：' + esc(e.message) + '</span>';
  } finally {
    busy(btn, false);
  }
}

function renderCurve(r) {
  const pts = r.points || [];
  const box = $('curveChart');
  if (!pts.length) { box.innerHTML = '<span class="muted">没有点。</span>'; return; }
  const W = 320, H = 130, PAD = 18;
  const chs = pts.map(p => p.chapter);
  const minC = Math.min(...chs), maxC = Math.max(...chs);
  const x = c => PAD + (maxC === minC ? (W - PAD * 2) / 2 : (c - minC) / (maxC - minC) * (W - PAD * 2));
  const y = t => H - PAD - Math.max(0, Math.min(100, t)) / 100 * (H - PAD * 2);
  const line = pts.map(p => x(p.chapter).toFixed(1) + ',' + y(p.target).toFixed(1)).join(' ');
  const warnCh = new Set((r.warnings || []).map(w => w.chapter));
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="150" role="img">';
  [0, 50, 100].forEach(v => {
    svg += '<line x1="' + PAD + '" y1="' + y(v) + '" x2="' + (W - PAD) + '" y2="' + y(v)
         + '" stroke="#888" stroke-opacity="0.25"' + (v === 50 ? ' stroke-dasharray="3 3"' : '') + '/>';
  });
  svg += '<polyline fill="none" stroke="#4a9e8f" stroke-width="2" points="' + line + '"/>';
  for (const p of pts) {
    svg += '<circle cx="' + x(p.chapter).toFixed(1) + '" cy="' + y(p.target).toFixed(1)
         + '" r="2.6" fill="' + (warnCh.has(p.chapter) ? '#d9534f' : '#4a9e8f') + '"/>';
  }
  svg += '</svg>';
  let html = svg;
  if (r.warnings && r.warnings.length) {
    html += '<ul class="ev">' + r.warnings.map(w => '<li>' + esc(w.message) + '</li>').join('') + '</ul>';
  } else {
    html += '<div class="muted">坡度检查：没有断崖。</div>';
  }
  box.innerHTML = html;
}

async function loadConfig() {
  const box = $('cfg');
  try {
    const r = await get('/api/config');
    $('cfgBase').value = r.base_url;
    $('cfgModel').value = r.model;
    box.innerHTML = '当前：<b>' + esc(r.model) + '</b> @ ' + esc(r.base_url)
      + '<br>key：' + (r.has_key ? '已配置（' + esc(r.key_tail) + '）' : '<b class="bad">没有</b>');
  } catch (e) {
    box.innerHTML = '<span class="bad">读不到配置：' + esc(e.message) + '</span>';
  }
}

async function saveConfig() {
  const btn = event.currentTarget;
  busy(btn, true, '保存中…');
  try {
    const r = await post('/api/config', {base_url: val('cfgBase'), model: val('cfgModel')});
    toast(r.backup ? '已保存，旧配置备份为 ' + r.backup : '已保存');
    loadConfig();
  } catch (e) {
    toast('失败：' + e.message, 5000);
  } finally {
    busy(btn, false);
  }
}

async function writeChapter() {
  const btn = event.currentTarget;
  busy(btn, true, '写着…');
  $('result').innerHTML = '<span class="muted">跑着，真模型可能要 30-60 秒…</span>';
  try {
    const r = await post('/api/write', {
      book_id: val('bookId') || 'demo',
      chapter: parseInt(val('chapter') || '1', 10),
      brief: val('brief'),
      real_model: $('realModel').checked,
      style: val('styleCustom') || val('style'),
    });
    renderResult(r);
    loadHistory();
  } catch (e) {
    $('result').innerHTML = '<span class="bad">失败：' + esc(e.message) + '</span>';
  } finally {
    busy(btn, false);
  }
}

function esc(s) {
  return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

function renderResult(r) {
  const cls = r.verdict === 'pass' ? 'pass' : 'violated';
  const tcls = r.trend_verdict === 'on_track' ? 'pass'
             : (r.trend_verdict === 'drifted' ? 'drifted' : 'violated');
  const dv = (r.deviation > 0 ? '+' : '') + r.deviation;
  let html = '';
  html += '<div class="verdict ' + cls + '">第 ' + r.chapter + ' 章</div>';
  html += '<div class="kv" style="margin:8px 0 12px">'
       + '<span>过闸 <b class="' + cls + '">' + r.verdict + '</b></span>'
       + '<span>走向 <b class="' + tcls + '">' + r.trend_verdict + '</b></span>'
       + '<span>走向分 <b>' + r.score + '</b></span>'
       + '<span>目标 <b>' + r.target + '</b></span>'
       + '<span>偏差 <b class="' + tcls + '">' + dv + '</b></span>'
       + '<span>token <b>' + r.tokens + '</b></span>'
       + '<span>字数 <b>' + (r.words || 0) + '</b></span>'
       + '<span>落盘 <b>' + (r.out_path ? '是' : '没写（被拦）') + '</b></span>'
       + '</div>';
  if (r.evidence && r.evidence.length) {
    html += '<ul class="ev">' + r.evidence.map(e => '<li>' + esc(e) + '</li>').join('') + '</ul>';
  }
  html += '<label style="margin-top:14px">正文</label>';
  html += '<pre class="text">' + esc(r.text || '(空)') + '</pre>';
  $('result').innerHTML = html;
}

async function loadBook() {
  const box = $('bookBox');
  try {
    const r = await get('/api/book?book_id=' + encodeURIComponent(val('bookId') || 'demo'));
    const dash = '<span class="muted">（空）</span>';
    let html = '<b>世界观</b><br>' + (r.world.length
      ? r.world.map(w => esc(w.title + '：' + w.body)).join('<br>') : dash);
    html += '<div style="margin-top:8px"><b>人物</b><br>' + (r.characters.length
      ? r.characters.map(c => esc(c.name + '（' + c.importance + '｜' + (c.state || '未记') + '）')
          + (c.profile ? '<br><span class="muted">' + esc(c.profile) + '</span>' : '')).join('<br>')
      : dash) + '</div>';
    html += '<div style="margin-top:8px"><b>曲线</b><br>' + (r.curve.length
      ? r.curve.map(p => '第' + p.chapter + '章 ' + p.target).join(' / ') : dash) + '</div>';
    box.innerHTML = html;
    if (r.curve.length) renderCurve({points: r.curve, warnings: []});
  } catch (e) {
    box.innerHTML = '<span class="bad">' + esc(e.message) + '</span>';
  }
}

async function loadHistory() {
  const box = $('history');
  try {
    const r = await get('/api/history?book_id=' + encodeURIComponent(val('bookId') || 'demo'));
    if (!r.rows.length) { box.innerHTML = '<span class="muted">还没有历史。</span>'; return; }
    let html = '<table><tr><th>章</th><th>分</th><th>目标</th><th>偏差</th><th>判定</th></tr>';
    for (const row of r.rows) {
      const dv = (row.deviation > 0 ? '+' : '') + row.deviation;
      const cls = row.verdict === 'pass' ? 'pass' : (row.verdict === 'violated' ? 'violated' : 'drifted');
      html += '<tr><td>' + row.chapter + '</td><td>' + row.score + '</td><td>' + row.target
           + '</td><td>' + dv + '</td><td class="' + cls + '">' + row.verdict + '</td></tr>';
    }
    html += '</table>';
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = '<span class="bad">' + esc(e.message) + '</span>';
  }
}

loadHistory();
loadConfig();
loadBook();
</script>
</body>
</html>
"""


def _make_orc(book_id: str, real_model: bool, style: str = "日常") -> Orchestrator:
    llm = OpenAIProvider.from_env() if real_model else FakeProvider(style=style)
    return Orchestrator(book_id=book_id, llm=llm, style=style)


class Handler(BaseHTTPRequestHandler):
    server_version = "novel-mas-web/0.1"

    # ---------- 响应辅助 ----------

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _fail(self, msg: str, code: int = 400) -> None:
        self._json({"ok": False, "error": msg}, code)

    # ---------- 路由 ----------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/config":
            self._config_get()
            return
        if parsed.path == "/api/book":
            qs = parse_qs(parsed.query)
            self._book(str((qs.get("book_id") or ["demo"])[0]))
            return
        if parsed.path == "/api/history":
            qs = parse_qs(parsed.query)
            book = (qs.get("book_id") or ["demo"])[0]
            try:
                conn = db.connect("trend.db")
                try:
                    rows = [
                        {
                            "chapter": r["chapter"],
                            "score": r["score"],
                            "target": r["target"],
                            "deviation": r["deviation"],
                            "verdict": r["verdict"],
                        }
                        for r in conn.execute(
                            "SELECT chapter, score, target, deviation, verdict"
                            " FROM chapter_scores WHERE book_id = ? ORDER BY chapter",
                            (book,),
                        )
                    ]
                finally:
                    conn.close()
                self._json({"ok": True, "rows": rows})
            except Exception as exc:  # noqa: BLE001
                self._fail(str(exc), 500)
            return
        self._fail("没有这个路径", 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:  # noqa: BLE001
            self._fail("请求体不是合法 JSON")
            return

        book = str(body.get("book_id") or "demo").strip() or "demo"

        try:
            if parsed.path == "/api/worldview":
                self._worldview(book, body)
            elif parsed.path == "/api/character":
                self._character(book, body)
            elif parsed.path == "/api/curve":
                self._curve(book, body)
            elif parsed.path == "/api/write":
                self._write(book, body)
            elif parsed.path == "/api/config":
                self._config_post(body)
            else:
                self._fail("没有这个路径", 404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._fail(f"{type(exc).__name__}: {exc}", 500)

    # ---------- 各动作 ----------

    def _worldview(self, book: str, body: dict) -> None:
        text = str(body.get("text") or "").strip()
        if not text:
            self._fail("世界观不能空")
            return
        n = Orchestrator(book_id=book, llm=FakeProvider()).ingest_worldview(text)
        self._json({"ok": True, "message": f"灌了 {n} 条（同标题会覆盖，不会堆）"})

    def _character(self, book: str, body: dict) -> None:
        name = str(body.get("name") or "").strip()
        profile = str(body.get("profile") or "").strip()
        importance = str(body.get("importance") or "minor").strip()
        if not name or not profile:
            self._fail("名字和人设都要填")
            return
        reg = Orchestrator(book_id=book, llm=FakeProvider()).registry
        snap = reg.call(
            "character", "import_character",
            book_id=book, name=name, profile=profile, importance=importance,
        )
        self._json({"ok": True, "message": f"导入了 {snap.name}（{snap.importance}）"})

    def _curve(self, book: str, body: dict) -> None:
        raw_points = body.get("points")
        prompt = str(body.get("prompt") or "").strip()
        real = bool(body.get("real_model"))
        if raw_points is None and not prompt:
            self._fail("要么写一句走向描述，要么手填曲线点")
            return

        if raw_points:
            try:
                pts = [
                    CurvePoint(
                        chapter=int(p["chapter"]),
                        target=int(p["target"]),
                        tolerance=int(p.get("tolerance", 15)),
                    )
                    for p in raw_points
                ]
            except (KeyError, TypeError, ValueError) as exc:
                self._fail(f"手填的点格式不对：{exc}")
                return
            result = _make_orc(book, False).registry.call(
                "trend", "set_curve", book_id=book, points=pts
            )
            source = "manual"
        else:
            if real:
                try:
                    llm = OpenAIProvider.from_env()
                except Exception as exc:  # noqa: BLE001
                    self._fail(
                        f"真模型不可用：{exc}（不勾真模型也能演示，用的是固定形状的假曲线）",
                        400,
                    )
                    return
                result = Orchestrator(book_id=book, llm=llm).registry.call(
                    "trend", "set_curve", book_id=book, prompt=prompt
                )
                source = "model"
            else:
                # 假模型不给点（返回空数组），这里塞一条固定形状的演示曲线
                result = _make_orc(book, False).registry.call(
                    "trend", "set_curve", book_id=book, points=_demo_curve(prompt)
                )
                source = "fake"

        curve = result["curve"]
        self._json(
            {
                "ok": True,
                "source": source,
                "points": [
                    {"chapter": p.chapter, "target": p.target, "tolerance": p.tolerance}
                    for p in curve
                ],
                "warnings": result["warnings"],
                "message": f"生成 {len(curve)} 个点",
            }
        )

    def _write(self, book: str, body: dict) -> None:
        chapter = int(body.get("chapter") or 1)
        brief = str(body.get("brief") or "").strip()
        real = bool(body.get("real_model"))
        style = str(body.get("style") or "").strip()[:20] or "日常"
        if not brief:
            self._fail("大纲不能空")
            return
        # 没曲线就直接说人话。写手那边抛的是 LookupError（「第 1 章的曲线不存在」），
        # 到页面上就成了一条看不懂的报错——新书第一次写章几乎必踩。
        conn = db.connect("trend.db")
        try:
            has_curve = conn.execute(
                "SELECT 1 FROM curve WHERE book_id = ? AND chapter = ?", (book, chapter)
            ).fetchone()
        finally:
            conn.close()
        if not has_curve:
            self._fail(
                f"这本书还没有第 {chapter} 章的曲线：先去 ③ 生成或手填，再写这一章", 400
            )
            return
        r = _make_orc(book, real, style).write_chapter(chapter, brief)
        self._json({
            "ok": True,
            "chapter": r.chapter,
            "verdict": r.verdict,
            "trend_verdict": r.trend_verdict,
            "score": r.score,
            "target": r.target,
            "deviation": r.deviation,
            "tokens": r.tokens,
            "words": r.words,
            "evidence": r.evidence,
            "out_path": r.out_path,
            "style": style,
            "text": r.text,
        })

    # ---------- 模型配置（只读展示 + 改端点，不碰 key） ----------

    def _config_payload(self) -> dict:
        env_file = _read_env()
        # 跟 llm.from_env 一个口径：**.env 优先，环境变量兜底**。
        # 反过来的话，面板显示的 key 跟真正在用的 key 可能不是同一个（踩过 401）。
        base_url = (
            env_file.get("NOVEL_BASE_URL")
            or os.environ.get("NOVEL_BASE_URL")
            or "https://api.deepseek.com/v1"
        )
        model = (
            env_file.get("NOVEL_MODEL")
            or os.environ.get("NOVEL_MODEL")
            or "deepseek-chat"
        )
        key = (
            env_file.get("OPENAI_API_KEY")
            or env_file.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or ""
        ).strip()
        return {
            "ok": True,
            "base_url": base_url,
            "model": model,
            "has_key": bool(key),
            "key_tail": ("…" + key[-4:]) if len(key) >= 8 else "",
            "env_file": str(ENV_PATH),
            "writable": True,
        }

    def _config_get(self) -> None:
        self._json(self._config_payload())

    def _book(self, book: str) -> None:
        """一本书现在有什么：世界观、人物、曲线。只读，给页面看底细。"""
        conn = db.connect("world.db")
        try:
            world = [
                dict(r)
                for r in conn.execute(
                    "SELECT category, title, body FROM world_bible"
                    " WHERE book_id = ? ORDER BY id",
                    (book,),
                )
            ]
        finally:
            conn.close()

        conn = db.connect("char.db")
        try:
            characters = [
                dict(r)
                for r in conn.execute(
                    "SELECT name, importance, profile, state, as_of_chapter"
                    " FROM characters WHERE book_id = ? ORDER BY id",
                    (book,),
                )
            ]
        finally:
            conn.close()

        conn = db.connect("trend.db")
        try:
            curve = [
                dict(r)
                for r in conn.execute(
                    "SELECT chapter, target, tolerance FROM curve"
                    " WHERE book_id = ? ORDER BY chapter",
                    (book,),
                )
            ]
        finally:
            conn.close()

        self._json(
            {
                "ok": True,
                "book_id": book,
                "world": world,
                "characters": characters,
                "curve": curve,
            }
        )

    def _config_post(self, body: dict) -> None:
        updates: dict[str, str] = {}
        base = str(body.get("base_url") or "").strip()
        model = str(body.get("model") or "").strip()
        if base:
            if not (base.startswith("http://") or base.startswith("https://")):
                self._fail("base_url 要以 http:// 或 https:// 开头")
                return
            updates["NOVEL_BASE_URL"] = base
        if model:
            if len(model) > 64:
                self._fail("model 名太长（>64 字符）")
                return
            updates["NOVEL_MODEL"] = model
        if not updates:
            self._fail("没有要改的项（base_url / model）")
            return
        backup = _write_env(updates)
        # 写文件不够：from_env() 读的是 os.environ，得同步
        for k, v in updates.items():
            os.environ[k] = v
        payload = self._config_payload()
        payload["backup"] = backup.name if backup else None
        self._json(payload)

    def log_message(self, fmt: str, *args) -> None:
        # 默认会把每个请求打成一长串，这里压掉；真出错在 except 里 print
        return


def main() -> None:
    db.migrate()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"实验台起来了： http://{HOST}:{PORT}")
    print("Ctrl+C 停。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停了。")
        srv.server_close()


if __name__ == "__main__":
    main()
