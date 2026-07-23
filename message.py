"""Message processing: filter and clean chatroom messages.

Only type=="msg" messages are displayed. HTML content is cleaned
(scripts/iframes/styles removed, <img> preserved, other tags stripped).
"""

import re

# Pre-compiled regex patterns for HTML cleaning
_SCRIPT_RE = re.compile(r"(?is)<script[^>]*>.*?</script>")
_IFRAME_RE = re.compile(r"(?is)<iframe[^>]*>.*?</iframe>")
_STYLE_RE = re.compile(r"(?is)<style[^>]*>.*?</style>")
_IMG_RE = re.compile(r"(?is)<img[^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"(?is)<br\s*/?>|</p>|</div>|</h[1-6]>")

# Placeholder pattern to restore img tags
_PLACEHOLDER_RE = re.compile(r"\{\{IMG_(\d+)\}\}")


def clean_html(content: str) -> str:
    """Clean HTML content: remove scripts/iframes/styles, preserve <img>, strip other tags.

    Preserves explicit line breaks (<br>, </p>, </div>) as newline characters so the
    danmu overlay can render them with the same formatting as the chatroom.
    """
    # Remove dangerous elements
    content = _SCRIPT_RE.sub("", content)
    content = _IFRAME_RE.sub("", content)
    content = _STYLE_RE.sub("", content)

    # Convert block/line-break tags to newlines before stripping tags
    content = _BR_RE.sub("\n", content)

    # Protect <img> tags with placeholders
    img_tags = _IMG_RE.findall(content)
    for i, img_tag in enumerate(img_tags):
        content = content.replace(img_tag, f"{{{{IMG_{i}}}}}", 1)

    # Strip all remaining HTML tags
    content = _TAG_RE.sub("", content)

    # Restore <img> tags
    def _restore(m):
        idx = int(m.group(1))
        return img_tags[idx] if idx < len(img_tags) else ""

    content = _PLACEHOLDER_RE.sub(_restore, content)
    return content.strip()


def should_display(msg_type: str) -> bool:
    """Only 'msg' type messages should be displayed."""
    return msg_type == "msg"


def _format_red_packet(content: str) -> tuple[str, bool]:
    """Detect red-packet JSON and return a human-readable text plus a flag.

    Returns (formatted_content, is_red_packet).
    """
    import json

    stripped = content.strip()
    if not stripped.startswith("{"):
        return content, False

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return content, False

    msg_type = data.get("msgType")
    if msg_type != "redPacket":
        return content, False

    msg = data.get("msg", "")
    count = data.get("count", 0)
    packet_type = data.get("type", "random")
    type_label = "拼手气红包" if packet_type == "random" else "普通红包"

    if msg and count:
        formatted = f"🧧 {msg} [{type_label} {count}]"
    elif msg:
        formatted = f"🧧 {msg} [{type_label}]"
    elif count:
        formatted = f"🧧 {type_label} {count}"
    else:
        formatted = "🧧 [红包]"

    return formatted, True


def process_message(raw_msg: dict) -> dict | None:
    """Process a raw chatroom message dict into a display message.

    Returns None if the message should be filtered out.
    """
    msg_type = raw_msg.get("type", "")
    if not should_display(msg_type):
        return None

    content = raw_msg.get("content", "")
    cleaned = clean_html(content)

    # Detect and format red-packet messages
    cleaned, is_red_packet = _format_red_packet(cleaned)

    nickname = raw_msg.get("userNickname", "")
    if not nickname:
        nickname = raw_msg.get("userName", "")

    return {
        "nickname": nickname,
        "avatar_url": raw_msg.get("userAvatarURL48", ""),
        "content": cleaned,
        "has_image": "<img" in cleaned,
        "is_red_packet": is_red_packet,
    }
