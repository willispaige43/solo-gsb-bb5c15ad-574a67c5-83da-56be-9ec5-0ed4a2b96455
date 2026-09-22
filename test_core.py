"""不依赖 FastAPI 的核心逻辑冒烟测试（纯标准库）。"""
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(__file__))

from app.parser import parse_rfc822


class FakeRow:
    def __init__(self, d):
        self._d = d

    def __getitem__(self, k):
        return self._d[k]

    def get(self, k, default=None):
        return self._d.get(k, default)


def make_rows(messages):
    rows = []
    for m in messages:
        rows.append(FakeRow({
            "message_id": m["message_id"],
            "subject": m.get("subject"),
            "in_reply_to": m.get("in_reply_to"),
            "refs_json": json.dumps(m.get("references", [])),
            "date_raw": m.get("date_raw"),
            "date_ts": m.get("date_ts"),
            "body_from": m.get("from"),
            "placeholder": 0,
            "is_cycle": 0,
            "broken_edge": None,
        }))
    # 补齐占位行（模拟数据库 save_graph 的行为）
    from app.threading import _propose_parents
    parents, missing = _propose_parents(rows)
    have = {r["message_id"] for r in rows}
    for mid in sorted(missing - have):
        rows.append(FakeRow({
            "message_id": mid, "subject": None, "in_reply_to": None,
            "refs_json": "[]", "date_raw": None, "date_ts": None,
            "body_from": None, "placeholder": 1, "is_cycle": 0,
            "broken_edge": None,
        }))
    return rows


def flatten_ids(nodes):
    out = []
    for n in nodes:
        out.append(n["message_id"])
        out.extend(flatten_ids(n["children"]))
    return out


passed = []
failed = []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"  -> {detail}"))


# ---------- 1. 解析 ----------
print("== 解析 ==")
raw1 = """From alice Mon Sep 22 10:00:00 2026
Message-ID: <a@x>
Subject: Hello
Date: Mon, 22 Sep 2026 10:00:00 +0800
From: Alice <alice@example.com>

body a
From bob Mon Sep 22 11:00:00 2026
Message-ID: <b@x>
Subject: Re: Hello
In-Reply-To: <a@x>
References: <a@x>
Date: Mon, 22 Sep 2026 11:00:00 +0800
From: bob@example.com

body b
"""
msgs = parse_rfc822(raw1)
check("mbox 两封均解析", len(msgs) == 2, f"got {len(msgs)}")
check("Subject 解码", msgs[0]["subject"] == "Hello")
check("In-Reply-To", msgs[1]["in_reply_to"] == "<a@x>")
check("References", msgs[1]["references"] == ["<a@x>"])
check("Date 时间戳", msgs[0]["date_ts"] is not None)
check("From 解析", "alice@example.com" in msgs[0]["from"])

# 多封直接拼接（无 mbox From 行）
raw2 = "Message-ID: <x@y>\nSubject: s1\n\nbody1\nMessage-ID: <z@y>\nSubject: s2\n\nbody2\n"
msgs2 = parse_rfc822(raw2)
check("直接拼接切成 2 封", len(msgs2) == 2, f"got {len(msgs2)}")

# 无 Message-ID
check("无 Message-ID 返回空", parse_rfc822("Subject: nope\n\nbody") == [])

# 编码主题
raw3 = "Message-ID: <c@x>\nSubject: =?UTF-8?B?5L2g5aW9?=\n\n"
check("编码头解码", parse_rfc822(raw3)[0]["subject"] == "你好")

# 多 ID 头去重
from app.parser import extract_message_ids
check("ID 提取与去重",
      extract_message_ids('<a@x> <b@x> <a@x> text <c@x>') ==
      ["<a@x>", "<b@x>", "<c@x>"])

# ---------- 2. 基本线程重建 ----------
print("== 线程重建 ==")
from app.threading import build_forest, break_cycles, normalize_subject

base = [
    {"message_id": "<a@x>", "subject": "Hello",
     "date_raw": "d1", "date_ts": 1000.0, "from": "alice"},
    {"message_id": "<b@x>", "subject": "Re: Hello", "in_reply_to": "<a@x>",
     "references": ["<a@x>"], "date_raw": "d2", "date_ts": 2000.0, "from": "bob"},
    {"message_id": "<c@x>", "subject": "Re: Hello", "in_reply_to": "<b@x>",
     "references": ["<a@x>", "<b@x>"], "date_raw": "d3", "date_ts": 3000.0, "from": "carol"},
]
res = build_forest(make_rows(base))
check("单线程", len(res["threads"]) == 1, f"roots={len(res['threads'])}")
check("根为 a", res["threads"][0]["message_id"] == "<a@x>")
check("深度链 a->b->c",
      flatten_ids(res["threads"]) == ["<a@x>", "<b@x>", "<c@x>"])
