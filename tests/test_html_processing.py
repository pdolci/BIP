import unittest

from html_processing import sanitize_uploaded_html


class HtmlProcessingTests(unittest.TestCase):
    def test_removes_dangerous_tags_and_attributes(self):
        source = """
        <html><body>
            <script>alert('x')</script>
            <p onclick=\"x\">Testo <a href=\"javascript:alert(1)\">bad</a> <a href=\"https://example.com\">ok</a></p>
        </body></html>
        """
        result = sanitize_uploaded_html(source)
        self.assertNotIn("script", result.lower())
        self.assertNotIn("onclick", result.lower())
        self.assertNotIn("javascript:", result.lower())
        self.assertIn('<a href="https://example.com">ok</a>', result)

    def test_converts_headings_and_chapter_paragraph_to_h2(self):
        source = """
        <h1>Titolo Libro</h1>
        <p>Capitolo 1 - Inizio</p>
        <p>Testo normale.</p>
        """
        result = sanitize_uploaded_html(source)
        self.assertIn("<h2>Titolo Libro</h2>", result)
        self.assertIn("<h2>Capitolo 1 - Inizio</h2>", result)
        self.assertIn("<p>Testo normale.</p>", result)


    def test_chapter_heading_preserves_safe_inline_formatting(self):
        source = """
        <p><strong>Capitolo 3</strong> - <em>Arrivo</em></p>
        """
        result = sanitize_uploaded_html(source)
        self.assertIn("<h2><strong>Capitolo 3</strong> - <em>Arrivo</em></h2>", result)
    def test_chapter_heading_conversion_escapes_inner_html(self):
        source = """
        <p>Capitolo 2 - &lt;img src=x onerror=alert(1)&gt;</p>
        """
        result = sanitize_uploaded_html(source)
        self.assertIn("<h2>Capitolo 2 - &lt;img src=x onerror=alert(1)&gt;</h2>", result)
        self.assertNotIn("<img", result)


if __name__ == "__main__":
    unittest.main()
