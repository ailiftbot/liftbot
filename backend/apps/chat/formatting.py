"""
Turn an AI Employee reply (plain text with light markdown) into safe chat HTML.

Every piece of text is HTML-escaped before it is wrapped in our own tags, so the
output is safe to drop into innerHTML in the widget and the dashboard. Supported:
paragraphs, line breaks, "- " / "* " / "• " bullets, "1." numbered lists,
**bold**, [label](https://link), bare http(s) links and email addresses.
Anything else (headings, tables, stray asterisks) is flattened to plain text.
"""
import re
from html import escape

_BULLET_RE = re.compile(r'^\s*(?:[-*•–]|\d{1,2}[.)])\s+(.*)$')
_NUMBERED_RE = re.compile(r'^\s*\d{1,2}[.)]\s+')
_HEADING_RE = re.compile(r'^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$')
_RULE_RE = re.compile(r'^\s*([-*_])\1{2,}\s*$')
_INLINE_RE = re.compile(
    r'\[(?P<label>[^\]\n]{1,200})\]\((?P<href>https?://[^\s)]+)\)'
    r'|\*\*(?P<bold>[^*\n]+?)\*\*'
    r'|__(?P<bold2>[^_\n]+?)__'
    r'|(?P<url>https?://[^\s<>"]*[^\s<>".,;:!?)\]\'])'
    r'|(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})'
)
# Single *emphasis* / leftover markdown symbols that would look like noise.
_STRAY_EMPHASIS_RE = re.compile(r'(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])')


def _link(href: str, label: str) -> str:
    return f'<a href="{escape(href)}" target="_blank" rel="noopener noreferrer nofollow">{label}</a>'


def _inline(text: str, allow_bold: bool = True) -> str:
    text = _STRAY_EMPHASIS_RE.sub(r'\1', text)
    out, pos = [], 0
    for m in _INLINE_RE.finditer(text):
        out.append(escape(text[pos:m.start()]))
        pos = m.end()
        if m.group('label') is not None:
            out.append(_link(m.group('href'), escape(m.group('label'))))
        elif m.group('bold') is not None or m.group('bold2') is not None:
            inner = m.group('bold') if m.group('bold') is not None else m.group('bold2')
            out.append(f'<strong>{_inline(inner, allow_bold=False)}</strong>' if allow_bold else escape(inner))
        elif m.group('url') is not None:
            url = m.group('url')
            out.append(_link(url, escape(url)))
        else:
            email = m.group('email')
            out.append(_link(f'mailto:{email}', escape(email)))
    out.append(escape(text[pos:]))
    return ''.join(out)


def _line(text: str) -> str:
    heading = _HEADING_RE.match(text)
    if heading:
        return f'<strong>{_inline(heading.group(1), allow_bold=False)}</strong>'
    return _inline(text)


def reply_to_html(text: str) -> str:
    if not text:
        return ''
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    html = []
    for block in re.split(r'\n\s*\n', text):
        lines = [ln for ln in block.split('\n') if ln.strip() and not _RULE_RE.match(ln)]
        para, items, ordered = [], [], False

        def flush_para():
            if para:
                html.append('<p>' + '<br>'.join(_line(ln.strip()) for ln in para) + '</p>')
                para.clear()

        def flush_list():
            if items:
                tag = 'ol' if ordered else 'ul'
                html.append(f'<{tag}>' + ''.join(f'<li>{_inline(i)}</li>' for i in items) + f'</{tag}>')
                items.clear()

        for ln in lines:
            bullet = _BULLET_RE.match(ln)
            if bullet:
                flush_para()
                if not items:
                    ordered = bool(_NUMBERED_RE.match(ln))
                items.append(bullet.group(1).strip())
            elif items and ln.startswith((' ', '\t')):
                # Indented continuation of the previous bullet.
                items[-1] = f'{items[-1]} {ln.strip()}'
            else:
                flush_list()
                para.append(ln)
        flush_para()
        flush_list()
    return ''.join(html)
