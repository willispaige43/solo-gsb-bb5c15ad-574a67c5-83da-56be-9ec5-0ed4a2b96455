"""会话树重建：引用关系建树、缺失父节点占位、循环引用断开并标记、稳定排序。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    message_id: str
    subject: str = ""
    sender: str = ""
    date: str = ""
    date_ts: int = 0
    placeholder: bool = False          # 被引用但库中不存在的缺失邮件
    cycle_broken: bool = False         # 本节点参与的循环引用已被断开
    cycle_target: str = ""             # 被断开的原父节点 Message-ID
    parent: "Node | None" = field(default=None, repr=False)
    children: list["Node"] = field(default_factory=list, repr=False)

    def sort_key(self) -> tuple[int, str]:
        """稳定排序键：(时间戳, Message-ID)。占位节点继承最早子节点的键，
        使其排在回复它的第一封邮件附近。"""
        if self.placeholder:
            if self.children:
                return min(c.sort_key() for c in self.children)
            return (0, self.message_id)
        return (self.date_ts, self.message_id)

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "subject": self.subject,
            "sender": self.sender,
            "date": self.date,
            "placeholder": self.placeholder,
            "cycle_broken": self.cycle_broken,
            "cycle_target": self.cycle_target,
            "children": [c.to_dict() for c in self.children],
        }


def _pick_parent_id(msg: dict) -> str | None:
    """父节点候选：References 列表末尾优先，其次 In-Reply-To（跳过自引用）。"""
    refs = [r for r in (msg.get("references") or "").split() if r]
    if msg.get("in_reply_to"):
        refs.append(msg["in_reply_to"])
    for rid in reversed(refs):
        if rid and rid != msg["message_id"]:
            return rid
    return None


def _creates_cycle(node: Node, parent: Node) -> bool:
    """沿 parent 的祖先链向上走，若遇到 node 说明挂接后会成环。"""
    cur: Node | None = parent
    while cur is not None:
        if cur is node:
            return True
        cur = cur.parent
    return False


def build_threads(messages: list[dict]) -> tuple[list[Node], dict]:
    """把扁平邮件列表重建为会话森林。返回 (根节点列表, 统计信息)。"""
    nodes: dict[str, Node] = {}

    # 第一遍：按 (时间, Message-ID) 稳定顺序创建所有真实节点
    ordered = sorted(messages, key=lambda m: (m["date_ts"], m["message_id"]))
    for m in ordered:
        node = nodes.get(m["message_id"])
        if node is None:
            node = Node(message_id=m["message_id"])
            nodes[m["message_id"]] = node
        node.placeholder = False
        node.subject = m.get("subject") or "(无主题)"
        node.sender = m.get("sender") or ""
        node.date = m.get("date") or ""
        node.date_ts = m.get("date_ts") or 0

    # 第二遍：按引用关系挂接，缺失父节点建占位节点，循环则断开并标记
    for m in ordered:
        node = nodes[m["message_id"]]
        parent_id = _pick_parent_id(m)
        if parent_id is None:
            continue
        parent = nodes.get(parent_id)
        if parent is None:
            parent = Node(message_id=parent_id, placeholder=True, subject="(缺失的邮件)")
            nodes[parent_id] = parent
        if _creates_cycle(node, parent):
            node.cycle_broken = True
            node.cycle_target = parent_id
            continue  # 断开此边，node 成为根
        node.parent = parent
        parent.children.append(node)

    roots = [n for n in nodes.values() if n.parent is None]

    def sort_tree(n: Node) -> None:
        n.children.sort(key=lambda c: c.sort_key())
        for c in n.children:
            sort_tree(c)

    for r in roots:
        sort_tree(r)
    roots.sort(key=lambda r: r.sort_key())

    stats = {
        "total": len(messages),
        "threads": len(roots),
        "placeholders": sum(1 for n in nodes.values() if n.placeholder),
        "cycles": sum(1 for n in nodes.values() if n.cycle_broken),
    }
    return roots, stats


def find_orphans(messages: list[dict]) -> list[dict]:
    """孤立邮件：既不引用任何邮件、也没有被任何邮件引用的单封邮件。"""
    referenced: set[str] = set()
    for m in messages:
        for rid in (m.get("references") or "").split():
            referenced.add(rid)
        if m.get("in_reply_to"):
            referenced.add(m["in_reply_to"])

    orphans = [
        m for m in messages
        if not (m.get("references") or "").strip()
        and not (m.get("in_reply_to") or "").strip()
        and m["message_id"] not in referenced
    ]
    orphans.sort(key=lambda m: (m["date_ts"], m["message_id"]))
    return orphans