check("无孤立", res["stats"]["orphans"] == 0)

# ---------- 3. 缺失父节点 -> 占位根 ----------
print("== 缺失父节点 ==")
missing = [
    {"message_id": "<r@x>", "subject": "Reply to ghost",
     "in_reply_to": "<ghost@x>", "references": ["<ghost@x>"],
     "date_ts": 5000.0, "date_raw": "d", "from": "d"},
]
res = build_forest(make_rows(missing))
root = res["threads"][0]
check("占位成为根", root["placeholder"] and root["message_id"] == "<ghost@x>")
check("真实邮件挂在占位下",
      root["children"][0]["message_id"] == "<r@x>")
check("占位统计=1", res["stats"]["placeholders"] == 1, str(res["stats"]))

# 缺失链 ghost1 -> ghost2 -> real
chain = [
    {"message_id": "<m@x>", "subject": "deep",
     "in_reply_to": "<g2@x>",
     "references": ["<g1@x>", "<g2@x>"],
     "date_ts": 9000.0, "date_raw": "d", "from": "e"},
]
res = build_forest(make_rows(chain))
ids = flatten_ids(res["threads"])
check("缺失链按引用串联",
      ids == ["<g1@x>", "<g2@x>", "<m@x>"], str(ids))

# ---------- 4. 循环引用 ----------
print("== 循环引用 ==")
cyc = [
    {"message_id": "<p@x>", "subject": "cyc p", "in_reply_to": "<q@x>",
     "references": ["<q@x>"], "date_ts": 100.0, "date_raw": "d", "from": "p"},
    {"message_id": "<q@x>", "subject": "cyc q", "in_reply_to": "<p@x>",
     "references": ["<p@x>"], "date_ts": 200.0, "date_raw": "d", "from": "q"},
]
rows = make_rows(cyc)
res = build_forest(rows)
check("检测到 1 处断环", res["stats"]["cycles"] == 1, str(res["stats"]))
flat = flatten_ids(res["threads"])
check("断开后为森林", set(flat) == {"<p@x>", "<q@x>"})
cycle_nodes = [n for n in flat]
marked = []
def walk(ns):
    for n in ns:
        if n["is_cycle"]:
            marked.append((n["message_id"], n["broken_edge"]))
        walk(n["children"])
walk(res["threads"])
check("环边被标记", len(marked) == 1 and marked[0][1] is not None, str(marked))

# 自引用环
selfcyc = [
    {"message_id": "<s@x>", "subject": "self", "in_reply_to": "<s@x>",
     "references": ["<s@x>"], "date_ts": 1.0, "date_raw": "d", "from": "s"},
]
res = build_forest(make_rows(selfcyc))
check("自引用算环并标记",
      res["stats"]["cycles"] == 1 and res["threads"][0]["is_cycle"] and
      res["threads"][0]["broken_edge"] == "<s@x>",
      str(res["stats"]))

# 三角环 a->b->c->a
tri = [
    {"message_id": "<ta@x>", "subject": "t", "in_reply_to": "<tc@x>",
     "references": ["<tc@x>"], "date_ts": 1.0, "date_raw": "d", "from": "a"},
    {"message_id": "<tb@x>", "subject": "t", "in_reply_to": "<ta@x>",
     "references": ["<ta@x>"], "date_ts": 2.0, "date_raw": "d", "from": "b"},
    {"message_id": "<tc@x>", "subject": "t", "in_reply_to": "<tb@x>",
     "references": ["<tb@x>"], "date_ts": 3.0, "date_raw": "d", "from": "c"},
]
res = build_forest(make_rows(tri))
check("三角环断开一处", res["stats"]["cycles"] == 1, str(res["stats"]))
check("三角环无节点丢失", len(flatten_ids(res["threads"])) == 3)

# ---------- 5. 排序稳定性 ----------
print("== 排序 ==")
unordered = [
    {"message_id": "<z@x>", "subject": "Re: T", "in_reply_to": "<root@x>",
     "references": ["<root@x>"], "date_ts": 30.0, "date_raw": "d", "from": "z"},
    {"message_id": "<root@x>", "subject": "T",
     "date_ts": 10.0, "date_raw": "d", "from": "r"},
    {"message_id": "<a@x>", "subject": "Re: T", "in_reply_to": "<root@x>",
     "references": ["<root@x>"], "date_ts": 20.0, "date_raw": "d", "from": "a"},
]
res = build_forest(make_rows(unordered))
ids = flatten_ids(res["threads"])
check("子节点按时间排序", ids == ["<root@x>", "<a@x>", "<z@x>"], str(ids))

