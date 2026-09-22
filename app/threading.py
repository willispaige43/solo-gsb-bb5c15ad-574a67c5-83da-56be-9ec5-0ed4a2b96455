"""按 RFC822 引用关系重建邮件会话树。

输入为数据库中的邮件行，输出会话森林。规则：

1. 父节点选择：优先 In-Reply-To，否则取 References 链最后一个引用；
   引用链中缺失的邮件创建为「占位根」（placeholder），缺失引用之间
   也按链顺序连边。
2. 循环引用：沿父指针做三色遍历检测环，断开闭环处的边，将该节点置为
   根并以 is_cycle / broken_edge 标记。
3. 主题归并：没有任何引用头（References、In-Reply-To 均为空）、也未
   涉及断环的真实邮件，若归一化主题相同，则归到时间最早的一封之下。
4. 排序：所有层级均按 (时间, Message-ID) 稳定排序，时间缺失排末尾。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

# 去掉 Re:/Fwd:/Aw:/Fw: 等多级回复/转发前缀
_PREFIX_RE = re.compile(r"^\s*(?:re|fw|fwd|aw|wg|sv)\s*(\[\d+\])?\s*:\s*", re.IGNORECASE)


def normalize_subject(subject: str | None) -> str:
    if not subject:
        return ""
    s = subject.strip()
    while True:
        new = _PREFIX_RE.sub("", s, count=1)
        if new == s:
            break
        s = new.strip()
    return re.sub(r"\s+", " ", s).lower()


def _date_key(row: sqlite3.Row) -> tuple[int, float, str]:
    ts = row["date_ts"]
    return (1, 0.0, row["message_id"]) if ts is None else (0, float(ts), row["message_id"])


def _propose_parents(
    rows: list[sqlite3.Row],
) -> tuple[dict[str, str | None], set[str]]:
    """根据引用头提议父边，并收集需要创建的占位 Message-ID。"""
    real_ids = {r["message_id"] for r in rows if not r["placeholder"]}
    referenced: set[str] = set()
    parents: dict[str, str | None] = {
        r["message_id"]: None for r in rows
    }
    # 占位链内部的连边（缺失引用按出现顺序串起来）
    placeholder_parent: dict[str, str] = {}

    for row in rows:
        if row["placeholder"]:
            continue
        mid = row["message_id"]
        refs: list[str] = _loads(row["refs_json"])
        irt = row["in_reply_to"]

        chain: list[str] = []
        for ref in refs:
            if ref not in chain:
                chain.append(ref)
        if irt and irt not in chain:
            # In-Reply-To 是最直接的父节点；未出现在 References 时追加到链尾
            chain.append(irt)
        if not chain:
            parents[mid] = None
            continue

        referenced.update(chain)
        # 缺失引用之间按链顺序串联（仅在尚未指定父节点时）
        prev: str | None = None
        for ref in chain:
            if ref not in real_ids:
                if prev is not None and ref not in placeholder_parent:
                    placeholder_parent[ref] = prev
            prev = ref

        direct = irt if irt in chain else chain[-1]
        # 注意：direct 可能等于 mid（自引用），留给 break_cycles 检测并标记
        parents[mid] = direct

    for ph, par in placeholder_parent.items():
        parents[ph] = par

    missing = referenced - real_ids
    return parents, missing


def _loads(value: str | None) -> list[str]:
    import json

    try:
        data = json.loads(value or "[]")
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def break_cycles(
    parents: dict[str, str | None],
) -> dict[str, str]:
    """在单父指针图上检测并断开环，返回 {节点: 被断开的父 Message-ID}。

    每个节点最多一条父边，因此从每个节点沿父指针上溯，用路径位置表
    即可定位环：再次遇到当前路径上的节点时，断开「当前节点」的父边。
    """
    broken: dict[str, str] = {}
    state: dict[str, int] = {nid: 0 for nid in parents}  # 0 白 / 1 灰 / 2 黑

    for start in parents:
        if state[start] != 0:
            continue
        path: list[str] = []
        index: dict[str, int] = {}
        cur = start
        while cur is not None and state.get(cur, 2) == 0:
            state[cur] = 1
            index[cur] = len(path)
            path.append(cur)
            nxt = parents.get(cur)
            if nxt is None:
                break
            if state.get(nxt, 2) == 1 and nxt in index:
                # 发现环：断开 cur -> nxt 这条边（含自引用 cur == nxt）
                broken[cur] = nxt
                parents[cur] = None
                break
            if state.get(nxt, 2) == 2:
                break
            cur = nxt
        for nid in path:
            state[nid] = 2
    return broken


def _merge_by_subject(
    rows: list[sqlite3.Row],
    parents: dict[str, str | None],
    broken: dict[str, str],
) -> None:
    """把同主题、无任何引用头的散落邮件归并到最早的一封之下。"""
    by_id = {r["message_id"]: r for r in rows}
    groups: dict[str, list[str]] = {}
    for row in rows:
        mid = row["message_id"]
        if row["placeholder"]:
            continue
        if parents.get(mid) is not None or mid in broken:
            continue
        if row["in_reply_to"] or _loads(row["refs_json"]):
            continue
        subject = normalize_subject(row["subject"])
        if not subject:
            continue
        groups.setdefault(subject, []).append(mid)

    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: _date_key(by_id[m]))
        root = members[0]
        for mid in members[1:]:
            parents[mid] = root


def build_forest(rows: list[sqlite3.Row]) -> dict[str, Any]:
    """根据数据库行重建会话森林，返回前端可直接渲染的结构。"""
    parents, missing = _propose_parents(rows)

    # 补入占位节点
    by_id: dict[str, sqlite3.Row] = {r["message_id"]: r for r in rows}
    existing = set(by_id)
    for mid in missing:
        if mid not in existing:
            raise RuntimeError(f"placeholder missing from db: {mid}")

    broken = break_cycles(parents)
    _merge_by_subject(rows, parents, broken)

    # 反向邻接表
    children: dict[str, list[str]] = {}
    for nid, par in parents.items():
        if par is not None:
            children.setdefault(par, []).append(nid)

    roots = [nid for nid, par in parents.items() if par is None]

    def row_key(mid: str) -> tuple[int, float, str]:
        return _date_key(by_id[mid])

    for kids in children.values():
        kids.sort(key=row_key)

    def subtree_key(mid: str) -> tuple[int, float, str]:
        # 迭代求子树最小 (无时间标记, 时间)
        best_ts: tuple[int, float] = (1, 0.0)
        stack = [mid]
        while stack:
            cur = stack.pop()
            ts = by_id[cur]["date_ts"]
            cand = (1, 0.0) if ts is None else (0, float(ts))
            if cand < best_ts:
                best_ts = cand
            stack.extend(children.get(cur, []))
        return (best_ts[0], best_ts[1], mid)

    roots.sort(key=subtree_key)

    def to_node(mid: str, depth: int) -> dict[str, Any]:
        row = by_id[mid]
        import json as _json

        date_ts = row["date_ts"]
        return {
            "message_id": mid,
            "subject": row["subject"],
            "from": row["body_from"],
            "date_raw": row["date_raw"],
            "date_ts": date_ts,
            "in_reply_to": row["in_reply_to"],
            "references": _loads(row["refs_json"]),
            "placeholder": bool(row["placeholder"]),
            "is_cycle": bool(row["is_cycle"] or mid in broken),
            "broken_edge": broken.get(mid) or row["broken_edge"],
            "depth": depth,
            "children": [to_node(c, depth + 1) for c in children.get(mid, [])],
        }

    forest = [to_node(r, 0) for r in roots]

    real_rows = [r for r in rows if not r["placeholder"]]
    real_ids_set = {r["message_id"] for r in real_rows}
    orphans = [
        nid
        for nid in roots
        if nid in real_ids_set and not children.get(nid)
    ]

    return {
        "threads": forest,
        "stats": {
            "total": len(real_rows),
            "placeholders": sum(1 for r in rows if r["placeholder"]),
            "cycles": len(broken),
            "thread_roots": len(roots),
            "orphans": len(orphans),
        },
        "orphan_ids": sorted(orphans),
        "missing_refs": sorted(missing),
    }
