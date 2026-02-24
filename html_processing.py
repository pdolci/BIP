import html
import re
from html.parser import HTMLParser

CHAPTER_TITLE_RE = re.compile(
    r"^\s*(?:capitolo|chapter|libro|parte|prologo|epilogo)\b[^\n]{0,160}$",
    re.IGNORECASE,
)
ROMAN_OR_NUMERIC_TITLE_RE = re.compile(r"^\s*(?:[IVXLCDM]+|\d+)(?:\s*[-.)]\s*|\s+)[^\n]{0,160}$")


class HtmlSanitizer(HTMLParser):
    """Sanitizza HTML preservando la struttura narrativa di base."""

    ALLOWED_TAGS = {
        "p", "br", "div", "span", "blockquote", "pre", "code",
        "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "b", "strong", "i", "em", "u", "s", "sup", "sub", "hr", "a",
    }
    VOID_TAGS = {"br", "hr"}
    DROP_CONTENT_TAGS = {"script", "style", "noscript", "template", "iframe", "object", "embed", "svg", "math"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.output = []
        self.open_tags = []
        self.drop_depth = 0

    def _sanitize_attrs(self, tag, attrs):
        if tag == "a":
            safe_attrs = []
            for key, value in attrs:
                name = (key or "").lower()
                if name not in {"href", "title"}:
                    continue
                text = (value or "").strip()
                if name == "href":
                    lowered = text.lower()
                    if lowered.startswith(("javascript:", "data:", "vbscript:")):
                        continue
                safe_attrs.append((name, html.escape(text, quote=True)))
            return safe_attrs
        return []

    def _append_tag(self, tag, attrs, close=False):
        if close:
            self.output.append(f"</{tag}>")
            return
        safe_attrs = self._sanitize_attrs(tag, attrs)
        attrs_chunk = "".join([f' {k}="{v}"' for k, v in safe_attrs])
        self.output.append(f"<{tag}{attrs_chunk}>")

    def handle_starttag(self, tag, attrs):
        normalized_tag = (tag or "").lower()
        if normalized_tag in self.DROP_CONTENT_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth > 0:
            return
        if normalized_tag not in self.ALLOWED_TAGS:
            return
        self._append_tag(normalized_tag, attrs, close=False)
        if normalized_tag not in self.VOID_TAGS:
            self.open_tags.append(normalized_tag)

    def handle_endtag(self, tag):
        normalized_tag = (tag or "").lower()
        if normalized_tag in self.DROP_CONTENT_TAGS:
            if self.drop_depth > 0:
                self.drop_depth -= 1
            return
        if self.drop_depth > 0 or normalized_tag not in self.ALLOWED_TAGS or normalized_tag in self.VOID_TAGS:
            return
        if normalized_tag in self.open_tags:
            while self.open_tags:
                pending = self.open_tags.pop()
                self._append_tag(pending, [], close=True)
                if pending == normalized_tag:
                    break

    def handle_startendtag(self, tag, attrs):
        normalized_tag = (tag or "").lower()
        if self.drop_depth > 0 or normalized_tag not in self.ALLOWED_TAGS:
            return
        if normalized_tag in self.VOID_TAGS:
            self._append_tag(normalized_tag, attrs, close=False)

    def handle_data(self, data):
        if self.drop_depth > 0 or not data:
            return
        self.output.append(html.escape(data))

    def handle_entityref(self, name):
        if self.drop_depth == 0:
            self.output.append(f"&{name};")

    def handle_charref(self, name):
        if self.drop_depth == 0:
            self.output.append(f"&#{name};")

    def get_html(self):
        while self.open_tags:
            self._append_tag(self.open_tags.pop(), [], close=True)
        sanitized = "".join(self.output)
        return re.sub(r"\n{3,}", "\n\n", sanitized).strip()


def emphasize_chapter_titles(sanitized_html):
    """Converte intestazioni e paragrafi capitolo in <h2>."""
    normalized = re.sub(r"<\s*/?h[1-6]\b", lambda m: "</h2" if m.group(0).startswith("</") else "<h2", sanitized_html, flags=re.IGNORECASE)

    def _replace_paragraph(match):
        full_text = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
        if CHAPTER_TITLE_RE.match(full_text) or ROMAN_OR_NUMERIC_TITLE_RE.match(full_text):
            return f"<h2>{match.group(1).strip()}</h2>"
        return match.group(0)

    return re.sub(r"<p>(.*?)</p>", _replace_paragraph, normalized, flags=re.IGNORECASE | re.DOTALL)


def sanitize_uploaded_html(content):
    sanitizer = HtmlSanitizer()
    sanitizer.feed(content)
    sanitizer.close()
    cleaned = sanitizer.get_html()
    return emphasize_chapter_titles(cleaned)
