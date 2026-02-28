from pathlib import Path
import re


POST_FORM_PATTERN = re.compile(r"<form[^>]*method=[\"']post[\"'][^>]*>", re.IGNORECASE)


def test_all_post_forms_include_csrf_token():
    template_dir = Path(__file__).resolve().parent.parent / "templates"

    for template_path in template_dir.glob("*.html"):
        html = template_path.read_text(encoding="utf-8")
        for form_match in POST_FORM_PATTERN.finditer(html):
            after_form = html[form_match.end(): form_match.end() + 500]
            assert "csrf_token" in after_form, (
                f"Missing CSRF token field in POST form inside {template_path.name}: "
                f"{form_match.group(0)}"
            )
