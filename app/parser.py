"""RFC822 邮件元数据解析：提取 Message-ID / Subject / From / Date / In-Reply-To / References。"""
from __future__ import annotations

import hashlib
import re
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

_MSG_ID_RE = re.compile(r"<([^<>\s]+)>")


def _normalize_msg_id(raw: str | None) -> str:
    """去掉尖括号与空白，返回规范化的 Message-ID；为空时返回空串。"""
    if not raw:
        return ""
    raw = raw.strip()
    m = _MSG_ID_RE.search(raw)
    return (m.group(1) if m else raw).strip()


def _parse_references(raw: str | None) -> list[str]:
    """References 头是由空白分隔的 <msg-id> 列表。"""
    if not raw:
        return []
    ids = _MSG_ID_RE.findall(raw)
    if not ids:  # 兼容没有尖括号的写法
        ids = raw.split()
    return [i.strip() for i in ids if i.strip()]


def _parse_date(raw: str | None) -> tuple[str, int]:
    """返回 (ISO 字符串, 时间戳)。解析失败时时间戳为 0，排序时稳定兜底。"""
    if not raw:
        return "", 0
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return "", 0
    if dt is None:
        return "", 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(), int(dt.timestamp())


def parse_eml(data: bytes) -> dict:
    """解析一封 .eml 文件，返回元数据字典。缺少 Message-ID 时生成确定性 ID。"""
    msg = BytesParser(policy=policy.default).parsebytes(data)

    message_id = _normalize_msg_id(msg.get("Message-ID"))
    subject = str(msg.get("Subject") or "(无主题)")
    sender = str(msg.get("From") or "")
    date_iso, date_ts = _parse_date(msg.get("Date"))
    in_reply_to = _normalize_msg_id(msg.get("In-Reply-To"))
    references = _parse_references(msg.get("References"))

    if not message_id:
        digest = hashlib.sha1(data).hexdigest()[:16]
        message_id = f"generated-{digest}"

    return {
        "message_id": message_id,
        "subject": subject,
        "sender": sender,
        "date": date_iso,
        "date_ts": date_ts,
        "in_reply_to": in_reply_to,
        "references": " ".join(references),
    }
