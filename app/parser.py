"""RFC822 邮件元数据解析。

支持：
- 单封邮件原始文本（RFC 822 / MIME 头）
- mbox 风格拼接（"From " 转义行分隔的多封邮件）
- 多封邮件首尾相连（靠 Message-ID 头重新切分）

只提取线程重建所需的元数据：Message-ID、Subject、In-Reply-To、
References、Date 以及 From。
"""
from __future__ import annotations

import re
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

_ID_RE = re.compile(r"<[^<>]+>")
_MBOX_FROM_RE = re.compile(r"^From .+", re.MULTILINE)


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def extract_message_ids(value: str | None) -> list[str]:
    """从头字段中提取 <msg-id>，去重并保持出现顺序。"""
    if not value:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for match in _ID_RE.findall(value):
        mid = match.strip()
        if mid not in seen:
            seen.add(mid)
            result.append(mid)
    return result


def _first_message_id(value: str | None) -> str | None:
    ids = extract_message_ids(value)
    return ids[0] if ids else None


def _parse_date(value: str | None) -> tuple[str | None, float | None]:
    if not value:
        return None, None
    raw = value.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                from datetime import timezone

                dt = dt.replace(tzinfo=timezone.utc)
            return raw, dt.timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    return raw, None


def _parse_from(value: str | None) -> str:
    if not value:
        return ""
    pairs = getaddresses([value])
    if pairs:
        name, addr = pairs[0]
        name = _decode_header_value(name)
        return f"{name} <{addr}>" if name else addr
    return value.strip()


def parse_message(msg: Message) -> dict[str, Any] | None:
    message_id = _first_message_id(msg.get("Message-ID"))
    if not message_id:
        return None
    date_raw, date_ts = _parse_date(msg.get("Date"))
    references = extract_message_ids(msg.get("References"))
    in_reply_to = _first_message_id(msg.get("In-Reply-To"))
    return {
        "message_id": message_id,
        "subject": _decode_header_value(msg.get("Subject")) or None,
        "in_reply_to": in_reply_to,
        "references": references,
        "date_raw": date_raw,
        "date_ts": date_ts,
        "from": _parse_from(msg.get("From")) or None,
    }


def _split_mbox(raw: str) -> list[str]:
    """按 mbox 的 "From " 分隔行切分，同时兼容普通多邮件拼接。"""
    blocks: list[str] = []
    if _MBOX_FROM_RE.search(raw):
        chunks = re.split(r"(?m)^From .+$\n?", raw)
        blocks = [c for c in chunks if c.strip()]
    else:
        # 普通拼接：以新出现的 "Message-ID:" 头切分
        starts = [m.start() for m in re.finditer(r"(?im)^Message-ID\s*:", raw)]
        if len(starts) > 1:
            # 找到每封邮件真正的起始（向上回溯到该邮件第一个头字段行）
            cuts = []
            for pos in starts:
                head = raw.rfind("\n", 0, pos)
                cuts.append(head + 1 if head != -1 else 0)
            blocks = [raw[i:j] for i, j in zip(cuts, cuts[1:] + [len(raw)])]
        else:
            blocks = [raw]
    return blocks


def parse_rfc822(raw: str) -> list[dict[str, Any]]:
    """解析原始 RFC822 文本，返回去重后的邮件元数据列表。"""
    from email import message_from_string

    messages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in _split_mbox(raw):
        block = block.strip()
        if not block:
            continue
        parsed = parse_message(message_from_string(block))
        if parsed and parsed["message_id"] not in seen:
            seen.add(parsed["message_id"])
            messages.append(parsed)
    return messages
