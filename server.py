#!/usr/bin/env python3
"""GraphRAG demo 後端：嚴格綱要的知識圖譜（docs/data.json）→ 子圖檢索 → LLM 生成帶出處的答案。

python3 server.py            # http://localhost:3100
LLM 走 OpenAI 相容端點（Gemini / OpenAI / Ollama 都行）：
  LLM_API_KEY  LLM_BASE_URL（預設 Gemini）  LLM_MODEL（預設 gemini-2.5-flash）
沒設 key 時只回檢索到的子圖與證據，不生成答案。
"""
import json, os, re, sys, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DOCS = Path(__file__).parent / "docs"
for l in (DOCS.parent / ".env").read_text().splitlines() if (DOCS.parent / ".env").is_file() else []:
    k, _, v = l.partition("=")
    if k.strip() and not k.startswith("#"): os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
D = json.loads((DOCS / "data.json").read_text(encoding="utf-8"))
by = {x["id"]: x for k, v in D.items() if isinstance(v, list) for x in v if "id" in x}
OK = {"已校", "已修正"}

# ---- 公開規則（同前台）：出處已校對、來源文件可公開才進圖 ----
doc_of = lambda pid: by[by[pid]["doc"]]
src_ok = lambda s: s["status"] in OK and doc_of(s["page"])["public"]
ev_ok = lambda e: any(src_ok(s) for s in e["sources"])
part_ok = lambda p: src_ok(p["source"]) and ev_ok(by[p["event"]])
fixes = [p["correction"] for p in D["participations"] if "correction" in p]
def fix(t):
    for c in fixes:
        t = t.replace(c["originalValue"], c["correctedValue"])
    return t
short = lambda s: s.replace("虛構・", "")
yr = lambda e: str(e["yearFrom"]) if e["yearFrom"] == e["yearTo"] else f'{e["yearFrom"]}–{e["yearTo"]}'

N, E = {}, {}
def node(id, kind, label, **kw): N.setdefault(id, {"id": id, "kind": kind, "label": label, **kw})
def link(a, b, label): E.setdefault(a + "~" + b, {"from": a, "to": b, "label": label})

def build():
    for h in D["heritage"]:
        node(h["id"], "個案", short(h["name"]), names=[h["name"], short(h["name"])],
             attrs=[h["city"], h["district"], h["address"], h["category"], h["grade"]], text=f'{h["name"]}（{h["category"]} {h["grade"]}，{h["address"]}）：{h["summary"]}')
    for c in D["craftsmen"]:
        node(c["id"], "匠師", c["name"], names=[c["name"]], attrs=[c["role"], c["school"]],
             text=f'{c["name"]}（{c["role"]}，{c["school"]}，活躍於 {c["active"]}）：{c["summary"]}')
    for o in D["organizations"]:
        node(o["id"], "單位", o["name"], names=[o["name"]], attrs=[o["type"]])
    for e in D["events"]:
        if not ev_ok(e): continue
        about = [by[a].get("heritage", a) for a in e["about"]]
        node(e["id"], "修繕事件", f'{yr(e)} {e["type"]}', attrs=[e["type"]], year=(e["yearFrom"], e["yearTo"]))
        for a in dict.fromkeys(about): link(e["id"], a, "關於")
        for a in e["about"]:
            if "heritage" in by[a]:
                m = by[a]
                node(a, "構件", f'{short(by[m["heritage"]]["name"])}・{m["name"]}', names=[m["name"]], attrs=[m["type"]])
                link(a, m["heritage"], "屬於"); link(e["id"], a, "修繕")
        dec = e["yearFrom"] // 10 * 10
        node(f"y{dec}", "年代", f"{dec} 年代"); link(e["id"], f"y{dec}", "年代")
        for s in e["sources"]:
            if src_ok(s):
                d = doc_of(s["page"]); node(d["id"], "文件", d["title"], names=[d["title"]]); link(d["id"], e["id"], "記載")
        p = e.get("project") and by[e["project"]]
        if p and p.get("executor"): link(p["executor"], e["id"], "承攬")
    for p in D["participations"]:
        if part_ok(p): link(p["craftsman"], p["event"], p["role"])
    # 只留有端點的邊與有連線的單位
    for k in [k for k, e in E.items() if e["from"] not in N or e["to"] not in N]: del E[k]
    used = {x for e in E.values() for x in (e["from"], e["to"])}
    for k in [k for k in N if k not in used]: del N[k]
build()
adj = {k: set() for k in N}
for e in E.values(): adj[e["from"]].add(e["to"]); adj[e["to"]].add(e["from"])

# ---- 檢索：問題 → 種子節點 → 子圖 ----
bigrams = lambda s: {s[i:i + 2] for i in range(len(s) - 1)}
def seeds(q):
    hits = {}
    for n in N.values():
        if any(x and x in q for x in n.get("names", [])): hits[n["id"]] = 3
        elif any(x and x in q for x in n.get("attrs", [])): hits[n["id"]] = 1
    for m in re.findall(r"\d{3,4}", q):
        y = int(m)
        for n in N.values():
            if n["kind"] == "修繕事件" and n["year"][0] <= y <= n["year"][1]: hits[n["id"]] = 2
    if hits:
        top = sorted(hits, key=lambda k: -hits[k])
        return top[:8]
    # ponytail: 無精確命中時用字元二元組重疊當全文檢索；要精準再換 BM25/向量
    qb = bigrams(q)
    score = {k: len(qb & bigrams(n["label"] + (n.get("text") or ""))) for k, n in N.items()}
    return [k for k, v in sorted(score.items(), key=lambda x: -x[1])[:3] if v]

