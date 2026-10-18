// 範例查詢：一次複製一段到 Neo4j Query 執行。資料來自 seed.cypher（全部虛構）

// ---- 0. 看整張圖 ----
MATCH (n)-[r]->(m) RETURN n, r, m;

// ---- 1. 個案修繕履歷：靈應宮時間軸，每筆附全部出處，矛盾者標示 ----
MATCH (h:Heritage {id:'h1'})
MATCH (e:Event)-[:ABOUT]->(x) WHERE x = h OR (x)-[:PART_OF]->(h)
WITH DISTINCT e
OPTIONAL MATCH (e)-[ev:EVIDENCED_BY]->(p:Page)-[:IN]->(d:Document)
RETURN e.yearFrom AS 年, e.type AS 事件, e.original AS 原始記載,
       coalesce(e.disputed, false) AS 有爭議, e.status AS 狀態,
       collect({文件:d.title, 頁:p.page, 主張:ev.claim, 原文:p.text, 校對:ev.status}) AS 出處
ORDER BY 年;

// ---- 2a. 審查輔助：送審中計畫 → 該個案過去所有修繕、工法、匠師、承攬單位 ----
MATCH (p:Project {status:'審查中'})-[:INCLUDES]->(:Event)-[:ABOUT]->(x)
OPTIONAL MATCH (x)-[:PART_OF]->(hp:Heritage)
WITH DISTINCT p, coalesce(hp, x) AS h
MATCH (e:Event)-[:ABOUT]->(y) WHERE (y = h OR (y)-[:PART_OF]->(h)) AND NOT (p)-[:INCLUDES]->(e)
WITH DISTINCT p, h, e
OPTIONAL MATCH (c:Craftsman)-[:PARTICIPATED_IN]->(e)
OPTIONAL MATCH (o:Organization)-[:EXECUTED]->(:Project)-[:INCLUDES]->(e)
RETURN p.name AS 送審計畫, h.name AS 個案, e.yearFrom AS 年, e.type AS 事件,
       e.methods AS 工法, e.materials AS 材料, collect(DISTINCT c.name) AS 匠師, collect(DISTINCT o.name) AS 承攬單位
ORDER BY 年;

// ---- 2b. 審查輔助：本次擬修的構件，過去修過幾次、怎麼修 ----
MATCH (p:Project {status:'審查中'})-[:INCLUDES]->(:Event)-[:ABOUT]->(m:Component)<-[:ABOUT]-(e:Event)
WHERE NOT (p)-[:INCLUDES]->(e)
RETURN m.name AS 構件, e.yearFrom AS 年, e.type AS 事件, e.description AS 說明, e.methods AS 工法, e.materials AS 材料
ORDER BY 年;

// ---- 3. 匠師作品：哪些匠師在兩座以上的廟做過事 ----
MATCH (c:Craftsman)-[:PARTICIPATED_IN]->(:Event)-[:ABOUT]->(x)
OPTIONAL MATCH (x)-[:PART_OF]->(hp:Heritage)
WITH c, coalesce(hp, x) AS h
WITH c, collect(DISTINCT h.name) AS 廟 WHERE size(廟) >= 2
RETURN c.name AS 匠師, 廟;

// ---- 3b. 靈應宮的屋頂修過幾次 ----
MATCH (:Heritage {id:'h1'})<-[:PART_OF]-(:Component {type:'屋頂'})<-[:ABOUT]-(e:Event)
WHERE e.type IN ['重修','修復'] AND coalesce(e.status,'') <> '規劃中'
RETURN count(e) AS 次數, collect(e.yearFrom) AS 年份;

// ---- 3c. 某某文化資產研究所承攬過哪些計畫，總經費多少 ----
MATCH (:Organization {name:'某某文化資產研究所'})-[:EXECUTED]->(p:Project)
RETURN collect(p.name) AS 計畫, sum(p.budget) AS 總經費;

// ---- 4a. 校對工作台：所有未校對的抽取結果，信心值低的排前面 ----
CALL {
  MATCH (e:Event)-[ev:EVIDENCED_BY {status:'未校'}]->(p:Page)-[:IN]->(d:Document)
  RETURN '事件' AS 種類, e.type + ' ' + toString(e.yearFrom) AS 事實, d.title AS 文件, p.page AS 頁, p.text AS 原文, ev.confidence AS 信心
  UNION ALL
  MATCH (c:Craftsman)-[r:PARTICIPATED_IN {status:'未校'}]->(e:Event), (d:Document {id:r.doc})
  RETURN '匠師參與' AS 種類, c.name + ' ' + r.role + '（' + e.type + ' ' + toString(e.yearFrom) + '）' AS 事實, d.title AS 文件, r.page AS 頁, r.quote AS 原文, r.confidence AS 信心
}
RETURN * ORDER BY 信心;

// ---- 4b. 校對紀錄：被人工修正過的內容 ----
MATCH ()-[r {status:'已修正'}]->()
RETURN type(r) AS 關係, r.doc AS 文件, r.page AS 頁, r.field AS 欄位,
       r.originalValue AS 原值, r.correctedValue AS 修正後, r.reviewer AS 校對人, r.reviewedAt AS 時間;

// ---- 5. 公開審核：前台看得到的事件（出處已校對、且來源文件可公開） ----
MATCH (e:Event)-[ev:EVIDENCED_BY]->(:Page)-[:IN]->(d:Document)
WITH e, any(x IN collect({s: ev.status, pub: d.public}) WHERE x.s IN ['已校','已修正'] AND x.pub) AS 公開
RETURN e.type AS 事件, e.yearFrom AS 年, 公開 ORDER BY 公開, 年;
