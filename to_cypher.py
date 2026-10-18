"""docs/data.json → seed.cypher（Neo4j 匯入檔）。執行：python3 to_cypher.py"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
D = json.loads((ROOT / "docs/data.json").read_text(encoding="utf-8"))


def lit(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(lit(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)  # Cypher 接受 JSON 風格的雙引號字串


def props(d, skip=()):
    return "{" + ", ".join(f"{k}: {lit(v)}" for k, v in d.items() if k not in skip and v not in (None, "")) + "}"


def check():
    ids = {x["id"] for k in ("heritage", "components", "craftsmen", "organizations", "projects", "documents", "pages", "events") for x in D[k]}
    page_ids = {p["id"] for p in D["pages"]}
    refs = [c["heritage"] for c in D["components"]]
    refs += [p[k] for p in D["projects"] for k in ("executor", "commissioner") if k in p]
    refs += [d["project"] for d in D["documents"] if "project" in d] + [h for d in D["documents"] for h in d["heritage"]]
    refs += [p["doc"] for p in D["pages"]]
    refs += [a for e in D["events"] for a in e["about"]] + [e["project"] for e in D["events"] if "project" in e]
    refs += [x for p in D["participations"] for x in (p["craftsman"], p["event"])]
    missing = [r for r in refs if r not in ids]
    assert not missing, f"找不到的 id：{missing}"
    srcs = [s["page"] for e in D["events"] for s in e["sources"]] + [p["source"]["page"] for p in D["participations"]]
    assert all(s in page_ids for s in srcs), "出處指到不存在的頁面"


def build():
    page_doc = {p["id"]: p["doc"] for p in D["pages"]}
    page_no = {p["id"]: p["page"] for p in D["pages"]}
    nodes, rels = [], []

    for h in D["heritage"]:
        nodes.append(f"({h['id']}:Heritage {props(h, ('lat', 'lng'))[:-1]}, location: point({{latitude: {h['lat']}, longitude: {h['lng']}}})}})")
    for c in D["components"]:
        nodes.append(f"({c['id']}:Component {props(c, ('heritage',))})")
        rels.append(f"({c['id']})-[:PART_OF]->({c['heritage']})")
    for c in D["craftsmen"]:
        nodes.append(f"({c['id']}:Craftsman {props(c)})")
    for o in D["organizations"]:
        nodes.append(f"({o['id']}:Organization {props(o)})")
    for p in D["projects"]:
        nodes.append(f"({p['id']}:Project {props(p, ('executor', 'commissioner'))})")
        if "executor" in p:
            rels.append(f"({p['executor']})-[:EXECUTED]->({p['id']})")
        if "commissioner" in p:
            rels.append(f"({p['commissioner']})-[:COMMISSIONED]->({p['id']})")
    for d in D["documents"]:
        nodes.append(f"({d['id']}:Document {props(d, ('project', 'heritage'))})")
        if "project" in d:
            rels.append(f"({d['project']})-[:PRODUCED]->({d['id']})")
        rels += [f"({d['id']})-[:DESCRIBES]->({h})" for h in d["heritage"]]
    for g in D["pages"]:
        nodes.append(f"({g['id']}:Page {{id: {lit(g['id'])}, page: {g['page']}, ocrConfidence: {g['ocr']}, text: {lit(g['text'])}}})")
        rels.append(f"({g['id']})-[:IN]->({g['doc']})")
    for e in D["events"]:
        claims = {s["claim"] for s in e["sources"] if "claim" in s}
        extra = {"disputed": True} if len(claims) > 1 else {}
        nodes.append(f"({e['id']}:Event {props({**e, **extra}, ('about', 'sources', 'project'))})")
        rels += [f"({e['id']})-[:ABOUT]->({a})" for a in e["about"]]
        if "project" in e:
            rels.append(f"({e['project']})-[:INCLUDES]->({e['id']})")
        for s in e["sources"]:
            ev = {k: v for k, v in s.items() if k != "page"}
            if s["method"] == "LLM":
                ev["model"] = "demo"
            rels.append(f"({e['id']})-[:EVIDENCED_BY {props(ev)}]->({s['page']})")
    for p in D["participations"]:
        s = p["source"]
        r = {"role": p["role"], "doc": page_doc[s["page"]], "page": page_no[s["page"]], **{k: v for k, v in s.items() if k != "page"}}
        corr = p.get("correction", {})
        body = props({**r, **{k: v for k, v in corr.items() if k != "reviewedAt"}})
        if "reviewedAt" in corr:
            body = body[:-1] + f", reviewedAt: date({lit(corr['reviewedAt'])})}}"
        rels.append(f"({p['craftsman']})-[:PARTICIPATED_IN {body}]->({p['event']})")

    head = ("// 由 to_cypher.py 從 docs/data.json 產生，請勿手改。全部為虛構資料。\n"
            "// 整段貼進 Neo4j Query 執行一次（重跑前先 MATCH (n) DETACH DELETE n）；範例查詢見 queries.cypher\n")
    return head + "CREATE\n " + ",\n ".join(nodes + rels) + ";\n"


if __name__ == "__main__":
    check()
    (ROOT / "seed.cypher").write_text(build(), encoding="utf-8")
    print(f"seed.cypher 已產生：{sum(len(D[k]) for k in D if k != '_note')} 筆資料")
