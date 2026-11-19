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

HOST = "127.0.0.1"
PORT = int(os.environ.get("NOVEL_PORT") or 8787)
PORT_SCAN = 10          # 8787 被占就往后找 10 个，别直接起不来
_REPO = Path(__file__).resolve().parents[2]


def _can_write(dir_path: Path) -> bool:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        probe = dir_path / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _env_path() -> Path:
    """配置放哪：仓库根（开发）→ 用户目录（安装版）→ 哪个能写用哪个。

    以前的兜底是「都没有就回仓库根」，装到 Program Files 时会 500
    （程序目录只读）。现在先探一下能不能写，写不了就落 %APPDATA%。
    """
    override = os.environ.get("NOVEL_ENV_FILE")
    if override:
        return Path(override)
    in_repo = _REPO / ".env"
    user = Path(os.environ.get("APPDATA") or Path.home()) / "novel-mas" / ".env"
    if in_repo.exists():
        return in_repo
    if user.exists():
        return user
    if _can_write(_REPO):
        return in_repo
    return user


ENV_PATH = _env_path()
TRASH_DIR = db.DATA_DIR / "trash"


def _book_tables(conn) -> list[str]:
    """这个库里带 book_id 列的表。没这一列的表不参与开关书。"""
    out: list[str] = []
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        t = r["name"]
        cols = {c["name"] for c in conn.execute(f"PRAGMA table_info({t})")}
        if "book_id" in cols:
            out.append(t)
    return out


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


def render_env(existing: list[str], updates: dict[str, str], header: str) -> str:
    """把「现有行 + 要改的键」渲染成 .env 全文（纯函数，方便测）。

    规则：
    - 同名的键就地替换
    - **不认识的键原样保留**（用户自己加的，比如代理地址）
    - 缺的键补在末尾
    """
    seen: set[str] = set()
    out: list[str] = []
    for line in existing:
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.strip().startswith("#") and key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    text = "\n".join(out).strip("\n")
    if header:
        text = f"{header}\n{text}" if text else header
    return text + "\n"


def _write_env(updates: dict[str, str]) -> tuple[Path, bool]:
    """把配置写进 .env。返回（路径, 是否新建）。

    **不备份**（2026-11-19 改）：以前每次保存都复制一份 .env.bak，
    每份都带明文 key —— 点十次就十把 key 躺在目录里。
    """
    created = not ENV_PATH.exists()
    existing: list[str] = []
    if not created:
        existing = ENV_PATH.read_text(encoding="utf-8").splitlines()
    header = (
        f"# 由 GUI 生成于 {datetime.now():%Y-%m-%d %H:%M}；手改会被覆盖，"
        "key 只在这个文件里，别提交到 git。"
        if created
        else ""
    )
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text(render_env(existing, updates, header), encoding="utf-8")
    return ENV_PATH, created


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
    <select id="bookSel" onchange="pickBook()" style="width:210px"><option value="">— 已有的书 —</option></select>
    <button class="ghost" onclick="refreshAll()">刷新</button>
    <button class="ghost" onclick="renameBook()">改名</button>
    <label style="margin:0;font-size:12px;display:flex;align-items:center;gap:4px">
      <input type="checkbox" id="delBackup" checked>删前备份
    </label>
    <button class="ghost" onclick="deleteBook()">删书</button>
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
      <h2 style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span>模型配置</span>
        <span id="cfgSummary" class="muted" style="font-size:12px;font-weight:400">读着…</span>
        <button class="ghost" id="cfgToggle" style="margin-left:auto;padding:2px 8px;font-size:12px"
                onclick="toggleConfig()">展开</button>
      </h2>
      <div id="cfgBody" style="display:none">
        <div id="cfg" class="muted"></div>
        <label style="margin-top:10px">base_url</label>
        <input id="cfgBase" placeholder="https://api.deepseek.com/v1">
        <label>model</label>
        <input id="cfgModel" placeholder="deepseek-chat">
        <label>api_key（留空 = 不动现有的）</label>
        <input id="cfgKey" type="password" placeholder="sk-..." autocomplete="off">
        <label>写手温度（0–1.5，留空 = 跟随默认 0.8）</label>
        <input id="cfgWriterTemp" type="number" step="0.1" min="0" max="1.5" placeholder="0.8">
        <label>写手 top_p（0.1–1.0，留空 = 不发这个字段）</label>
        <input id="cfgWriterTopP" type="number" step="0.05" min="0.1" max="1" placeholder="留空">
        <div class="muted" style="margin-top:6px">这两项只作用在<b>写正文</b>那一次调用上；世界 / 走向 / 人物三个判官和仲裁一律 0.1，不受影响。</div>
        <div class="row" style="margin-top:10px">
          <button onclick="saveConfig()">保存</button>
          <button class="ghost" onclick="loadConfig()">重读</button>
        </div>
        <div class="muted" style="margin-top:8px">key 只从 .env 读，页面不保存也不回显；改完写回 .env（不额外留副本）。</div>
      </div>
    </section>

    <section>
      <h2>本书概况 <button class="ghost" style="float:right" onclick="loadBook()">刷新</button></h2>
      <div class="row" style="margin-bottom:10px">
        <input id="wvTitle" placeholder="世界观标题（同名=覆盖）">
        <div style="flex:2"><input id="wvBody" placeholder="正文一句话"></div>
        <button class="ghost" onclick="saveWorldviewItem()">写</button>
      </div>
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
  const sum = $('cfgSummary');
  try {
    const r = await get('/api/config');
    $('cfgBase').value = r.base_url;
    $('cfgModel').value = r.model;
    $('cfgWriterTemp').value = (r.writer_temperature === null) ? '' : r.writer_temperature;
    $('cfgWriterTopP').value = (r.writer_top_p === null) ? '' : r.writer_top_p;
    // 收起时只看这一行：用的哪个模型、连的哪个端点、key 有没有
    sum.innerHTML = '<b>' + esc(r.model) + '</b> @ ' + esc(r.base_url)
      + (r.has_key ? ' · key ' + esc(r.key_tail) : ' · <b class="bad">key 没配</b>');
    box.innerHTML = '当前：<b>' + esc(r.model) + '</b> @ ' + esc(r.base_url)
      + '<br>key：' + (r.has_key ? '已配置（' + esc(r.key_tail) + '）' : '<b class="bad">没有</b>')
      + '<br>写回的文件：<code>' + esc(r.env_file) + '</code>';
    // 没 key 就别让面板缩着——那是新用户第一件要干的事
    if (!r.has_key) toggleConfig(true);
  } catch (e) {
    sum.innerHTML = '<span class="bad">读不到配置</span>';
    box.innerHTML = '<span class="bad">读不到配置：' + esc(e.message) + '</span>';
  }
}