def subgraph(seed):
    got, frontier = set(seed), set(seed)
    for _ in range(2 if len(seed) <= 2 else 1):
        frontier = {n for f in frontier for n in adj[f]} - got
        got |= frontier
    return got

def sources_of(i):
    """事件的結構化出處，給前台顯示信任資訊（文件、頁碼、原文、校對狀態、信心）。"""
    return [{"doc": doc_of(s["page"])["title"], "page": by[s["page"]]["page"], "quote": fix(by[s["page"]]["text"]), "claim": s.get("claim"),
             "method": s["method"], "confidence": s.get("confidence"), "status": s["status"]} for s in by[i]["sources"] if src_ok(s)]

def facts(ids):
    lines = [N[i]["text"] for i in sorted(ids) if N[i].get("text")]
    for i in sorted(ids):
        n = N[i]
        if n["kind"] != "修繕事件": continue
        e = by[i]
        about = "、".join(short(by[a]["name"]) for a in e["about"])
        line = f'[{i}] {yr(e)} 年{e["type"]}：{about}；原始記載「{e["original"]}」'
        if e.get("description"): line += f'；{e["description"]}'
        if e.get("methods"): line += f'；工法 {"、".join(e["methods"])}'
        if e.get("materials"): line += f'；材料 {"、".join(e["materials"])}'
        who = [f'{by[p["craftsman"]]["name"]}（{p["role"]}）' for p in D["participations"] if p["event"] == i and part_ok(p)]
        if who: line += f'；匠師 {"、".join(who)}'
        pr = e.get("project") and by[e["project"]]
        if pr: line += f'；工程《{pr["name"]}》{pr["year"]} 年，執行 {by[pr["executor"]]["name"]}'
        src = [f'《{doc_of(s["page"])["title"]}》第 {by[s["page"]]["page"]} 頁：「{fix(by[s["page"]]["text"])}」' for s in e["sources"] if src_ok(s)]
        lines.append(line + "\n    出處：" + "\n    出處：".join(src))
    return "\n".join(lines)

PROMPT = ("你是文化資產知識庫的問答助理。只能根據下列「圖譜證據」回答，不可使用外部知識；"
          "證據沒有的就說「知識庫目前沒有相關紀錄」。若不同出處記載互相矛盾（如年代），要並陳並指出。"
          "回答用繁體中文純文字，不要使用 Markdown 符號（如 * 或 #）。每個依據事件的陳述句末，必須用中括號標出所依據的事件編號，如 [e2]（只能用證據裡出現的編號，可並列 [e2][e3]）。\n\n圖譜證據：\n{ctx}\n\n問題：{q}")

def llm(prompt):
    key = os.environ.get("LLM_API_KEY")
    if not key: return None
    base = os.environ.get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
    req = urllib.request.Request(base + "/chat/completions", method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        data=json.dumps({"model": os.environ.get("LLM_MODEL", "gemini-2.5-flash"), "temperature": 0,
                         "messages": [{"role": "user", "content": prompt}]}).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        txt = json.load(r)["choices"][0]["message"]["content"]
    # 模型偶爾仍會吐 Markdown：粗體符號拿掉、項目符號換成「・」
    return re.sub(r"^\s*[*-]\s+", "・", txt.replace("**", ""), flags=re.M)

def query(q):
    s = seeds(q)
    if not s: return {"answer": "知識庫目前沒有相關紀錄。", "context": "", "entities": []}
    ids = subgraph(s)
    ctx = facts(ids)
    try: ans = llm(PROMPT.format(ctx=ctx, q=q))
    except Exception as ex: ans = f"（LLM 呼叫失敗：{ex}；以下為檢索到的證據）"
    return {"answer": ans or "（未設定 LLM_API_KEY，僅顯示檢索到的子圖與證據）", "context": ctx, "entities": sorted(ids)}

class H(BaseHTTPRequestHandler):
    def send(self, body, ctype, code=200):
        self.send_response(code); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/api/graph":
            return self.send(json.dumps({"nodes": [{**n, "detail": facts({n["id"]}) or n.get("text") or n["label"], **({"sources": sources_of(n["id"])} if n["kind"] == "修繕事件" else {})} for n in N.values()], "edges": list(E.values())}, ensure_ascii=False).encode(), "application/json")
        f = DOCS / ("index.html" if self.path in ("/", "") else self.path.lstrip("/"))
        if f.is_file() and DOCS in f.resolve().parents:
            return self.send(f.read_bytes(), {"html": "text/html", "json": "application/json"}.get(f.suffix[1:], "text/plain") + "; charset=utf-8")
        self.send(b"not found", "text/plain", 404)
    def do_POST(self):
        if self.path != "/api/query": return self.send(b"not found", "text/plain", 404)
        q = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}").get("query", "").strip()
        self.send(json.dumps(query(q) if q else {"error": "empty query"}, ensure_ascii=False).encode(), "application/json")

if __name__ == "__main__":
    if len(sys.argv) > 1:  # 自檢：python3 server.py "問題" 只印檢索結果
        r = query(" ".join(sys.argv[1:])); print(r["answer"], "\n--", r["entities"], "\n", r["context"])
    else:
        print("http://localhost:3100"); ThreadingHTTPServer(("", int(os.environ.get("PORT", 3100))), H).serve_forever()
