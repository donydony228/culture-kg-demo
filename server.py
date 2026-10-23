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
KB = json.loads((DOCS / "knowledge.json").read_text(encoding="utf-8"))  # 知識條目層：通用知識，連到個案/構件/事件
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
def short(s):  # 「清 仇英《桐陰畫靜圖》」→「桐陰畫靜圖」；「唐 象尊」→「象尊」
    m = re.search(r"《(.+?)》", s)
    return m.group(1) if m else s.split(" ")[-1]
aliases = lambda s: list(dict.fromkeys([s, short(s), s.split(" ")[-1], *re.findall(r"[《「](.+?)[》」]", s)]))
yr = lambda e: str(e["yearFrom"]) if e["yearFrom"] == e["yearTo"] else f'{e["yearFrom"]}–{e["yearTo"]}'

N, E = {}, {}
def node(id, kind, label, **kw): N.setdefault(id, {"id": id, "kind": kind, "label": label, **kw})
def link(a, b, label): E.setdefault(a + "~" + b, {"from": a, "to": b, "label": label})

def build():
    for h in D["heritage"]:
        node(h["id"], "文物", short(h["name"]), images=h.get("images"), names=aliases(h["name"]), attrs=[h["category"], h["era"], *h["materials"]],
             text=f'{h["name"]}（{h["category"]}，{h["era"] or "年代未載明"}，材質：{"、".join(h["materials"])}，館藏編號：{h["accNo"] or "未載明"}）：{h["summary"]}')
        for m in h["materials"]:
            node("mat-" + m, "材質", m, names=[m]); link(h["id"], "mat-" + m, "材質")
    for c in D["craftsmen"]:
        node(c["id"], "人物", c["name"], names=[c["name"]], attrs=[c["role"], c["school"]], text=f'{c["name"]}（{c["role"]}）：{c["summary"]}')
    for o in D["organizations"]:
        node(o["id"], "單位", o["name"], names=[o["name"]], attrs=[o["type"]])
    for e in D["events"]:
        if not ev_ok(e): continue
        about = [by[a].get("heritage", a) for a in e["about"]]
        node(e["id"], "事件", f'{yr(e)} {e["type"]}・{short(by[e["about"][0]]["name"])}', attrs=[e["type"]], year=(e["yearFrom"], e["yearTo"]))
        for a in dict.fromkeys(about): link(e["id"], a, "關於")
        for a in e["about"]:
            if "heritage" in by[a]:
                m = by[a]
                node(a, "部位", f'{short(by[m["heritage"]]["name"])}・{m["name"]}', names=[m["name"]], attrs=[m["type"]])
                link(a, m["heritage"], "屬於"); link(e["id"], a, "處置")
        for t in e.get("materials", []): node("mat-" + t, "材質", t, names=[t]); link(e["id"], "mat-" + t, "使用")
        for s in e["sources"]:
            if src_ok(s):
                d = doc_of(s["page"]); node(d["id"], "文件", d["title"], names=[d["title"]], url=d.get("url")); link(d["id"], e["id"], "記載")
        p = e.get("project") and by[e["project"]]
        if p and p.get("executor"): link(p["executor"], e["id"], "主辦")
    for p in D["participations"]:
        if part_ok(p): link(p["craftsman"], p["event"], p["role"])
    for d in [n for n in N.values() if n["kind"] == "文件"]:
        evs = [e for e in D["events"] if e["id"] in N and any(doc_of(s["page"])["id"] == d["id"] for s in e["sources"] if src_ok(s))]
        objs = {a for e in evs for a in e["about"] if a.startswith("r")} | {by[a]["heritage"] for e in evs for a in e["about"] if "heritage" in by[a]}
        orgs = {a for e in evs for a in e["about"] if a.startswith("o")}
        d["text"] = f'{d["label"]}（展覽網頁，{d.get("url") or ""}）：記載 {len(evs)} 筆事件，涉及 {len(objs)} 件文物、{len(orgs)} 個單位。'
        d["stats"] = [len(objs), len(evs), len(orgs)]
    # 知識條目 → 構件/個案/事件（事件被公開規則濾掉就不連）、條目之間互連
    for k in KB["entries"]:
        body = re.sub(r"\[s\d+\]", "", "".join(k["body"]))
        node(k["id"], "知識", k["title"], names=k["keys"], attrs=[k["type"]], summary=k["summary"], entry=True,
             text=f'[{k["id"]}] 知識條目《{k["title"]}》：{body} 知識出處：' + "；".join(f'{KB["sources"][s]["title"]}（{KB["sources"][s]["kind"]}）' for s in k["refs"]))
        for t in k.get("about", []) + k.get("cases", []): link(k["id"], t, "說明")
        for t in k.get("links", []): link(k["id"], t, "相關")
    # 只出現幾次的材質是雜訊（連不出關係），不入圖
    deg = {}
    for e in E.values():
        for x in (e["from"], e["to"]): deg[x] = deg.get(x, 0) + 1
    for k in [k for k, n in N.items() if n["kind"] == "材質" and deg.get(k, 0) < 4]: del N[k]
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
            if n["kind"] == "事件" and n["year"][0] <= y <= n["year"][1]: hits[n["id"]] = 2
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
    # 知識條目全文很長，證據裡只放前 450 字（摘要＋第一段）；完整內容在閱讀器，這裡省 token 就是省時間
    lines = [N[i]["text"][:450] + ("…" if len(N[i]["text"]) > 450 else "") if N[i]["kind"] == "知識" else N[i]["text"] for i in sorted(ids) if N[i].get("text")]
    for i in sorted(ids):
        n = N[i]
        if n["kind"] != "事件": continue
        e = by[i]
        about = "、".join(short(by[a]["name"]) for a in e["about"])
        line = f'[{i}] {yr(e)} 年{e["type"]}：{about}；原始記載「{e["original"]}」'
        if e.get("description"): line += f'；{e["description"]}'
        if e.get("methods"): line += f'；方法與技術 {"、".join(e["methods"])}'
        if e.get("materials"): line += f'；材料 {"、".join(e["materials"])}'
        who = [f'{by[p["craftsman"]]["name"]}（{p["role"]}）' for p in D["participations"] if p["event"] == i and part_ok(p)]
        if who: line += f'；相關人物 {"、".join(who)}'
        pr = e.get("project") and by[e["project"]]
        if pr: line += f'；計畫《{pr["name"]}》，主辦 {by[pr["executor"]]["name"]}'
        src = [f'《{doc_of(s["page"])["title"]}》第 {by[s["page"]]["page"]} 項：「{fix(by[s["page"]]["text"])}」' for s in e["sources"] if src_ok(s)]
        lines.append(line + "\n    出處：" + "\n    出處：".join(src))
    return "\n".join(lines)

