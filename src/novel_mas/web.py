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
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .db import base as db
from .llm import FakeProvider, OpenAIProvider
from .orchestrator import Orchestrator

PORT = 8787
HOST = "127.0.0.1"


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
      <button onclick="act('curve', {prompt: val('curvePrompt')})">生成曲线（20 章）</button>
    </section>

    <section>
      <h2>④ 写这一章</h2>
      <div class="row">
        <div><label>第几章</label><input id="chapter" value="1"></div>
        <div style="flex:2"><label>大纲</label><input id="brief" value="李三在巷口捡到一枚铜钱，抬头看见钟楼上有个人影。"></div>
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
    if (what === 'curve') toast('生成了 ' + r.points + ' 个点');
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
       + '<span>落盘 <b>' + (r.out_path ? '是' : '没写（被拦）') + '</b></span>'
       + '</div>';
  if (r.evidence && r.evidence.length) {
    html += '<ul class="ev">' + r.evidence.map(e => '<li>' + esc(e) + '</li>').join('') + '</ul>';
  }
  html += '<label style="margin-top:14px">正文</label>';
  html += '<pre class="text">' + esc(r.text || '(空)') + '</pre>';
  $('result').innerHTML = html;
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
</script>
</body>
</html>
"""


def _make_orc(book_id: str, real_model: bool) -> Orchestrator:
    llm = OpenAIProvider.from_env() if real_model else FakeProvider(style="日常")
    return Orchestrator(book_id=book_id, llm=llm)


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
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            self._fail("先写一句走向描述")
            return
        reg = _make_orc(book, real_model=True).registry
        result = reg.call("trend", "set_curve", book_id=book, prompt=prompt)
        pts = result["curve"]
        warnings = result["warnings"]
        self._json({
            "ok": True,
            "points": len(pts),
            "warnings": warnings,
            "message": f"生成 {len(pts)} 个点" + (f"，{len(warnings)} 条坡度警告" if warnings else ""),
        })

    def _write(self, book: str, body: dict) -> None:
        chapter = int(body.get("chapter") or 1)
        brief = str(body.get("brief") or "").strip()
        real = bool(body.get("real_model"))
        if not brief:
            self._fail("大纲不能空")
            return
        r = _make_orc(book, real).write_chapter(chapter, brief)
        self._json({
            "ok": True,
            "chapter": r.chapter,
            "verdict": r.verdict,
            "trend_verdict": r.trend_verdict,
            "score": r.score,
            "target": r.target,
            "deviation": r.deviation,
            "tokens": r.tokens,
            "evidence": r.evidence,
            "out_path": r.out_path,
            "text": r.text,
        })

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
