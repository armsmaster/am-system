"""Собирает HTML-версии статей блога рядом с markdown-файлами.

    uv run python scripts/build_blog.py           # пересобрать HTML
    uv run python scripts/build_blog.py --check   # проверить, что HTML актуален (для CI)

Источник истины — markdown. HTML получается из него детерминированно:
без дат и случайных значений, поэтому проверка в CI ловит забытую пересборку.
Каждая страница самодостаточна: стили встроены, внешних ресурсов нет.
"""

from __future__ import annotations

import html
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import markdown
from markdown.extensions.toc import slugify_unicode

BLOG = Path(__file__).resolve().parent.parent / "docs" / "blog"
INDEX = "README"

# Ссылки между статьями ведут на .md — в HTML-версии они должны вести на .html.
# Внешние адреса, якоря и абсолютные пути не трогаем.
MD_LINK = re.compile(r'href="(?![a-zA-Z][a-zA-Z0-9+.-]*:|#|/)([^"#]+)\.md(#[^"]*)?"')


@dataclass(frozen=True)
class Page:
    source: Path
    title: str

    @property
    def target(self) -> Path:
        return self.source.with_suffix(".html")

    @property
    def href(self) -> str:
        return self.target.name


def title_of(source: Path) -> str:
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    raise ValueError(f"{source.name}: нет заголовка первого уровня")


def pages() -> tuple[Page, list[Page]]:
    """Оглавление серии и статьи в порядке номеров."""
    index = Page(BLOG / f"{INDEX}.md", title_of(BLOG / f"{INDEX}.md"))
    posts = [Page(path, title_of(path)) for path in sorted(BLOG.glob("[0-9][0-9]-*.md"))]
    return index, posts


def to_html(text: str) -> str:
    converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        extension_configs={"toc": {"slugify": slugify_unicode}},
    )
    body = converter.convert(text)
    return MD_LINK.sub(lambda m: f'href="{m[1]}.html{m[2] or ""}"', body)


def link(page: Page | None, text: str) -> str:
    return f'<a href="{page.href}">{text}</a>' if page else "<span></span>"


def render(page: Page, previous: Page | None, following: Page | None, is_index: bool) -> str:
    body = to_html(page.source.read_text(encoding="utf-8"))
    back = f'<nav class="nav"><a href="{INDEX}.html">← Все статьи серии</a></nav>'
    pager = ""
    if previous or following:
        left = link(previous, f"← {html.escape(previous.title)}" if previous else "")
        right = link(following, f"{html.escape(following.title)} →" if following else "")
        pager = f'<nav class="pager">{left}{right}</nav>'
    return TEMPLATE.format(
        title=html.escape(page.title), top="" if is_index else back, body=body, pager=pager
    )


def build() -> dict[Path, str]:
    index, posts = pages()
    result = {index.target: render(index, None, None, is_index=True)}
    for number, post in enumerate(posts):
        previous = posts[number - 1] if number > 0 else None
        following = posts[number + 1] if number + 1 < len(posts) else None
        result[post.target] = render(post, previous, following, is_index=False)
    return result


def main(argv: list[str]) -> int:
    # Консоль Windows по умолчанию не печатает кириллицу.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    check = "--check" in argv
    stale: list[str] = []
    for target, content in build().items():
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == content:
            continue
        if check:
            stale.append(target.name)
        else:
            target.write_text(content, encoding="utf-8", newline="\n")
            print(f"собран {target.relative_to(BLOG.parent.parent)}")
    if stale:
        print("HTML-версии устарели, выполните `make blog`: " + ", ".join(stale))
        return 1
    return 0


TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg: #ffffff; --text: #1f2328; --muted: #59636e; --border: #d1d9e0;
  --accent: #1a5fb4; --code-bg: #f4f6f8; --note-bg: #f1f6fd; --note-border: #1a5fb4;
  color-scheme: light dark;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #16191d; --text: #e6e8eb; --muted: #9aa3ad; --border: #30363d;
    --accent: #79aaf0; --code-bg: #1f242a; --note-bg: #1a2433; --note-border: #79aaf0;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--text);
  font: 18px/1.65 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}}
main {{ max-width: 760px; margin: 0 auto; padding: 28px 20px 64px; }}
h1 {{ font-size: 34px; line-height: 1.2; margin: 12px 0 24px; }}
h2 {{ font-size: 26px; line-height: 1.25; margin: 48px 0 16px; }}
h3 {{ font-size: 21px; line-height: 1.3; margin: 32px 0 12px; }}
p, ul, ol {{ margin: 0 0 16px; }}
li {{ margin: 4px 0; }}
a {{ color: var(--accent); }}
strong {{ font-weight: 600; }}
blockquote {{
  margin: 0 0 24px; padding: 16px 20px; background: var(--note-bg);
  border-left: 4px solid var(--note-border); border-radius: 0 8px 8px 0;
}}
blockquote > :last-child {{ margin-bottom: 0; }}
code {{
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 0.86em;
  background: var(--code-bg); padding: 1px 5px; border-radius: 4px;
}}
pre {{
  background: var(--code-bg); padding: 14px 16px; border-radius: 8px;
  overflow-x: auto; line-height: 1.45; margin: 0 0 20px;
}}
pre code {{ background: none; padding: 0; font-size: 15px; }}
/* Длинные имена в строчном коде переносятся, иначе на телефоне появится
   горизонтальная прокрутка всей страницы. */
:not(pre) > code {{ overflow-wrap: anywhere; }}
main {{ overflow-wrap: break-word; }}
table {{
  display: block; overflow-x: auto; border-collapse: collapse;
  margin: 0 0 20px; font-size: 16px; line-height: 1.5;
}}
th, td {{
  border: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top;
}}
th {{ background: var(--code-bg); font-weight: 600; }}
.nav {{ font-size: 15px; }}
.pager {{
  display: flex; justify-content: space-between; gap: 24px; margin-top: 56px;
  padding-top: 16px; border-top: 1px solid var(--border); font-size: 16px;
}}
.pager a:last-child {{ text-align: right; }}
@media (max-width: 600px) {{
  body {{ font-size: 17px; }}
  h1 {{ font-size: 28px; }}
  h2 {{ font-size: 23px; }}
  .pager {{ flex-direction: column; }}
}}
</style>
</head>
<body>
<main>
{top}
{body}
{pager}
</main>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
