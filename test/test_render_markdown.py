"""Test S1: render_markdown nie przepuszcza skryptów, handlerów, javascript: ani obrazków."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backendV4.api import Api

render = Api.render_markdown.__get__(Api.__new__(Api))

EVIL = [
    'ok <img src=x onerror="alert(1)">',
    "<script>alert(1)</script>",
    "[x](javascript:alert(1))",
    "![](http://example.com/a.png)",
    '<a href="#" onclick="alert(1)">a</a>',
    "<iframe src=http://example.com></iframe>",
    "<svg onload=alert(1)>",
]
for src in EVIL:
    out = render(src).lower()
    for bad in ("<script", "onerror", "onclick", "onload", "javascript:", "<img", "<iframe", "<svg", "example.com/a.png"):
        assert bad not in out, f"{src!r} -> {out!r}"

out = render("| a | b |\n|:-|-:|\n| 1 | 2 |\n\n```py\nx = 1\n```\n\n**b** [l](https://x.pl)")
for good in ("<table>", "<th", "text-align", "<pre><code", "language-py", "<strong>b</strong>", 'href="https://x.pl"'):
    assert good in out, f"brak {good!r} w {out!r}"
print("OK test_render_markdown")
