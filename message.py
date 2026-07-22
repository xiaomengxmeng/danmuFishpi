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

# Placeholder pattern to restore img tags after stripping
_PLACEHOLDER_RE = re.compile(r"\{\{IMG_(\d+)\}\}")


def clean_html(content: str) -> str:
    """Clean HTML content: remove scripts/iframes/styles, preserve <img>, strip other tags."""
    # Remove dangerous elements
    content = _SCRIPT_RE.sub("", content)
    content = _IFRAME_RE.sub("", content)
    content = _STYLE_RE.sub("", content)

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


def process_message(raw_msg: dict) -> dict | None:
    """Process a raw chatroom message dict into a display message.

    Returns None if the message should be filtered out.
    """
    msg_type = raw_msg.get("type", "")
    if not should_display(msg_type):
        return None

    content = raw_msg.get("content", "")
    cleaned = clean_html(content)

    return {
        "nickname": raw_msg.get("userNickname", ""),
        "avatar_url": raw_msg.get("userAvatarURL48", ""),
        "content": cleaned,
        "has_image": "<img" in cleaned,
    }
