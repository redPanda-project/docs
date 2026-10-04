#!/usr/bin/env python3
"""Offline check of repository-relative links in the docs (no HTTP requests).

Checks, for every *.md / *.adoc file in the repository:
  * Markdown links  [text](target)          -> target file exists; #anchor exists
                                               in the target Markdown file
  * AsciiDoc macros link:target[...]        -> same as above
                    include::target[...]
  * AsciiDoc xrefs  <<id>> / <<id,text>>    -> id is defined somewhere in the
                                               AsciiDoc sources ([[id]] / [#id])

External links (http, https, mailto, ...) are ignored on purpose: they make CI
flaky. Markdown anchors follow GitHub's heading-slug rules (plus explicit
<a id/name="..."> tags). Fenced code blocks, inline code, AsciiDoc
listing/literal/comment/passthrough blocks and // comments are skipped, as are
targets containing AsciiDoc {attribute} references.

Usage: scripts/check_links.py [repo-root]   (default: parent of this script)
Exit code 1 if any link is broken.
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent).resolve()
SKIP_DIRS = {".git", "node_modules", "build"}

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
ADOC_DELIM_RE = re.compile(r"^(-{4,}|\.{4,}|/{4,}|\+{4,})\s*$")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
ADOC_MACRO_RE = re.compile(r"\b(link|include|image|xref)::?([^\[\s]+)\[")
ADOC_XREF_RE = re.compile(r"<<([^,>]+)(?:,[^>]*)?>>")
ADOC_ID_RE = re.compile(r"\[\[([^\],]+)(?:,[^\]]*)?\]\]|^\[#([\w:-]+)[^\]]*\]")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
HTML_ANCHOR_RE = re.compile(r"<a\s+(?:id|name)=\"([^\"]+)\"", re.I)
EXTERNAL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")  # http:, https:, mailto:, ...


def doc_files():
    for p in sorted(ROOT.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".md", ".adoc") \
                and not SKIP_DIRS.intersection(p.relative_to(ROOT).parts):
            yield p


def prose_lines(path, strip_code=True):
    """Yield (lineno, line) outside code/comment blocks, inline code removed."""
    adoc = path.suffix.lower() == ".adoc"
    fence = None  # opening delimiter of the block we are in, if any
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = FENCE_RE.match(line) or (adoc and ADOC_DELIM_RE.match(line))
        if m:
            delim = m.group(1)
            if fence is None:
                fence = delim
            elif delim == fence or (delim[0] == fence[0] in "`~" and len(delim) >= len(fence)):
                fence = None  # AsciiDoc: exact delimiter; Markdown: same char, >= length
            continue
        if fence is not None or (adoc and line.startswith("//")):
            continue
        yield no, INLINE_CODE_RE.sub("", line) if strip_code else line


def github_slug(text):
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [text](url) -> text
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


_anchor_cache = {}


def md_anchors(path):
    if path not in _anchor_cache:
        anchors, seen = set(), {}
        for _, line in prose_lines(path, strip_code=False):
            m = HEADING_RE.match(line)
            if m:
                slug = github_slug(m.group(2))
                n = seen.get(slug, 0)
                seen[slug] = n + 1
                anchors.add(slug if n == 0 else f"{slug}-{n}")
            anchors.update(HTML_ANCHOR_RE.findall(line))
        _anchor_cache[path] = anchors
    return _anchor_cache[path]


def check_target(src, target, adoc_ids):
    """Return an error string, or None if the relative link resolves."""
    if EXTERNAL_RE.match(target) or "{" in target:
        return None
    path_part, _, anchor = target.partition("#")
    path_part = unquote(path_part.partition("?")[0])
    anchor = unquote(anchor)
    if path_part.startswith("/"):  # GitHub resolves these against the repo root
        dest = (ROOT / path_part.lstrip("/")).resolve()
    else:
        dest = (src.parent / path_part).resolve() if path_part else src
    if not dest.is_relative_to(ROOT):
        return f"points outside the repository: {path_part}"
    if not dest.exists():
        return f"missing file: {path_part}"
    if anchor and dest.suffix.lower() == ".md" and anchor not in md_anchors(dest):
        return f"missing anchor #{anchor} in {dest.relative_to(ROOT)}"
    if anchor and dest.suffix.lower() == ".adoc" and anchor not in adoc_ids:
        return f"unknown AsciiDoc id #{anchor}"
    return None


def main():
    files = list(doc_files())
    adoc_ids = set()
    for f in files:
        if f.suffix.lower() == ".adoc":
            for _, line in prose_lines(f, strip_code=False):
                for a, b in ADOC_ID_RE.findall(line):
                    adoc_ids.add(a or b)

    errors, checked = [], 0
    for f in files:
        rel = f.relative_to(ROOT)
        for no, line in prose_lines(f):
            targets = []
            if f.suffix.lower() == ".md":
                targets = MD_LINK_RE.findall(line)
            else:
                targets, refs = [], ADOC_XREF_RE.findall(line)
                for macro, t in ADOC_MACRO_RE.findall(line):
                    if macro == "xref" and "#" not in t and not t.endswith(".adoc"):
                        refs.append(t)  # xref:id[] within the same document
                    else:
                        targets.append(t)
                for ref in refs:
                    checked += 1
                    ref = ref.strip()
                    if "#" in ref or ref.endswith(".adoc"):
                        targets.append(ref)
                    elif ref not in adoc_ids:
                        errors.append(f"{rel}:{no}: unknown AsciiDoc id <<{ref}>>")
            for t in targets:
                checked += 1
                err = check_target(f, t, adoc_ids)
                if err:
                    errors.append(f"{rel}:{no}: {t} -> {err}")

    for e in errors:
        print(e)
    print(f"checked {checked} relative links in {len(files)} files, {len(errors)} broken")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
