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

    class ChapterTitleTransformer(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.output = []
            self.in_paragraph = False
            self.paragraph_inner = []
            self.paragraph_text = []

        @staticmethod
        def _render_tag(tag, attrs, close=False, self_close=False):
            if close:
                return f"</{tag}>"
            attrs_chunk = "".join([f' {key}="{html.escape(value or "", quote=True)}"' for key, value in attrs])
            suffix = " /" if self_close else ""
            return f"<{tag}{attrs_chunk}{suffix}>"

        def _append_chunk(self, chunk, text_equivalent=""):
            if self.in_paragraph:
                self.paragraph_inner.append(chunk)
                if text_equivalent:
                    self.paragraph_text.append(text_equivalent)
                return
            self.output.append(chunk)

        def handle_starttag(self, tag, attrs):
            normalized_tag = (tag or "").lower()
            if normalized_tag == "p":
                self.in_paragraph = True
                self.paragraph_inner = []
                self.paragraph_text = []
                return
            if normalized_tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self._append_chunk("<h2>")
                return
            self._append_chunk(self._render_tag(normalized_tag, attrs))

        def handle_endtag(self, tag):
            normalized_tag = (tag or "").lower()
            if normalized_tag == "p" and self.in_paragraph:
                paragraph_text = html.unescape("".join(self.paragraph_text)).strip()
                inner_html = "".join(self.paragraph_inner).strip()
                if CHAPTER_TITLE_RE.match(paragraph_text) or ROMAN_OR_NUMERIC_TITLE_RE.match(paragraph_text):
                    self.output.append(f"<h2>{inner_html}</h2>")
                else:
                    self.output.append(f"<p>{inner_html}</p>")
                self.in_paragraph = False
                self.paragraph_inner = []
                self.paragraph_text = []
                return
            if normalized_tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self._append_chunk("</h2>")
                return
            self._append_chunk(self._render_tag(normalized_tag, [], close=True))

        def handle_startendtag(self, tag, attrs):
            normalized_tag = (tag or "").lower()
            self._append_chunk(self._render_tag(normalized_tag, attrs, self_close=True))

        def handle_data(self, data):
            escaped = html.escape(data)
            self._append_chunk(escaped, text_equivalent=data)

        def handle_entityref(self, name):
            chunk = f"&{name};"
            self._append_chunk(chunk, text_equivalent=html.unescape(chunk))

        def handle_charref(self, name):
            chunk = f"&#{name};"
            self._append_chunk(chunk, text_equivalent=html.unescape(chunk))

        def get_html(self):
            return "".join(self.output)

    transformer = ChapterTitleTransformer()
    transformer.feed(sanitized_html)
    transformer.close()
    return transformer.get_html()


def sanitize_uploaded_html(content):
    sanitizer = HtmlSanitizer()
    sanitizer.feed(content)
    sanitizer.close()
    cleaned = sanitizer.get_html()
    return emphasize_chapter_titles(cleaned)