# 同时间按 Message-ID
sametime = [
    {"message_id": "<root@x>", "subject": "T", "date_ts": 10.0, "date_raw": "d", "from": "r"},
    {"message_id": "<z@x>", "subject": "Re: T", "in_reply_to": "<root@x>",
     "references": ["<root@x>"], "date_ts": 10.0, "date_raw": "d", "from": "z"},
    {"message_id": "<a@x>", "subject": "Re: T", "in_reply_to": "<root@x>",
     "references": ["<root@x>"], "date_ts": 10.0, "date_raw": "d", "from": "a"},
]
res = build_forest(make_rows(sametime))
ids = flatten_ids(res["threads"])
check("同时间按 Message-ID 排序",
      ids == ["<root@x>", "<a@x>", "<z@x>"], str(ids))

# 根按子树最早时间排序
two_threads = [
    {"message_id": "<late@x>", "subject": "late", "date_ts": 900.0, "date_raw": "d", "from": "l"},
    {"message_id": "<early@x>", "subject": "early", "date_ts": 100.0, "date_raw": "d", "from": "e"},
    {"message_id": "<reply-late@x>", "subject": "Re: late",
     "in_reply_to": "<late@x>", "references": ["<late@x>"],
     "date_ts": 1000.0, "date_raw": "d", "from": "l2"},
]
res = build_forest(make_rows(two_threads))
check("根按子树最早时间排序",
      [t["message_id"] for t in res["threads"]] == ["<early@x>", "<late@x>"],
      str([t["message_id"] for t in res["threads"]]))

# 时间缺失排末尾
no_date = [
    {"message_id": "<nodate@x>", "subject": "?", "date_ts": None, "date_raw": None, "from": "n"},
    {"message_id": "<dated@x>", "subject": "!", "date_ts": 5.0, "date_raw": "d", "from": "d"},
]
res = build_forest(make_rows(no_date))
check("缺时间排末尾",
      [t["message_id"] for t in res["threads"]] == ["<dated@x>", "<nodate@x>"],
      str([t["message_id"] for t in res["threads"]]))

# ---------- 6. 孤立邮件 & 主题归并 ----------
print("== 孤立与主题归并 ==")
solo = [
    {"message_id": "<solo1@x>", "subject": "Lunch?", "date_ts": 1.0, "date_raw": "d", "from": "a"},
    {"message_id": "<solo2@x>", "subject": "Random", "date_ts": 2.0, "date_raw": "d", "from": "b"},
]
res = build_forest(make_rows(solo))
check("两封均孤立", res["stats"]["orphans"] == 2, str(res["stats"]))
check("不同主题不归并", len(res["threads"]) == 2)

subject_group = [
    {"message_id": "<m1@x>", "subject": "Weekly Sync", "date_ts": 300.0, "date_raw": "d", "from": "a"},
    {"message_id": "<m2@x>", "subject": "weekly sync", "date_ts": 100.0, "date_raw": "d", "from": "b"},
    {"message_id": "<m3@x>", "subject": "Re: Weekly Sync", "date_ts": 200.0, "date_raw": "d", "from": "c"},
]
res = build_forest(make_rows(subject_group))
check("同主题散落邮件归并", len(res["threads"]) == 1, f"roots={len(res['threads'])}")
root = res["threads"][0]
check("最早的为主题根", root["message_id"] == "<m2@x>", root["message_id"])
ids = flatten_ids(res["threads"])
check("归并后顺序按时间", ids == ["<m2@x>", "<m3@x>", "<m1@x>"], str(ids))

check("主题归一化", normalize_subject("Re: Fwd:  Hello ") == "hello")

# 有引用头的不同主题邮件不参与主题归并
irt = [
    {"message_id": "<g@x>", "subject": "G", "date_ts": 1.0, "date_raw": "d", "from": "g"},
    {"message_id": "<h@x>", "subject": "H", "in_reply_to": "<g@x>",
     "references": ["<g@x>"], "date_ts": 2.0, "date_raw": "d", "from": "h"},
]
res = build_forest(make_rows(irt))
check("引用优先于主题归并", flatten_ids(res["threads"]) == ["<g@x>", "<h@x>"])

# ---------- 汇总 ----------
print(f"\n通过 {len(passed)}，失败 {len(failed)}")
sys.exit(1 if failed else 0)
