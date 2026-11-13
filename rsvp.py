import json
import posixpath
import re
import unicodedata
import zipfile
from collections import namedtuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote
from urllib.request import ProxyHandler, Request, build_opener

from lxml import etree, html

# --------------------
# HTTP transfer
# --------------------

# Bypass any system proxy: the device is on the local network.
_opener = build_opener(ProxyHandler({}))


def normalize_url(url):
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ValueError("No device URL set.")
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    return url


def _error_from_http(e):
    body = e.read().decode("utf-8", "replace")
    try:
        j = json.loads(body)
        detail = "%s: %s" % (j.get("code", "error"), j.get("message", body))
        if j.get("field"):
            detail += " (field: %s)" % j["field"]
    except ValueError:
        detail = body[:200]
    return RuntimeError("HTTP %d from device - %s" % (e.code, detail))


def ping(base_url, timeout=5):
    """GET /api/v2/device. Returns the parsed JSON dict."""
    base_url = normalize_url(base_url)
    try:
        with _opener.open(base_url + "/api/v2/device", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        raise _error_from_http(e)
    except (URLError, OSError) as e:
        raise RuntimeError(
            "Cannot reach the reader at %s (%s). Open 'Companion sync' on the "
            "Nano and make sure this computer can reach it." % (base_url, getattr(e, "reason", e))
        )


def upload(base_url, category, name, data, timeout=600):
    """POST /api/v2/library?category=..&name=.. with an octet-stream body."""
    base_url = normalize_url(base_url)
    if category not in ("book", "article"):
        raise ValueError("category must be 'book' or 'article'")
    url = "%s/api/v2/library?category=%s&name=%s" % (base_url, category, quote(name, safe=""))
    req = Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    try:
        with _opener.open(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body
    except HTTPError as e:
        raise _error_from_http(e)
    except (URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            raise RuntimeError(
                "The reader closed the connection while uploading '%s'. Details: %s"
                % (name, reason)
            )
        raise RuntimeError("Upload failed: %s" % reason)


# -------------------
# Text cleaning (RsvpText::normalizeDisplayText)
# -------------------

WRAP_WIDTH = 96  # bytes per output line (RsvpWriter.cpp kOutputWrapWidth)
MAX_CHAPTER_BYTES = 256
HEADER_TITLE_BYTES = 70
HEADER_AUTHOR_BYTES = 70
HEADER_SOURCE_BYTES = 60
MAX_NAME_LEN = 80

_PRE_DROP = re.compile("[\x1c-\x1f]")
_WS = re.compile(r"\s+")
_DROP = re.compile("[\u0000-\u001f\u007f-\u009f\u00ad\u200b\ufeff]")
_LIGATURES = {
    ord(k): v
    for k, v in {
        "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
        "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
    }.items()
}


def clean_text(s):
    if not s:
        return ""
    s = _PRE_DROP.sub("", s)
    s = _WS.sub(" ", s)
    s = _DROP.sub("", s)
    s = s.translate(_LIGATURES)
    return _WS.sub(" ", s).strip()


def _is_word_char(ch):
    cat = unicodedata.category(ch)
    return cat[0] == "L" or cat == "Nd"


def readable_key(s):
    return "".join(c.lower() for c in clean_text(s) if _is_word_char(c))


# -------
# Paths
# -------

def _collapse(path):
    parts = []
    for part in path.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def _dir_of(path):
    i = path.rfind("/")
    return path[: i + 1] if i >= 0 else ""


def resolve_zip_path(base_dir, href):
    path = href.split("#", 1)[0].split("?", 1)[0]
    path = unquote(path).replace("\\", "/")
    if not href.startswith("/"):
        path = base_dir + path
    return _collapse(path)


class _ZipIndex:
    def __init__(self, zf):
        self.zf = zf
        self.names = set(zf.namelist())
        self.lower = {n.lower(): n for n in self.names}

    def read(self, name):
        real = name if name in self.names else self.lower.get(name.lower())
        return self.zf.read(real) if real is not None else None


# --------------------
# EPUB package  (EpubPackage.cpp, EpubConverter.cpp)
# --------------------

ManifestItem = namedtuple("ManifestItem", "id path media_type properties")
TocEntry = namedtuple("TocEntry", "path title fragment")


def _parse_html(data):
    """Parse XHTML/HTML bytes. EPUB content is UTF-8 unless declared otherwise,
    but lxml guesses Latin-1 when there is no declaration, which mangles
    accented characters. So force UTF-8 whenever the bytes are valid UTF-8."""
    try:
        data.decode("utf-8")
        parser = html.HTMLParser(encoding="utf-8")
        return html.document_fromstring(data, parser=parser)
    except UnicodeDecodeError:
        return html.document_fromstring(data)


def parse_package(opf, base_dir):
    """opf: parsed OPF root element. Returns a dict."""
    manifest = []
    for it in opf.iterfind(".//{*}manifest/{*}item"):
        href = it.get("href")
        if href:
            manifest.append(ManifestItem(
                it.get("id") or "", resolve_zip_path(base_dir, href),
                it.get("media-type") or "", it.get("properties") or ""))
    spine = [r.get("idref") for r in opf.iterfind(".//{*}spine/{*}itemref") if r.get("idref")]

    def dc(name):
        for el in opf.iterfind(".//{*}metadata/{*}" + name):
            t = clean_text("".join(el.itertext()))
            if t:
                return t
        return ""

    return {
        "version": opf.get("version") or "",
        "manifest": manifest,
        "spine": spine,
        "title": dc("title"),
        "creator": dc("creator"),
        "language": dc("language"),
    }


def _is_content_doc(item):
    mt, p = item.media_type.lower(), item.path.lower()
    return mt in ("application/xhtml+xml", "text/html") or p.endswith((".xhtml", ".html", ".htm"))


def reading_order(manifest, spine):
    by_id = {}
    for m in manifest:
        by_id.setdefault(m.id, m)
    order = [by_id[i].path for i in spine if i in by_id and _is_content_doc(by_id[i])]
    return order or [m.path for m in manifest if _is_content_doc(m)]


def is_content_toc_title(title, book_title):
    cleaned = clean_text(title)
    normalized = readable_key(cleaned)
    bkey = readable_key(book_title)
    return (
        bool(cleaned)
        and cleaned.lower() not in ("contents", "cover", "title page")
        and normalized != "tableofcontents"
        and bool(normalized)
        and (not bkey or normalized != bkey)
        and (not bkey or not bkey.startswith(normalized))
    )


def _toc_entry(toc_path, href, title):
    fragment = href.split("#", 1)[1] if "#" in href else ""
    fragment = unquote(fragment.split("?", 1)[0]).strip()
    return TocEntry(resolve_zip_path(_dir_of(toc_path), href), clean_text(title), fragment)


def parse_ncx_toc(data, toc_path, book_title):
    entries = []
    root = etree.fromstring(data)
    for np_ in root.iter("{*}navPoint"):
        label = np_.find("{*}navLabel/{*}text")
        content = np_.find("{*}content")
        if label is None or content is None:
            continue
        href = content.get("src") or ""
        title = "".join(label.itertext())
        if href and is_content_toc_title(title, book_title):
            entries.append(_toc_entry(toc_path, href, title))
    return entries


def parse_nav_toc(data, toc_path, book_title):
    entries = []
    doc = _parse_html(data)
    scope = doc
    for nav in doc.iter("nav"):
        types = (nav.get("epub:type") or "") + " " + (nav.get("{http://www.idpf.org/2007/ops}type") or "") \
            + " " + (nav.get("type") or "")
        if "toc" in types.split():
            scope = nav
            break
    for a in scope.iter("a"):
        href = a.get("href") or ""
        title = a.text_content()
        if href and is_content_toc_title(title, book_title):
            entries.append(_toc_entry(toc_path, href, title))
    return entries


def read_toc(idx, manifest, epub3, book_title):
    navs = [m for m in manifest if "nav" in m.properties.split()]
    ncxs = [m for m in manifest if m.media_type.lower() == "application/x-dtbncx+xml"]

    def first_readable(items, is_nav):
        for it in items:
            data = idx.read(it.path)
            if data is None:
                continue
            try:
                entries = (parse_nav_toc if is_nav else parse_ncx_toc)(data, it.path, book_title)
            except (etree.XMLSyntaxError, etree.ParserError, ValueError):
                continue
            if entries:
                return entries
        return []

    order = [(navs, True), (ncxs, False)] if epub3 else [(ncxs, False), (navs, True)]
    for items, is_nav in order:
        entries = first_readable(items, is_nav)
        if entries:
            return entries
    return []


def fallback_chapter_title(path):
    base = posixpath.basename(path)
    name = base.rsplit(".", 1)[0] if "." in base else base
    title = name.replace("_", " ").replace("-", " ").strip()
    if title and "a" <= title[0] <= "z":
        title = title[0].upper() + title[1:]
    return title


# ----------------------------
# Content document parser (EpubContentParser.cpp)
# ----------------------------

_SKIP_TAGS = {"head", "script", "style", "svg", "math", "nav"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "br", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "header", "hr", "li", "main", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}


class DocParser:
    """Feeds ('chapter', title) and ('para', text) events to `emit`.

    The firmware turns headings into chapter markers (never body text), emits
    table-of-contents titles when it reaches their anchors or the next heading,
    and ends a paragraph at every closing block tag, <br>, <hr> and <li>.
    """

    def __init__(self, toc, has_toc, fallback_title, book_title, emit):
        self.toc = list(toc)
        self.has_toc = has_toc
        self.fallback = fallback_title
        self.book_title = book_title
        self.emit = emit
        self.line = ""
        self.heading = ""
        self.in_heading = False
        self.chapter_written = False
        self.next_toc = 0
        if len(self.toc) == 1:
            self._chapter(self.toc[0].title)
            self.next_toc = 1

    def _chapter(self, title):
        self.chapter_written = True
        self.emit("chapter", title)

    def _flush(self):
        text = clean_text(self.line)
        self.line = ""
        if not text:
            return
        if not self.chapter_written and not self.has_toc and self.fallback:
            self._chapter(self.fallback)
        self.emit("para", text)

    def _emit_through(self, index):
        if self.next_toc > index or self.next_toc >= len(self.toc):
            return
        self._flush()
        while self.next_toc <= index and self.next_toc < len(self.toc):
            self._chapter(self.toc[self.next_toc].title)
            self.next_toc += 1

    def _match(self, anchor):
        if not anchor:
            return -1
        for i in range(self.next_toc, len(self.toc)):
            if self.toc[i].fragment and self.toc[i].fragment == anchor:
                return i
        return -1

    def _suppress(self, heading):
        if not self.has_toc:
            return False
        key = readable_key(heading)
        return key in ("contents", "tableofcontents") or (
            bool(self.book_title) and key == readable_key(self.book_title))

    def _text(self, s):
        if not s:
            return
        if self.in_heading:
            self.heading += s
        else:
            self.line += s

    def _start(self, el, name):
        anchor = (el.get("id") or el.get("name") or "").strip()
        m = self._match(anchor)
        if m >= 0:
            self._emit_through(m)
        if name in _HEADINGS:
            self._flush()
            if self.toc and m < 0 and self.next_toc < len(self.toc):
                self._emit_through(self.next_toc)
            self.in_heading = True
            self.heading = ""
        elif name in _BLOCK_TAGS:
            if name in ("br", "hr", "li"):
                self._flush()
            else:
                self.line += " "
        self._text(el.text)

    def _end(self, el, name):
        if name in _HEADINGS:
            self.in_heading = False
            cleaned = clean_text(self.heading)
            self.heading = ""
            if not self.toc and not self._suppress(cleaned):
                self._chapter(cleaned)
        elif name in _BLOCK_TAGS:
            self._flush()
        self._text(el.tail)

    def feed(self, data):
        try:
            root = _parse_html(data)
        except (etree.ParserError, etree.XMLSyntaxError, ValueError):
            return False
        etree.strip_elements(root, etree.Comment, etree.ProcessingInstruction, with_tail=False)
        skip_el = None
        for event, el in etree.iterwalk(root, events=("start", "end")):
            name = el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""
            if skip_el is not None:
                if event == "end" and el is skip_el:
                    skip_el = None
                    self._text(el.tail)
                continue
            if event == "start":
                if name in _SKIP_TAGS:
                    self._flush()
                    skip_el = el
                    continue
                self._start(el, name)
            else:
                self._end(el, name)
        return True

    def finish(self):
        self._flush()
        if self.toc:
            self._emit_through(len(self.toc) - 1)


def _check_drm(idx):
    data = idx.read("META-INF/encryption.xml")
    if not data:
        return
    for uri in re.findall(rb'CipherReference[^>]*URI="([^"]+)"', data):
        if uri.lower().endswith((b".xhtml", b".html", b".htm", b".xml", b".opf", b".ncx")):
            raise ValueError("This EPUB is DRM-protected and can't be converted.")


def epub_chapters(path, title_hint=None):
    """Returns ([(chapter_title_or_None, [paragraph, ...]), ...], language_or_None)."""
    with zipfile.ZipFile(path) as zf:
        idx = _ZipIndex(zf)
        _check_drm(idx)
        container = etree.fromstring(idx.read("META-INF/container.xml"))
        opf_path = container.find(".//{*}rootfile").get("full-path")
        opf = etree.fromstring(idx.read(opf_path))
        pkg = parse_package(opf, _dir_of(opf_path))

        book_title = pkg["title"] or title_hint or posixpath.basename(path).rsplit(".", 1)[0]
        toc = read_toc(idx, pkg["manifest"], pkg["version"].startswith("3"), book_title)
        has_toc = bool(toc)

        events = []
        emit = lambda kind, value: events.append((kind, value))
        for doc_path in reading_order(pkg["manifest"], pkg["spine"]):
            data = idx.read(doc_path)
            if data is None:
                continue
            doc_toc = [e for e in toc if e.path.lower() == doc_path.lower()]
            parser = DocParser(doc_toc, has_toc, fallback_chapter_title(doc_path), book_title, emit)
            parser.feed(data)
            parser.finish()

    chapters, title, paras = [], None, []
    for kind, value in events:
        if kind == "chapter":
            chapters.append((title, paras))
            title, paras = value, []
        else:
            paras.append(value)
    chapters.append((title, paras))
    return chapters, normalize_locale(pkg["language"])


# --------------------
# Plain text reading
# --------------------

def text_chapters(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        s = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        s = raw.decode("latin-1")
    paras = [clean_text(p) for p in re.split(r"\n\s*\n", s)]
    return [(None, [p for p in paras if p])]


# -------------------
# .rsvp rendering  (RsvpWriter.cpp)
# -------------------

def normalize_locale(tag):
    tag = (tag or "").strip()
    if not tag or len(tag) > 35 or "_" in tag:
        return None
    out = []
    for i, p in enumerate(tag.split("-")):
        if not p or len(p) > 8 or not (p.isascii() and p.isalnum()):
            return None # Not valid
        if i == 0 and (len(p) < 2 or not p.isalpha()):
            return None # Not valid
        if i == 1 and len(p) == 4 and p.isalpha():
            p = p.title()
        elif i > 0 and len(p) == 2 and p.isalpha():
            p = p.upper()
        else:
            p = p.lower()
        out.append(p)
    return "-".join(out)


def _bounded(text, max_bytes):
    # Truncates max_bytes of UTF-8 without splitting a character
    b = clean_text(text).encode("utf-8", "replace")[:max_bytes]
    return b.decode("utf-8", "ignore").strip()


def _header_value(text, max_bytes):
    # Truncates at a word boundary, for the short header lines
    t = clean_text(text)
    if len(t.encode("utf-8", "replace")) <= max_bytes:
        return t
    cut = t.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "ignore")
    sp = cut.rfind(" ")
    if sp > max_bytes // 2:
        cut = cut[:sp]
    return cut.rstrip(" ,;:-")


def _wrap_bytes(text, width=WRAP_WIDTH):
    """Greedy word wrap by UTF-8 byte length, like the firmware (a word longer than
    the width stays on a line of its own)."""
    lines, cur, cur_len = [], [], 0
    for tok in text.split():
        n = len(tok.encode("utf-8", "replace"))
        add = n + (1 if cur else 0)
        if cur and cur_len + add > width:
            lines.append(" ".join(cur))
            cur, cur_len = [tok], n
        else:
            cur.append(tok)
            cur_len += add
    if cur:
        lines.append(" ".join(cur))
    return lines


def _readable_words(text):
    return sum(1 for t in text.split() if any(_is_word_char(c) for c in t))


def render_rsvp(title, author, chapters, source=None, language=None):
    lines = ["@rsvp 1", "@title " + (_header_value(title, HEADER_TITLE_BYTES) or "Untitled")]
    author = _header_value(author, HEADER_AUTHOR_BYTES)
    if author:
        lines.append("@author " + author)
    lines.append("@source " + _header_value(source or "", HEADER_SOURCE_BYTES))
    if language:
        lines.append("@language " + language)
    lines.append("")

    words = 0
    last_chapter = ""
    for ctitle, paras in chapters:
        ct = _bounded(ctitle, MAX_CHAPTER_BYTES) if ctitle else ""
        if ct and ct != last_chapter:
            if words > 0 or last_chapter:
                lines.append("")
            lines.append("@chapter " + ct)
            last_chapter = ct
        for p in paras:
            text = clean_text(p)
            if not text:
                continue
            if words > 0:
                lines.append("")
                lines.append("@para")
            for ln in _wrap_bytes(text):
                lines.append("@" + ln if ln.startswith("@") else ln)
            words += _readable_words(text)
    if words == 0:
        raise ValueError("No readable text found in this book.")
    return ("\n".join(lines) + "\n").encode("utf-8", "replace"), words


def convert_to_rsvp(path, fmt, title, author, source=None):
    """path/fmt must be an EPUB or TXT file. Returns (bytes, word_count)."""
    fmt = fmt.upper()
    language = None
    if fmt == "EPUB":
        chapters, language = epub_chapters(path, title)
        language = language if language and len(language.split("-")[0]) == 2 else None
    elif fmt == "TXT":
        chapters = text_chapters(path)
    else:
        raise ValueError("Unsupported format for conversion: " + fmt)
    return render_rsvp(title, author, chapters, source, language)


def safe_name(title, author, book_id):
    """ASCII-safe base filename (no extension)."""
    base = title if not author else "%s - %s" % (title, author)
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", base)
    base = re.sub(r"\s+", " ", base).strip(" .")
    base = base[:MAX_NAME_LEN].strip(" .")
    return base or "book-%s" % book_id