function toggleConfig(force) {
  const body = $('cfgBody');
  const open = (force === undefined) ? (body.style.display === 'none') : !!force;
  body.style.display = open ? 'block' : 'none';
  $('cfgToggle').textContent = open ? '收起' : '展开';
}

async function saveConfig() {
  const btn = event.currentTarget;
  busy(btn, true, '保存中…');
  const typedKey = val('cfgKey');
  try {
    const r = await post('/api/config', {
      base_url: val('cfgBase'),
      model: val('cfgModel'),
      api_key: typedKey,
      writer_temperature: val('cfgWriterTemp'),
      writer_top_p: val('cfgWriterTopP'),
    });
    if (typedKey) $('cfgKey').value = '';   // 别在输入框里留着明文
    toast(r.message || '已保存', 7000);
    loadConfig();
  } catch (e) {
    toast('失败：' + e.message, 7000);
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
      ? r.world.map(w =>
          esc(w.title + '：' + w.body)
          + ' <button class="ghost" style="padding:0 6px;font-size:11px"'
          + ' data-title="' + esc(w.title) + '" onclick="removeWorldview(this.dataset.title)">删</button>'
        ).join('<br>') : dash);
    html += '<div style="margin-top:8px"><b>人物</b><br>' + (r.characters.length
      ? r.characters.map(c =>
          esc(c.name + '（' + c.importance + '｜' + (c.state || '未记') + '）')
          + ' <button class="ghost" style="padding:0 6px;font-size:11px"'
          + ' data-name="' + esc(c.name) + '" onclick="removeCharacter(this.dataset.name)">删</button>'
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

async function removeWorldview(title) {
  if (!confirm('删掉世界观「' + title + '」？撤不回来。')) return;
  try {
    const r = await post('/api/worldview_remove', {book_id: val('bookId') || 'demo', title: title});
    toast(r.message || '删了', 5000);
    loadBook();
  } catch (e) {
    toast('失败：' + e.message, 6000);
  }
}

async function saveWorldviewItem() {
  const title = val('wvTitle');
  const body = val('wvBody');
  if (!title || !body) { toast('标题和正文都要填', 4000); return; }
  try {
    const r = await post('/api/worldview_item', {book_id: val('bookId') || 'demo', title: title, body: body});
    toast(r.message || '写入了', 5000);
    $('wvTitle').value = '';
    $('wvBody').value = '';
    loadBook();
  } catch (e) {
    toast('失败：' + e.message, 6000);
  }
}

async function removeCharacter(name) {
  if (!confirm('删掉「' + name + '」？档案和逐章状态都会删，撤不回来。')) return;
  try {
    const r = await post('/api/character_remove', {book_id: val('bookId') || 'demo', name: name});
    toast(r.message || '删了', 5000);
    loadBook();
  } catch (e) {
    toast('失败：' + e.message, 6000);
  }
}

async function loadBooks() {
  try {
    const r = await get('/api/books');
    const cur = val('bookId') || 'demo';
    const sel = $('bookSel');
    sel.innerHTML = '<option value="">— 已有的书 —</option>' + r.books.map(b =>
      '<option value="' + esc(b.book_id) + '"' + (b.book_id === cur ? ' selected' : '') + '>'
      + esc(b.book_id) + '（世界观' + b.world + '·人物' + b.characters
      + '·曲线' + b.curve + '·章' + b.chapters + '）</option>').join('');
  } catch (e) {
    // 列不出来不算致命，别打断页面
  }
}

function pickBook() {
  const v = $('bookSel').value;
  if (!v) return;
  $('bookId').value = v;
  refreshAll();
}

async function refreshAll() {
  await Promise.all([loadBook(), loadHistory(), loadBooks()]);
}

async function renameBook() {
  const old = val('bookId');
  if (!old) { toast('先填书号', 3000); return; }
  const name = prompt('把「' + old + '」改成什么？（数据会整体搬过去）', old);
  if (!name || name === old) return;
  try {
    const r = await post('/api/book_rename', {book_id: old, new_id: name});
    $('bookId').value = name;
    toast(r.message, 6000);
    refreshAll();
  } catch (e) {
    toast('失败：' + e.message, 7000);
  }
}

async function deleteBook() {
  const b = val('bookId');
  if (!b) { toast('先填书号', 3000); return; }
  const backup = $('delBackup').checked;
  const tip = backup
    ? '会先把这本书导成 JSON 放到 data/trash/。'
    : '**不备份**，删掉就真没了。';
  if (!confirm('删掉「' + b + '」整本书？\n' + tip)) return;
  try {
    const r = await post('/api/book_delete', {book_id: b, backup: backup});
    toast(r.message, 9000);
    refreshAll();
  } catch (e) {
    toast('失败：' + e.message, 7000);
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
loadBooks();
</script>
</body>
</html>
"""


def _count(db_name: str, table: str, book: str) -> int:
    conn = db.connect(db_name)
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE book_id = ?", (book,)
        ).fetchone()
        return int(row["c"])
    finally:
        conn.close()


def _book_stats(book: str) -> dict:
    return {
        "world": _count("world.db", "world_bible", book),
        "characters": _count("char.db", "characters", book),
        "curve": _count("trend.db", "curve", book),
        "chapters": _count("trend.db", "chapter_scores", book),
    }


def books_overview() -> list[dict]:
    """库里有哪些书，各有多少东西。

    书不是独立的库文件，是每张表的 book_id 列，所以要扫全表才看得见。
    """
    found: set[str] = set()
    for name in db.DB_NAMES:
        conn = db.connect(name)
        try:
            for t in _book_tables(conn):
                for r in conn.execute(f"SELECT DISTINCT book_id FROM {t}"):
                    found.add(str(r["book_id"]))
        finally:
            conn.close()
    return [{"book_id": b, **_book_stats(b)} for b in sorted(found)]


def rename_book(old: str, new: str) -> int:
    """把一本书的 book_id 整体改掉，返回动了多少行。"""
    moved = 0
    for name in db.DB_NAMES:
        conn = db.connect(name)
        try:
            for t in _book_tables(conn):
                moved += conn.execute(
                    f"UPDATE {t} SET book_id = ? WHERE book_id = ?", (new, old)
                ).rowcount
            conn.commit()
        finally:
            conn.close()
    return moved


def delete_book(book: str, backup: bool = True) -> tuple[int, Path | None]:
    """删整本书：默认先导 JSON 备份，再逐表删。返回（删了多少行, 备份路径或 None）。

    备份可以关掉（`backup=False`）：有人就是不要留垃圾文件。
    但如果关了，删掉的东西就真没有了 —— 界面上必须说清楚。
    """
    backup_path: Path | None = None
    if backup:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = TRASH_DIR / f"{book}-{stamp}.json"
        dump: dict[str, list[dict]] = {}
        for name in db.DB_NAMES:
            conn = db.connect(name)
            try:
                for t in _book_tables(conn):
                    rows = conn.execute(
                        f"SELECT * FROM {t} WHERE book_id = ?", (book,)
                    ).fetchall()
                    if rows:
                        dump[f"{name}:{t}"] = [dict(r) for r in rows]
            finally:
                conn.close()
        backup_path.write_text(
            json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    removed = 0
    for name in db.DB_NAMES:
        conn = db.connect(name)
        try:
            for t in _book_tables(conn):
                removed += conn.execute(
                    f"DELETE FROM {t} WHERE book_id = ?", (book,)
                ).rowcount
            conn.commit()
        finally:
            conn.close()
    return removed, backup_path


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
        if parsed.path == "/api/books":
            self._books()
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
            elif parsed.path == "/api/worldview_item":
                self._worldview_item(book, body)
            elif parsed.path == "/api/worldview_remove":
                self._worldview_remove(book, body)
            elif parsed.path == "/api/character":
                self._character(book, body)
            elif parsed.path == "/api/character_remove":
                self._character_remove(book, body)
            elif parsed.path == "/api/curve":
                self._curve(book, body)
            elif parsed.path == "/api/write":
                self._write(book, body)
            elif parsed.path == "/api/config":
                self._config_post(body)
            elif parsed.path == "/api/book_rename":
                self._book_rename(book, body)
            elif parsed.path == "/api/book_delete":
                self._book_delete(book, body)
            else:
                self._fail("没有这个路径", 404)
        except (LookupError, ValueError) as exc:
            # 用户能自己修的错（找不到 / 填错了）：400，说人话，别带类名前缀
            self._fail(str(exc), 400)
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

    def _worldview_item(self, book: str, body: dict) -> None:
        title = str(body.get("title") or "").strip()
        text = str(body.get("body") or "").strip()
        category = str(body.get("category") or "规则").strip()
        if not title or not text:
            self._fail("标题和正文都要填")
            return
        reg = Orchestrator(book_id=book, llm=FakeProvider()).registry
        r = reg.call(
            "world_will", "set_worldview",
            book_id=book, title=title, body=text, category=category,
        )
        self._json({"ok": True, "message": f"写入了「{r['title']}」（同名是覆盖）"})

    def _worldview_remove(self, book: str, body: dict) -> None:
        title = str(body.get("title") or "").strip()
        if not title:
            self._fail("要删哪一条？标题不能空")
            return
        reg = Orchestrator(book_id=book, llm=FakeProvider()).registry
        reg.call("world_will", "remove_worldview", book_id=book, title=title)
        self._json({"ok": True, "message": f"删掉了「{title}」"})

    def _books(self) -> None:
        self._json({"ok": True, "books": books_overview()})

    def _book_rename(self, old: str, body: dict) -> None:
        new = str(body.get("new_id") or "").strip()
        if not new:
            self._fail("新书号不能空")
            return
        if new == old:
            self._fail("新旧一样，没改")
            return
        # 改到已有的书 = 两本书合并，几乎一定不是用户想要的
        if any(b["book_id"] == new for b in books_overview()):
            self._fail(f"已经有「{new}」这本书了；改名会合并数据，换个名字或先删掉它", 400)
            return
        moved = rename_book(old, new)
        if moved == 0:
            self._fail(f"库里没有「{old}」这本书", 400)
            return
        self._json({"ok": True, "message": f"「{old}」改名为「{new}」，动了 {moved} 行"})

    def _book_delete(self, book: str, body: dict) -> None:
        if not any(b["book_id"] == book for b in books_overview()):
            self._fail(f"库里没有「{book}」这本书", 400)
            return
        want_backup = bool(body.get("backup", True))
        removed, path = delete_book(book, backup=want_backup)
        tail = f"备份在 data/trash/{path.name}" if path else "没备份（你要求的）"
        self._json(
            {"ok": True, "message": f"删掉了「{book}」，共 {removed} 行；{tail}"}
        )

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
        self._json(
            {
                "ok": True,
                "message": f"导入了 {snap.name}（{snap.importance}）——同名会覆盖档案，状态的进度保留",
            }
        )

    def _character_remove(self, book: str, body: dict) -> None:
        name = str(body.get("name") or "").strip()
        if not name:
            self._fail("要删谁？名字不能空")
            return
        reg = Orchestrator(book_id=book, llm=FakeProvider()).registry
        r = reg.call("character", "remove_character", book_id=book, name=name)
        self._json(
            {
                "ok": True,
                "message": (
                    f"删掉了 {r['name']}（连带 {r['states_removed']} 条状态、"
                    f"{r['relationships_removed']} 条关系）"
                ),
            }
        )

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

        def _num(name: str) -> float | None:
            """读一个数字配置；没配就返回 None（0 是合法值，不能拿 0 当「没设置」）。"""
            raw = env_file.get(name) or os.environ.get(name)
            if not raw:
                return None
            try:
                return float(str(raw).strip())
            except ValueError:
                return None

        return {
            "ok": True,
            "base_url": base_url,
            "model": model,
            "has_key": bool(key),
            "key_tail": ("…" + key[-4:]) if len(key) >= 8 else "",
            "env_file": str(ENV_PATH),
            "writable": True,
            "writer_temperature": _num("NOVEL_WRITER_TEMPERATURE"),
            "writer_top_p": _num("NOVEL_WRITER_TOP_P"),
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
        api_key = str(body.get("api_key") or "").strip()
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
        if api_key:
            if not 8 <= len(api_key) <= 200:
                self._fail("key 看着不对（长度应在 8–200 之间）")
                return
            # 沿用文件里已有的键名，别凭空多出一行同义键
            env_file = _read_env()
            key_name = (
                "OPENAI_API_KEY"
                if "OPENAI_API_KEY" in env_file and "DEEPSEEK_API_KEY" not in env_file
                else "DEEPSEEK_API_KEY"
            )
            updates[key_name] = api_key
        # 写手旋钮：只存不验范围是不够的 —— 温度 5.0 也能存进去，然后每章都炸。
        for field, env_name, lo, hi, label in (
            ("writer_temperature", "NOVEL_WRITER_TEMPERATURE", 0.0, 1.5, "写手温度"),
            ("writer_top_p", "NOVEL_WRITER_TOP_P", 0.1, 1.0, "写手 top_p"),
        ):
            raw = str(body.get(field) or "").strip()
            if not raw:
                continue            # 留空 = 不动这一项
            try:
                num = float(raw)
            except ValueError:
                self._fail(f"{label} 要填数字（现在填的是「{raw}」）")
                return
            if not lo <= num <= hi:
                self._fail(f"{label} 要在 {lo}–{hi} 之间（现在是 {num}）")
                return
            updates[env_name] = f"{num:g}"
        if not updates:
            self._fail("没有要改的项（base_url / model / api_key / 写手温度 / top_p）")
            return
        path, created = _write_env(updates)
        # 写文件不够：from_env() 读的是 os.environ，得同步
        for k, v in updates.items():
            os.environ[k] = v
        payload = self._config_payload()
        payload.pop("writable", None)
        payload["created"] = created
        payload["message"] = ("生成了新的 .env：" if created else "更新了 .env：") + str(path)
        self._json(payload)

    def log_message(self, fmt: str, *args) -> None:
        # 默认会把每个请求打成一长串，这里压掉；真出错在 except 里 print
        return


def _port_taken(port: int) -> bool:
    """端口上有没有人在听。

    为什么不用「bind 失败」来判断：Windows 下 http.server 默认开
    SO_REUSEADDR，两个进程可以同时绑同一个端口 —— bind 会成功，
    请求是谁接到只看运气。所以得主动连一下。（2026-11-19 踩过）
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((HOST, port)) == 0


def main() -> None:
    db.migrate()
    srv = None
    port = PORT
    for port in range(PORT, PORT + PORT_SCAN):
        if _port_taken(port):
            continue
        try:
            srv = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            srv = None
            continue
    if srv is None:
        raise SystemExit(
            f"{PORT}–{PORT + PORT_SCAN - 1} 全被占了，起不来。"
            "关掉占用的程序，或用 NOVEL_PORT 指定端口。"
        )
    if port != PORT:
        print(f"注意：{PORT} 被占用，改用 {port}")
    # 把实际端口写下来：启动器要按它开窗口，别硬编码 8787
    try:
        (db.DATA_DIR / "port.txt").write_text(str(port), encoding="utf-8")
    except OSError:
        pass
    print(f"实验台起来了： http://{HOST}:{port}")
    print(f"数据目录：{db.DATA_DIR}")
    print("Ctrl+C 停。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停了。")
        srv.server_close()


if __name__ == "__main__":
    main()
