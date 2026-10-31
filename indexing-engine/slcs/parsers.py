"""Symbol extraction.

* Python: the standard-library `ast` module (exact).
* JavaScript/TypeScript/Java/Go/Rust/C#/PHP/C/C++: a *heuristic* declaration scanner (regex on comment/string-masked
  source + brace matching). It is deliberately not a full parser: unusual formatting can be missed. Misses degrade
  gracefully to line-window chunks (see chunker.py). Tree-sitter is the planned upgrade (docs/planning, section 36).
* SQL: statement splitter with dollar-quote/comment/string masking.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Optional

from .models import CodeUnit
from .textio import split_lines

# Words that start statements (checked against the first token of a line)...
CONTROL_WORDS = {
    "return", "new", "else", "throw", "if", "while", "for", "foreach", "switch", "catch", "await", "yield", "case",
    "using", "lock", "typeof", "sizeof", "delete", "goto", "do", "try", "finally", "elif", "assert", "defer", "package",
    "import",
}
# ...and words that can never be a declared name. Ordinary words such as `of`, `get`, `with`, `select` may be method names.
CONTROL_NAMES = {"if", "for", "while", "switch", "catch", "return", "else", "do", "try", "finally", "foreach", "using",
                 "lock", "sizeof", "typeof", "new", "throw", "await", "yield", "case", "function"}


@dataclass
class ParseResult:
    units: list[CodeUnit] = field(default_factory=list)
    parser: str = "none"
    warning: Optional[str] = None


# --------------------------------------------------------------------------------------------- masking
def _blank(out: list[str], i: int, j: int) -> None:
    for k in range(i, j):
        if out[k] != "\n":
            out[k] = " "


def mask_code(text: str, lang: str) -> str:
    """Blank out comments and string contents (keeping newlines and length) so brace counting is reliable."""
    out = list(text)
    n = len(text)
    i = 0
    line_markers = ("//", "#") if lang == "php" else ("//",)
    rust_char = re.compile(r"'(?:\\.|[^\\'\n])'")
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            _blank(out, i, j)
            i = j
            continue
        if any(text.startswith(m, i) for m in line_markers):
            j = text.find("\n", i)
            j = n if j == -1 else j
            _blank(out, i, j)
            i = j
            continue
        if c in "\"'`":
            if lang == "rust" and c == "'":
                m = rust_char.match(text, i)
                if m:
                    _blank(out, i + 1, m.end() - 1)
                    i = m.end()
                else:
                    i += 1  # lifetime such as 'a
                continue
            if lang == "csharp" and c == '"' and i > 0 and text[i - 1] == "@":
                j = i + 1
                while j < n:
                    if text[j] == '"':
                        if j + 1 < n and text[j + 1] == '"':
                            j += 2
                            continue
                        break
                    j += 1
                _blank(out, i + 1, j)
                i = min(j + 1, n)
                continue
            multiline = c == "`" and lang in ("javascript", "typescript", "go")
            j = i + 1
            while j < n:
                ch = text[j]
                if ch == "\\" and lang != "go" or (ch == "\\" and c != "`"):
                    j += 2
                    continue
                if ch == c:
                    break
                if ch == "\n" and not multiline:
                    break
                j += 1
            _blank(out, i + 1, min(j, n))
            i = j + 1 if j < n and text[j] == c else j
            continue
        i += 1
    return "".join(out)


def mask_sql(text: str) -> str:
    out = list(text)
    n = len(text)
    i = 0
    dollar = re.compile(r"\$[A-Za-z_]*\$")
    while i < n:
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            _blank(out, i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            _blank(out, i, j)
            i = j
        elif text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            _blank(out, i + 1, min(j, n))
            i = j + 1
        elif text[i] == "$":
            m = dollar.match(text, i)
            if m:
                tag = m.group(0)
                j = text.find(tag, m.end())
                j = n if j == -1 else j
                _blank(out, m.end(), j)
                i = min(j + len(tag), n)
            else:
                i += 1
        else:
            i += 1
    return "".join(out)


# --------------------------------------------------------------------------------------------- helpers
def find_block_end(mlines: list[str], i0: int, start_col: int = 0) -> Optional[int]:
    """0-based index of the line holding the closing brace of the first {...} block at/after line i0.
    Returns None for declarations without a body (prototype, abstract) or when braces never balance."""
    depth = 0
    paren = 0
    started = False
    for i in range(i0, len(mlines)):
        line = mlines[i]
        for ch in (line[start_col:] if i == i0 else line):
            if ch == "(":
                paren += 1
            elif ch == ")":
                paren = max(0, paren - 1)
            elif ch == "{" and paren == 0:
                depth += 1
                started = True
            elif ch == "}" and paren == 0 and started:
                depth -= 1
                if depth == 0:
                    return i
            elif ch == ";" and not started and paren == 0:
                return None
        if not started and i - i0 >= 12:
            return None
    return None


_COMMENT_PREFIXES = {
    "default": ("//", "/*", "*", "*/", "@", "#["),
    "php": ("//", "/*", "*", "*/", "#", "@"),
    "python": ("#",),
    "sql": ("--", "/*", "*", "*/"),
}


def extend_up(raw_lines: list[str], start_idx: int, lang: str) -> int:
    """Include contiguous comment/annotation lines directly above a declaration (0-based index)."""
    prefixes = _COMMENT_PREFIXES.get(lang, _COMMENT_PREFIXES["default"])
    i = start_idx
    steps = 0
    while i > 0 and steps < 40:
        prev = raw_lines[i - 1].strip()
        if not prev:
            break
        is_attr = lang == "csharp" and prev.startswith("[") and prev.endswith("]")
        if prev.startswith(prefixes) or is_attr:
            i -= 1
            steps += 1
        else:
            break
    return i


# --------------------------------------------------------------------------------------------- Python
def parse_python(text: str) -> ParseResult:
    tree = ast.parse(text)
    raw = split_lines(text)
    units: list[CodeUnit] = []

    def func_start(node) -> int:
        decorators = [d.lineno for d in getattr(node, "decorator_list", [])]
        return min([node.lineno] + decorators)

    def visit_class(node: ast.ClassDef, prefix: str) -> None:
        qual = f"{prefix}{node.name}"
        start = func_start(node)
        start = extend_up(raw, start - 1, "python") + 1
        children = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        if not children:
            units.append(CodeUnit(qual, "class", start, node.end_lineno, prefix[:-1] or None))
            return
        first_child_start = min(func_start(c) for c in children)
        first_child_start = extend_up(raw, first_child_start - 1, "python") + 1
        if first_child_start - 1 >= start:
            units.append(CodeUnit(qual, "class", start, first_child_start - 1, prefix[:-1] or None))
        for c in children:
            if isinstance(c, ast.ClassDef):
                visit_class(c, qual + ".")
            else:
                s = extend_up(raw, func_start(c) - 1, "python") + 1
                units.append(CodeUnit(f"{qual}.{c.name}", "method", s, c.end_lineno, qual))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            s = extend_up(raw, func_start(node) - 1, "python") + 1
            units.append(CodeUnit(node.name, "function", s, node.end_lineno))
        elif isinstance(node, ast.ClassDef):
            visit_class(node, "")
    return ParseResult(units, "python-ast")


# --------------------------------------------------------------------------------------------- brace languages
_MODS = r"(?:(?:public|private|protected|internal|static|final|abstract|sealed|partial|synchronized|native|default|override|virtual|async|unsafe|extern|inline|const|readonly|export|declare)\s+)*"
_CLIKE_FUNC = re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*" + _MODS +
    r"(?:<[^>]+>\s+)?(?P<type>[\w:<>\[\],.*&?]+(?:\s*[*&]+)?)\s+(?P<name>~?[A-Za-z_]\w*)\s*\(")
_CLIKE_CTOR = re.compile(r"^\s*" + _MODS + r"(?P<name>[A-Z]\w*)\s*\([^;]*$")

PATTERNS: dict[str, dict] = {
    "javascript": {
        "containers": [re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?P<kind>class)\s+(?P<name>[A-Za-z_$][\w$]*)")],
        "functions": [
            re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)\s*[(<]"),
            re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]+?)?=\s*(?:async\s+)?"
                       r"(?:function\b|\([^)]*\)\s*(?::\s*[^=]+?)?\s*=>|[A-Za-z_$][\w$]*\s*=>|\(\s*$)"),
        ],
        "method": re.compile(r"^\s*(?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*"
                             r"(?P<name>[A-Za-z_$#][\w$]*)\s*(?:<[^>]*>)?\s*\("),
    },
    "go": {
        "containers": [re.compile(r"^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+(?P<kind>struct|interface)\s*\{")],
        "functions": [re.compile(r"^\s*func\s+(?:\((?P<recv>[^)]*)\)\s*)?(?P<name>[A-Za-z_]\w*)\s*[\[(]")],
    },
    "rust": {
        "containers": [re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?P<kind>struct|enum|trait|impl|mod)\b(?:<[^>]*>)?\s*(?P<name>[A-Za-z_][\w]*)?")],
        "functions": [re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?fn\s+(?P<name>[A-Za-z_]\w*)")],
    },
    "php": {
        "containers": [re.compile(r"^\s*(?:(?:abstract|final)\s+)?(?P<kind>class|interface|trait)\s+(?P<name>[A-Za-z_]\w*)")],
        "functions": [re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+&?(?P<name>[A-Za-z_]\w*)\s*\(")],
    },
    "java": {
        "containers": [re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*" + _MODS + r"(?P<kind>class|interface|enum|record)\s+(?P<name>[A-Za-z_]\w*)")],
        "functions": [_CLIKE_FUNC],
    },
    "csharp": {
        "containers": [re.compile(r"^\s*" + _MODS + r"(?P<kind>class|interface|struct|enum|record)\s+(?P<name>[A-Za-z_]\w*)")],
        "functions": [_CLIKE_FUNC],
    },
    "c": {
        "containers": [re.compile(r"^\s*(?:typedef\s+)?(?P<kind>struct)\s+(?P<name>[A-Za-z_]\w*)\s*(?:\{.*)?$")],
        "functions": [_CLIKE_FUNC],
    },
    "cpp": {
        "containers": [re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?(?P<kind>class|struct)\s+(?P<name>[A-Za-z_]\w*)[^;(]*$")],
        "functions": [_CLIKE_FUNC],
    },
}
PATTERNS["typescript"] = {
    "containers": PATTERNS["javascript"]["containers"] + [
        re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?P<kind>interface|enum)\s+(?P<name>[A-Za-z_$][\w$]*)")],
    "functions": PATTERNS["javascript"]["functions"],
    "method": PATTERNS["javascript"]["method"],
}


@dataclass
class _Cand:
    idx: int
    kind: str
    name: str
    is_container: bool
    end_idx: int = -1


def parse_brace_language(text: str, lang: str) -> ParseResult:
    spec = PATTERNS[lang]
    raw = split_lines(text)
    mlines = [ln[:-1] if ln.endswith("\r") else ln for ln in mask_code(text, lang).split("\n")]
    method_rx = spec.get("method")
    cands: list[_Cand] = []
    open_containers: list[int] = []  # end indices of containers enclosing the current line
    for i, ml in enumerate(mlines):
        if not ml.strip():
            continue
        while open_containers and i > open_containers[-1]:
            open_containers.pop()
        if _try_container(ml, i, mlines, spec, cands, open_containers):
            continue
        rxs = list(spec["functions"])
        if method_rx is not None and open_containers:
            rxs.append(method_rx)
        for rx in rxs:
            m = rx.match(ml)
            if not m:
                continue
            name = m.group("name")
            first_tok = ml.strip().split()[0].lstrip("@") if ml.strip() else ""
            if name in CONTROL_NAMES or first_tok in CONTROL_WORDS:
                break
            js_like = lang in ("javascript", "typescript")
            start_col = ml.find("=>") + 2 if "=>" in ml and js_like else 0
            end = find_block_end(mlines, i, start_col)
            if end is None and "=>" in ml and js_like:
                end = i
                for j in range(i, min(i + 15, len(mlines))):
                    end = j
                    if ";" in mlines[j]:
                        break
            if end is None:
                break
            kind = "function"
            recv = m.groupdict().get("recv")
            if lang == "go" and recv:
                name = f"{recv.split()[-1].lstrip('*').split('[')[0]}.{name}"
                kind = "method"
            cands.append(_Cand(i, kind, name, False, end))
            break
    return _assemble(cands, raw, lang)


def _try_container(ml: str, i: int, mlines: list[str], spec: dict, cands: list, open_containers: list[int]) -> bool:
    for rx in spec["containers"]:
        m = rx.match(ml)
        if not m:
            continue
        kind = m.group("kind")
        name = (m.groupdict().get("name") or "").strip()
        if kind == "impl" and " for " in ml:
            name = re.split(r"\bfor\b", ml, maxsplit=1)[1].strip().split()[0].strip("{<")
        if not name:
            return False
        end = find_block_end(mlines, i)
        if end is None:
            return False
        cands.append(_Cand(i, kind, name, True, end))
        open_containers.append(end)
        return True
    return False


def _assemble(cands: list[_Cand], raw: list[str], lang: str) -> ParseResult:
    units: list[CodeUnit] = []
    containers: list[CodeUnit] = []
    stack: list[tuple[_Cand, CodeUnit]] = []
    for c in sorted(cands, key=lambda c: c.idx):
        while stack and c.idx > stack[-1][0].end_idx:
            stack.pop()
        parent = stack[-1] if stack else None
        if parent is not None and not parent[0].is_container:
            continue  # declarations nested in a function body belong to that function
        start = extend_up(raw, c.idx, lang)
        name, kind = c.name, c.kind
        parent_name = parent[1].name if parent else None
        if parent is not None:
            if kind == "function":
                kind = "method"
            if "." not in name or c.is_container:
                name = f"{parent_name}.{name}"
        u = CodeUnit(name, kind, start + 1, c.end_idx + 1, parent_name)
        units.append(u)
        if c.is_container:
            containers.append(u)
        stack.append((c, u))
    # A container that has members becomes a header-only unit so members are not indexed twice.
    for cu in containers:
        inside = [m for m in units if m is not cu and m.start_line >= cu.start_line and m.end_line <= cu.end_line]
        if inside:
            cu.end_line = min(m.start_line for m in inside) - 1
    result = [u for u in units if u.end_line >= u.start_line]
    result.sort(key=lambda u: (u.start_line, u.end_line))
    return ParseResult(result, f"heuristic-{lang}")


# --------------------------------------------------------------------------------------------- SQL
_CREATE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+)?(?:TEMP(?:ORARY)?\s+)?(?:MATERIALIZED\s+)?"
    r"(?P<kind>TABLE|VIEW|FUNCTION|PROCEDURE|INDEX|TRIGGER|TYPE|SCHEMA|SEQUENCE)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<name>[\w.\"`\[\]]+)", re.I)
_VERB = re.compile(r"^\s*(?P<verb>ALTER|DROP|INSERT|UPDATE|DELETE|SELECT|WITH|GRANT|TRUNCATE)\b", re.I)
_OBJ = re.compile(r"\b(?:TABLE|INTO|UPDATE|FROM|VIEW)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([\w.\"`\[\]]+)", re.I)


def parse_sql(text: str) -> ParseResult:
    masked = mask_sql(text)
    raw = split_lines(text)
    line_starts = [0]
    for m in re.finditer("\n", masked):
        line_starts.append(m.end())

    def line_of(offset: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    units: list[CodeUnit] = []
    pos = 0
    for part in masked.split(";"):
        seg_start = pos
        pos += len(part) + 1
        stripped = part.strip()
        if not stripped:
            continue
        first = seg_start + (len(part) - len(part.lstrip()))
        last = seg_start + len(part.rstrip()) - 1
        s_line, e_line = line_of(first), line_of(last)
        if s_line > len(raw):
            continue
        e_line = min(e_line + 0, len(raw))
        head = stripped[:400]
        m = _CREATE.match(head)
        if m:
            name, kind = m.group("name").strip('"`[]'), m.group("kind").lower()
        else:
            v = _VERB.match(head)
            if not v:
                continue
            o = _OBJ.search(head)
            kind = "statement"
            name = f"{v.group('verb').upper()} {o.group(1).strip(chr(34) + '`[]')}" if o else v.group("verb").upper()
        s_idx = extend_up(raw, s_line - 1, "sql")
        units.append(CodeUnit(name, kind, s_idx + 1, e_line))
    return ParseResult(units, "sql-splitter")


# --------------------------------------------------------------------------------------------- dispatcher
def parse_units(text: str, language: str) -> ParseResult:
    """Never raises: a parser failure returns an empty result with a warning so the chunker can fall back."""
    try:
        if language == "python":
            try:
                return parse_python(text)
            except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
                return ParseResult([], "python-ast", f"python parse failed ({type(exc).__name__}); using line windows")
        if language in PATTERNS:
            return parse_brace_language(text, language)
        if language == "sql":
            return parse_sql(text)
    except Exception as exc:  # defensive: heuristic scanners must never break indexing
        return ParseResult([], "error", f"parser error ({type(exc).__name__}); using line windows")
    return ParseResult([], "none")