PROMPT = ("你是文物保存修護知識庫的問答助理。只能根據下列「圖譜證據」回答，不可使用外部知識；"
          "證據沒有的就說「知識庫目前沒有相關紀錄」。若不同出處記載互相矛盾（如年代），要並陳並指出。"
          "回答用繁體中文純文字，不要使用 Markdown 符號（如 * 或 #）。每個依據事件或知識條目的陳述句末，必須用中括號標出編號，事件如 [e2]、知識條目如 [k-tile]（只能用證據裡出現的編號，可並列）。知識條目是通用的保存修護知識、不是某件文物的紀錄，引用時要分清楚。事件的原始記載為「處置年份展覽未載明」者，所列 2026 年是展覽展出年，不是處置發生的年份，不可當成修復年代。\n\n圖譜證據：\n{ctx}\n\n問題：{q}")

# Gemini 2.5 預設會「思考」，同一題 20 秒 → 關掉只要 3 秒；其他供應商不吃這個參數，用 LLM_REASONING 自行指定（空字串＝不送）
THINK = {"reasoning_effort": r} if (r := os.environ.get("LLM_REASONING", "none" if "generativelanguage" in os.environ.get("LLM_BASE_URL", "generativelanguage") else "")) else {}
CACHE = {}  # 問題 → 結果；證據與答案只取決於問題與靜態資料，同題直接回（成功的答案才存）

def llm(prompt):
    key = os.environ.get("LLM_API_KEY")
    if not key: return None
    base = os.environ.get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
    req = urllib.request.Request(base + "/chat/completions", method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        data=json.dumps({"model": os.environ.get("LLM_MODEL", "gemini-2.5-flash"), "temperature": 0,
                         "messages": [{"role": "user", "content": prompt}], **THINK}).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        txt = json.load(r)["choices"][0]["message"]["content"]
    # 模型偶爾仍會吐 Markdown：粗體符號拿掉、項目符號換成「・」
    return re.sub(r"^\s*[*-]\s+", "・", txt.replace("**", ""), flags=re.M)

def query(q):
    q = q.strip()
    if q in CACHE: return CACHE[q]
    r = _query(q)
    if r.get("answer") and not r["answer"].startswith("（"): CACHE[q] = r
    return r

def _query(q):
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
            return self.send(json.dumps({"nodes": [{**n, "detail": facts({n["id"]}) or n.get("text") or n["label"], **({"sources": sources_of(n["id"])} if n["kind"] == "事件" else {})} for n in N.values()], "edges": list(E.values())}, ensure_ascii=False).encode(), "application/json")
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
