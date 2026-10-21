#!/usr/bin/env python3
"""Generate EDA.ipynb from EDA.py.  EDA.py stays the single source of truth; the notebook is rebuilt, never hand-synced.

    uv run python tools/make_notebook.py             # (re)write EDA.ipynb next to EDA.py
    uv run python tools/make_notebook.py --check     # exit 1 when EDA.ipynb is out of date (writes nothing)
    uv run python tools/make_notebook.py --smoke     # also execute every definition cell once (no data needed)
    uv run python tools/make_notebook.py --plan      # print the cell list instead of writing

How the notebook is cut from EDA.py (nothing is retyped, so it cannot drift):
    * the banner comments ("# 2. SMALL HELPERS ...", "# SECTION 4 · ...") split the file into chunks; a chunk with an
      @register(...) function is a *section*, every other chunk is *support code*
    * support code goes first (it must exist before the data is loaded), then one Start cell, then each section as
      "markdown heading, code cell(s), run(n) cell" in file order
    * main() is not copied: its head (arguments -> ctx), its section loop and its tail (summary + HTML) are lifted from
      the AST of main() itself, so a change to main() reaches the notebook on the next regeneration
    * `ROOT = Path(__file__)...` becomes the kernel's working directory; main() and the `__main__` guard are dropped

Only the standard library is needed to generate; --smoke imports the project's dependencies.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULE = re.compile(r"^# ═{20,}\s*$")
ROOT_LINE = "ROOT = Path(__file__).resolve().parent"
ROOT_REPLACEMENT = (
    "ROOT = Path.cwd().resolve()  # notebook: the folder the kernel runs in (EDA.py: the script's folder)"
)
MAX_CELL_LINES = 160  # support code is cut into cells of about this size, at statement boundaries
HIDE_ABOVE_LINES = 25  # longer definition cells start collapsed (JupyterLab honours it)


class GenerationError(Exception):
    pass


# ══════════════════════════════════════════════════════════════════════════════════
# Notebook text that is not in EDA.py (kept short: everything else is lifted from EDA.py)
# ══════════════════════════════════════════════════════════════════════════════════
INTRO = """\
# EDA · pre-modeling analysis of the loan portfolio (notebook edition)

This notebook is **generated from `EDA.py`** by `tools/make_notebook.py`: the same code, sections and numbers, but every \
section's text, tables and charts appear right below its own cell instead of in one HTML page, and everything stays in \
memory (`ctx`, `rep`) so you can poke at it afterwards.

**How to use**
1. **Kernel:** the project's `.venv` (it needs `ipykernel`). Open the notebook from the project folder, the one that holds `Data/`.
2. **Parameters:** edit the next cell (`FAST = True` gives a quick first pass).
3. **Run all** (about 5 minutes for the full data, about 2 minutes with `FAST = True`) or go step by step: *support code → Start → sections*. \
Sections hand results forward through `ctx.store`, so run them in order.
4. **Re-run one section** with `run(n)`: its previous result is replaced, not duplicated. Edit a section's code cell, run it, then run its `run(n)` cell.
5. The last cells write the same hand-off files as `python EDA.py` (`eda_report.html`, `summary.md`, `feature_roles.csv`, `figures/`, `tables/`) into `OUT_DIR`.

**Keep in mind**
- `EDA.py` remains the source of truth (tests, `make eda`, README). To change the analysis for good, edit `EDA.py` and regenerate: \
`uv run python tools/make_notebook.py`. The generator refuses to overwrite a notebook whose cells you edited unless you pass `--force`; \
`--out other.ipynb` writes a copy instead.
- Code cells of the support code and of the sections are collapsed in JupyterLab; click the cell to expand.
- Nothing is written into `Data/` or `openbanking_mock/`.
"""

PARAMS = """\
# ── Parameters: the notebook equivalent of the command-line flags of EDA.py ─────────────────────
FAST = False  # True: stratified 20,000-row sample and lighter models (--fast)
NO_MODELS = False  # True: skip the model-based checks: adversarial validation (3), score reverse-engineering (8), all of 9
NO_MOCK = False  # True: skip Part II, the mock / synthetic bank data (sections 11-14)
ONLY_SECTIONS = ""  # e.g. "1,2,8" to run only those sections; "" = all (EDA.py's --sections)
TARGET = None  # another binary column, e.g. "RestructuringStatusBool"; None = the documented outcome flag
BINARIZE = None  # with a numeric TARGET: "<=150" or ">=85"
N_JOBS = 4  # upper bound of CPU threads for models / BLAS
DATA_DIR = None  # None = ./Data
MOCK_DIR = None  # None = ./openbanking_mock/output/dataset when it exists
OUT_DIR = "reports/notebook"  # figures/, tables/, eda_report.html ... (reports/ itself belongs to `python EDA.py`)
EXPORT_MOCK_FEATURES = False  # True: also write mock_customer_features.csv (one row per mock customer, synthetic)
EXTRA_ARGS = []  # any other EDA.py flag, e.g. ["--open"]
LIVE_LOG = False  # True: also stream the console log (findings, timings) while a section runs
"""

START = """\
# ── Start: read the data, build `ctx` (shared by every section) and open the mock bank data ────────────
argv = [
    *(["--fast"] if FAST else []),
    *(["--no-models"] if NO_MODELS else []),
    *(["--no-mock"] if NO_MOCK else []),
    *(["--export-mock-features"] if EXPORT_MOCK_FEATURES else []),
    *(["--sections", str(ONLY_SECTIONS)] if ONLY_SECTIONS else []),
    *(["--target", str(TARGET)] if TARGET else []),
    *(["--binarize", str(BINARIZE)] if BINARIZE else []),
    *(["--data-dir", str(DATA_DIR)] if DATA_DIR else []),
    *(["--mock-dir", str(MOCK_DIR)] if MOCK_DIR else []),
    "--out-dir", str(OUT_DIR),
    "--n-jobs", str(N_JOBS),
    *EXTRA_ARGS,
]  # fmt: skip
if not DATA_DIR and not DEFAULT_DATA_DIR.is_dir():
    raise SystemExit(f"{DEFAULT_DATA_DIR} not found: open the notebook from the project folder or set DATA_DIR")

# ↓ the first half of EDA.py's main(), unchanged ↓
@@HEAD@@
# ↑ the first half of EDA.py's main(), unchanged ↑

_threadpool = @@POOL@@  # keeps BLAS / OpenMP polite; stays active for the whole session
try:  # EDA.py drew to files only; give the notebook's own plots their inline backend back
    get_ipython().run_line_magic("matplotlib", "inline")
except NameError:
    pass
"""

RUNTIME = '''\
# ── Notebook runtime (not part of EDA.py): run(n) executes one section and shows its report below the cell ──
import contextlib

from IPython.display import HTML, Image, Markdown, clear_output, display


def _scoped_css(css: str, scope: str = ".eda") -> str:
    """EDA.py's HTML_CSS styles a whole web page (body, nav, dark mode ...).  Keep the component rules and scope
    them to one wrapper, so the notebook's own look is left alone."""
    css = re.sub(r"@media[^{]*\\{(?:[^{}]*\\{[^{}]*\\})*[^{}]*\\}", "", css)
    page_level = {"html", "body", "nav", "main", "footer", "header h1", ".sub", ".chips", ".chip"}
    rules = []
    for selectors, body in re.findall(r"([^{}]+)\\{([^{}]*)\\}", css):
        keep = []
        for sel in (s.strip() for s in selectors.split(",")):
            if sel == ":root":
                keep.append(scope)
            elif sel == "*":
                keep.append(f"{scope} *")
            elif sel not in page_level and not sel.startswith("nav "):
                keep.append(f"{scope} {sel}")
        if keep:
            rules.append(f"{','.join(keep)}{{{body}}}")
    return "".join(rules)


_EDA_CSS = _scoped_css(HTML_CSS) + (
    ".eda{background:var(--bg);color:var(--ink);font:14.5px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
    "border-radius:10px;padding:2px 16px 10px;margin:4px 0}"
)


def _show(blocks: list, footer: str = "") -> None:
    """Show report blocks as notebook outputs: charts as real images, everything else as styled HTML."""
    parts = [f"<style>{_EDA_CSS}</style>"]
    depth = 0  # inside a collapsible group everything must stay in one HTML piece

    def flush() -> None:
        if parts:
            display(HTML("<div class='eda'>" + "".join(parts) + "</div>"))
            parts.clear()

    for b in blocks:
        depth += {"group_open": 1, "group_close": -1}.get(b[0], 0)
        if b[0] == "figure" and depth == 0:
            _, title, read, takeaway, b64, png = b
            parts.append(f"<h4>{_inline(title)}</h4>")
            flush()
            display(Image(data=base64.b64decode(b64), format="png"))
            take = f"<p class='take'><b>Takeaway.</b> {_inline(takeaway)}</p>" if takeaway else ""
            parts.append(f"<p class='read'><b>How to read.</b> {_inline(read)}</p>{take}<p class='src'>figures/{png}</p>")
        else:
            parts.append(Report._block_html(b))
    parts.append(footer)
    flush()


# ↓ the body of the section loop in EDA.py's main(), unchanged except that `continue` became `return` ↓
def _run_section(@@LOOP_PARAMS@@) -> None:
@@LOOP_BODY@@
# ↑ the body of the section loop in EDA.py's main() ↑


def run(num: int, *, log: bool | None = None) -> None:
    """Run section `num` and show its report below this cell.  Running it again replaces the earlier result."""
    if "ctx" not in globals():
        raise RuntimeError("Run the 'Start' cell first: it reads the data and builds `ctx`.")
    entry = next((e for e in reversed(SECTIONS) if e[0] == num), None)
    if entry is None:
        raise KeyError(f"section {num} is not defined: run the cell that defines it first")
    n_sec, n_find, n_fail = len(rep.sections), len(rep.findings), len(rep.failures)
    print(f"⏳ section {num} · {entry[1]} ...", flush=True)
    quiet = not (LIVE_LOG if log is None else log)
    with contextlib.redirect_stdout(io.StringIO()) if quiet else contextlib.nullcontext():
        _run_section(*entry)
    plt.close("all")
    if len(rep.sections) == n_sec:
        print(f"section {num} skipped: it is not in ONLY_SECTIONS, or there is no mock data (see the output of Start)")
        return
    new = rep.sections[n_sec:]
    rep.sections[:] = sorted(
        [s for s in rep.sections[:n_sec] if s["num"] != num] + new, key=lambda s: s["num"]
    )  # the earlier result of this section is replaced, the order stays 1, 2, 3 ...
    rep.findings[:] = [f for f in rep.findings[:n_find] if f.sec_id != f"s{num}"] + rep.findings[n_find:]
    rep.failures[:] = [f for f in rep.failures[:n_fail] if not f.startswith(f"{num}. ")] + rep.failures[n_fail:]
    clear_output(wait=True)
    _show(new[-1]["blocks"], f"<p class='src'>⏱ {new[-1]['secs']:.1f}s</p>")
'''

FINISH = """\
# ── Finish: executive summary + the same hand-off files as `python EDA.py` ─────────────────────────────
# ↓ the second half of EDA.py's main(), unchanged ↓
def write_report() -> int:
@@TAIL@@
# ↑ the second half of EDA.py's main() ↑


t0 = time.perf_counter() - sum(s["secs"] for s in rep.sections)  # "generated in ...s" counts compute time, not idle time
exit_code = write_report()
summary_md = out_dir / "summary.md"
if summary_md.exists():
    display(Markdown(summary_md.read_text(encoding="utf-8")))
print(f"files: {out_dir}")
"""

EXPLORE = """\
# Everything the sections used is still in memory:
#   ctx.df          the pooled loan table          ctx.y / ctx.target     the 0/1 target and its name
#   ctx.features    the feature names              ctx.roles / ctx.kinds  column -> role / kind
#   ctx.store       results handed between sections (try: list(ctx.store))
#   rep.findings    every finding, with severity and the section it came from
# For your own plots use plt as usual (the inline backend is on).
ctx.df.head()
"""


# ══════════════════════════════════════════════════════════════════════════════════
# Reading EDA.py
# ══════════════════════════════════════════════════════════════════════════════════
@dataclass
class Chunk:
    title: str  # first line of the banner
    notes: list[str]  # the rest of the banner comment
    first: int  # first code line (0-based, inclusive) ...
    last: int  # ... and one past the last
    nodes: list[ast.stmt] = field(default_factory=list)


def start_line(node: ast.stmt) -> int:
    """1-based first line of a statement, decorators included."""
    return min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])


def registration(node: ast.stmt) -> tuple[int, str, str] | None:
    for dec in getattr(node, "decorator_list", []):
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "register":
            if len(dec.args) != 3 or dec.keywords:
                raise GenerationError("@register(...) is expected to take (num, title, question) positionally")
            num, title, question = (ast.literal_eval(a) for a in dec.args)
            return int(num), str(title), str(question)
    return None


def find_chunks(lines: list[str], tree: ast.Module) -> tuple[int, list[Chunk]]:
    """Banner comments -> chunks.  Returns the line where the first banner starts and the chunks."""
    banners: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if RULE.match(lines[i]):
            j = i + 1
            while j < len(lines) and lines[j].startswith("#") and not RULE.match(lines[j]):
                j += 1
            if j >= len(lines) or not RULE.match(lines[j]):
                raise GenerationError(f"EDA.py line {i + 1}: a banner rule without a closing rule")
            banners.append((i, j))
            i = j + 1
        else:
            i += 1
    if not banners:
        raise GenerationError("no banner comments (# ═══ ... ═══) found in EDA.py: nothing to cut the file at")
    chunks = []
    for k, (a, b) in enumerate(banners):
        text = [re.sub(r"^#\s?", "", ln).rstrip() for ln in lines[a + 1 : b]]
        last = banners[k + 1][0] if k + 1 < len(banners) else len(lines)
        chunk = Chunk(text[0].strip(), [t for t in text[1:] if t.strip()], b + 1, last)
        chunk.nodes = [n for n in tree.body if chunk.first <= start_line(n) - 1 < chunk.last]
        chunks.append(chunk)
    return banners[0][0], chunks


def is_main_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


class ContinueToReturn(ast.NodeTransformer):
    """The section loop's `continue` becomes `return` once the loop body is a function.  Nested loops are left alone."""

    def visit_Continue(self, node: ast.Continue) -> ast.Return:  # noqa: N802
        return ast.copy_location(ast.Return(value=None), node)

    def visit_For(self, node: ast.For) -> ast.For:  # noqa: N802
        return node

    visit_While = visit_For  # noqa: N815


@dataclass
class MainParts:
    head: str  # statements before the section loop, dedented to module level
    pool: str  # the context manager around the loop, e.g. threadpool_limits(...)
    loop_params: str  # "num, title, question, fn"
    loop_body: str  # the loop body as a function body (indented 4 spaces)
    tail: str  # statements after the loop, as a function body (indented 4 spaces)


def split_main(lines: list[str], main: ast.FunctionDef) -> MainParts:
    body = [s for s in main.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    pools = [
        s
        for s in body
        if isinstance(s, ast.With)
        and any(
            isinstance(it.context_expr, ast.Call) and getattr(it.context_expr.func, "id", "") == "threadpool_limits"
            for it in s.items
        )
    ]
    if len(pools) != 1:
        raise GenerationError(
            "main() no longer has exactly one `with threadpool_limits(...)` block around the section loop"
        )
    pool = pools[0]
    loops = [s for s in pool.body if isinstance(s, ast.For)]
    if len(loops) != 1 or not isinstance(loops[0].target, ast.Tuple) or len(loops[0].target.elts) != 4:
        raise GenerationError(
            "main(): expected one `for num, title, question, fn in sorted(SECTIONS ...)` loop in that block"
        )
    loop = loops[0]
    head_lines = lines[start_line(body[0]) - 1 : pool.lineno - 1]
    while head_lines and head_lines[-1].lstrip().startswith("#"):  # a comment that belongs to the `with` we replace
        head_lines.pop()
    head = textwrap.dedent("\n".join(head_lines)).strip("\n")
    params = ", ".join(e.id for e in loop.target.elts if isinstance(e, ast.Name))
    new_body = [ContinueToReturn().visit(s) for s in loop.body]
    loop_body = textwrap.indent(ast.unparse(ast.Module(body=new_body, type_ignores=[])), "    ")
    tail = "\n".join(lines[pool.end_lineno : main.end_lineno]).strip("\n")
    if "return" not in tail:
        raise GenerationError("main(): the part after the section loop no longer returns an exit code")
    return MainParts(head, ast.unparse(pool.items[0].context_expr), params, loop_body, tail)


# ══════════════════════════════════════════════════════════════════════════════════
# Building the notebook
# ══════════════════════════════════════════════════════════════════════════════════
@dataclass
class Cell:
    kind: str  # md | params | imports | def | rt | start | run | finish | explore
    cid: str
    source: str
    hidden: bool = False


@dataclass
class Piece:
    """A top-level statement plus the comment / blank lines just above it, so a comment stays with what it describes."""

    node: ast.stmt
    a: int  # first line (0-based) ...
    b: int  # ... and one past the last
    registered: tuple[int, str, str] | None  # (num, title, question) when it is a @register'ed section function


def pieces(chunk: Chunk, removed: set[int]) -> list[Piece]:
    nodes = [n for n in chunk.nodes if start_line(n) - 1 not in removed]
    return [
        Piece(
            n,
            chunk.first if i == 0 else nodes[i - 1].end_lineno,  # a statement's lines begin where the previous ends
            chunk.last if i == len(nodes) - 1 else n.end_lineno,
            registration(n),
        )
        for i, n in enumerate(nodes)
    ]


def code_cells(lines: list[str], pcs: list[Piece], removed: set[int]) -> list[str]:
    """Group consecutive pieces into code cells of about MAX_CELL_LINES (a single longer piece is a cell of its own)."""
    groups: list[list[Piece]] = []
    size = 0
    for p in pcs:
        if not groups or size + (p.b - p.a) > MAX_CELL_LINES:
            groups.append([])
            size = 0
        groups[-1].append(p)
        size += p.b - p.a
    cells = []
    for g in groups:
        text = "\n".join(lines[k] for p in g for k in range(p.a, p.b) if k not in removed).strip("\n")
        if any(ln.strip() and not ln.lstrip().startswith("#") for ln in text.splitlines()):
            cells.append(text)
    return cells


def check_section_references(sections: list[Piece]) -> None:
    """Helpers are all defined before Start, so only the @register functions themselves are order-sensitive: a section
    must not call a section function that the notebook defines further down."""
    for i, p in enumerate(sections):
        later = {q.node.name for q in sections[i + 1 :] if isinstance(q.node, ast.FunctionDef)}
        used = {n.id for n in ast.walk(p.node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        if used & later:
            raise GenerationError(
                f"section {p.registered[0]} uses {sorted(used & later)}, defined in a later section cell"
            )


def fill(template: str, **values: str) -> str:
    out = template
    for key, val in values.items():
        out = out.replace(f"@@{key}@@", val)
    left = re.findall(r"@@\w+@@", out)
    if left:
        raise GenerationError(f"unfilled template slot(s): {left}")
    return out.strip("\n")


def quote(notes: list[str]) -> str:
    return "".join(f"\n> {t.strip()}" for t in notes)


def build_cells(src: str) -> list[Cell]:
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        raise GenerationError(f"EDA.py does not parse right now ({exc}); regenerate once the editing is done") from exc
    first_banner, chunks = find_chunks(lines, tree)

    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    if len(mains) != 1:
        raise GenerationError("expected exactly one top-level main() in EDA.py")
    parts = split_main(lines, mains[0])
    removed: set[int] = set()
    for n in [mains[0], *(g for g in tree.body if is_main_guard(g))]:
        removed.update(range(start_line(n) - 1, n.end_lineno))

    # Every top-level statement that is not a section function (helpers, constants, classes) goes into the support
    # code, in file order: the script has defined all of them before it runs anything, and an earlier section may call
    # a helper that sits under a later section's banner (section 9 calls section 10's `model_settings`).  The section
    # functions themselves stay in the analysis part, each with its run() cell.
    support: list[tuple[str, list[Piece], bool]] = []  # (markdown heading, pieces, keep expanded)
    sections: list[Piece] = []
    dividers: dict[int, str] = {}  # id(first section piece after a PART banner) -> markdown
    pending = ""
    for ch in chunks:
        pcs = pieces(ch, removed)
        regs = [i for i, p in enumerate(pcs) if p.registered]
        if not regs:
            support.append((f"### {ch.title}\n{quote(ch.notes)}".rstrip(), pcs, ch.title.startswith("0.")))
            if ch.title.startswith("PART"):
                pending = f"# {ch.title}\n{quote(ch.notes)}".rstrip()
            continue
        names = "; ".join(f"{p.registered[0]} · {p.registered[1]}" for p in pcs if p.registered)
        before = [p for p in pcs[: regs[-1]] if not p.registered]
        after = pcs[regs[-1] + 1 :]
        if before:
            support.append((f"### Helpers of section {names}", before, False))
        if after:
            defined = ", ".join(f"`{x}`" for p in after for x in sorted(bound_names([p.node])))
            support.append(
                (f"### Support code after section {pcs[regs[-1]].registered[0]}\n\nDefines {defined}", after, False)
            )
        for i in regs:
            if pending:
                dividers[id(pcs[i])] = pending
                pending = ""
            sections.append(pcs[i])
    check_section_references(sections)

    # preamble: the module docstring is dropped (the notebook has its own intro), the imports become one code cell
    doc_node = tree.body[0]
    if not (isinstance(doc_node, ast.Expr) and isinstance(doc_node.value, ast.Constant)):
        raise GenerationError("EDA.py should start with its module docstring")
    imports = "\n".join(lines[doc_node.end_lineno : first_banner]).strip("\n")

    cells = [
        Cell("md", "eda-intro", INTRO),
        Cell("md", "eda-params-md", "## Parameters"),
        Cell("params", "eda-params", PARAMS.strip("\n")),
        Cell(
            "md",
            "eda-support-md",
            "## Support code\n\nImports, configuration, helpers (also the ones that belong to a single section: a section "
            "may call a helper of a later one), the report engine and the data loading, unchanged from `EDA.py`. "
            "Run these cells once; they only define things.",
        ),
        Cell("imports", "eda-imports", imports, hidden=True),
    ]

    root_hits = 0
    for n, (head, pcs, keep_open) in enumerate(support):
        cells.append(Cell("md", f"eda-sup{n:02d}-md", head))
        for k, text in enumerate(code_cells(lines, pcs, removed)):
            root_hits += text.count(ROOT_LINE)
            text = text.replace(ROOT_LINE, ROOT_REPLACEMENT)
            hidden = not keep_open and text.count("\n") >= HIDE_ABOVE_LINES
            cells.append(Cell("def", f"eda-sup{n:02d}-{k}", text, hidden=hidden))
    if root_hits != 1:
        raise GenerationError(f"expected exactly one line `{ROOT_LINE}` in EDA.py, found {root_hits}")

    cells += [
        Cell("rt", "eda-runtime", fill(RUNTIME, LOOP_PARAMS=parts.loop_params, LOOP_BODY=parts.loop_body), hidden=True),
        Cell(
            "md",
            "eda-start-md",
            "## Start\n\nReads the data, builds `ctx` and opens the mock bank data. Run it again to start from scratch "
            "(it also clears `OUT_DIR`).",
        ),
        Cell("start", "eda-start", fill(START, HEAD=parts.head, POOL=parts.pool)),
        Cell(
            "md",
            "eda-analysis-md",
            "## Analysis\n\nOne block per section: the heading, the code, then `run(n)`, whose result appears below it.",
        ),
    ]

    for p in sections:
        num, title, question = p.registered
        if id(p) in dividers:  # the Part II divider: its support code was placed above
            slug = re.sub(r"\W+", "-", dividers[id(p)].splitlines()[0].lower()).strip("-")[:24]
            cells.append(Cell("md", f"eda-{slug}-md", dividers[id(p)]))
        cells.append(Cell("md", f"eda-s{num:02d}-md", f"## Section {num} · {title}\n\n*❓ {question}*"))
        (text,) = code_cells(lines, [p], removed)
        cells.append(Cell("def", f"eda-s{num:02d}-0", text, hidden=text.count("\n") >= HIDE_ABOVE_LINES))
        cells.append(Cell("run", f"eda-s{num:02d}-run", f"run({num})"))

    cells += [
        Cell(
            "md",
            "eda-finish-md",
            "## Finish\n\nThe executive summary and the hand-off files, exactly as `python EDA.py` writes them.",
        ),
        Cell("finish", "eda-finish", fill(FINISH, TAIL=parts.tail)),
        Cell("md", "eda-explore-md", "## Your turn\n\nThe whole analysis is still in memory."),
        Cell("explore", "eda-explore", EXPLORE.strip("\n")),
    ]
    return cells


def digest(text: str) -> str:
    norm = "\n".join(ln.rstrip() for ln in text.strip().splitlines())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def as_notebook(cells: list[Cell], src: str) -> dict:
    def lines_of(text: str) -> list[str]:
        return text.splitlines(keepends=True)

    nb_cells = []
    for c in cells:
        meta: dict = {"eda": {"kind": c.kind, "sha": digest(c.source)}}
        if c.hidden:
            meta["jupyter"] = {"source_hidden": True}
        base = {"id": c.cid, "metadata": meta, "source": lines_of(c.source)}
        if c.kind == "md":
            nb_cells.append({"cell_type": "markdown", **base})
        else:
            nb_cells.append({"cell_type": "code", "execution_count": None, "outputs": [], **base})
    return {
        "cells": nb_cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "eda": {"generated_by": "tools/make_notebook.py", "source": "EDA.py", "source_sha": digest(src)},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def dumps(nb: dict) -> str:
    return json.dumps(nb, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


# ══════════════════════════════════════════════════════════════════════════════════
# Checks
# ══════════════════════════════════════════════════════════════════════════════════
def compile_cells(cells: list[Cell]) -> None:
    for c in cells:
        if c.kind != "md":
            try:
                ast.parse(c.source)
            except SyntaxError as exc:
                raise GenerationError(f"generated cell {c.cid} is not valid Python: {exc}") from exc


class Bound(ast.NodeVisitor):
    """Names bound at module level (function / class bodies, lambdas and comprehensions are other scopes)."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:  # noqa: N802
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_ClassDef = visit_FunctionDef  # noqa: N815

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.names.update((a.asname or a.name).split(".")[0] for a in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.names.update(a.asname or a.name for a in node.names)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        pass

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = visit_Lambda  # noqa: N815


def bound_names(nodes: list[ast.stmt]) -> set[str]:
    visitor = Bound()
    for n in nodes:
        visitor.visit(n)
    return visitor.names


def check_shadowing(cells: list[Cell], src: str) -> None:
    """The notebook's own cells (parameters, Start, runtime, Finish) must not bind a name that EDA.py binds at module
    level: whichever cell runs second would silently overwrite the other.  (A `SECTIONS` parameter once did exactly
    that to EDA.py's section registry and quietly disabled the section filter.)"""
    tree = ast.parse(src)
    eda = bound_names(
        [n for n in tree.body if not (isinstance(n, ast.FunctionDef) and n.name == "main") and not is_main_guard(n)]
    )
    for c in cells:
        if c.kind in ("params", "start", "rt", "finish", "explore"):
            clash = bound_names(ast.parse(c.source).body) & eda
            if clash:
                raise GenerationError(
                    f"notebook cell {c.cid} binds {sorted(clash)}, which EDA.py also defines at module level"
                )


def smoke(cells: list[Cell]) -> None:
    """Execute the imports and every definition cell in order, as the notebook will (no data is read).  Catches
    definition-order mistakes (a name used at definition time that a later chunk defines) and a missing run() cell."""
    import __future__

    import types

    # a real module registered in sys.modules, like __main__ in a kernel (@dataclass looks its module up there)
    module = sys.modules["eda_notebook_smoke"] = types.ModuleType("eda_notebook_smoke")
    ns = module.__dict__
    flags = __future__.annotations.compiler_flag  # IPython keeps `from __future__` flags alive across cells
    try:
        for c in cells:
            if c.kind in ("imports", "def"):
                exec(compile(c.source, c.cid, "exec", flags=flags, dont_inherit=True), ns)  # noqa: S102
    finally:
        sys.modules.pop("eda_notebook_smoke", None)
    registered = {n for n, *_ in ns["SECTIONS"]}
    with_run = {int(m.group(1)) for c in cells if c.kind == "run" and (m := re.fullmatch(r"run\((\d+)\)", c.source))}
    if registered != with_run:
        raise GenerationError(
            f"sections {sorted(registered ^ with_run)} have a run() cell or a @register, but not both"
        )
    for name in (
        "Report",
        "HTML_CSS",
        "_inline",
        "parse_args",
        "thread_limit",
        "build_story",
        "build_context",
        "open_mock",
    ):
        if name not in ns:
            raise GenerationError(f"the notebook needs `{name}` from EDA.py, which is gone")
    if not hasattr(ns["Report"], "_block_html"):
        raise GenerationError("the notebook runtime needs Report._block_html, which is gone")
    n_defs = sum(c.kind in ("imports", "def") for c in cells)
    print(f"smoke: {n_defs} definition cells executed, sections {sorted(registered)}")


def user_edited(existing: dict) -> list[str]:
    """Cells of an existing notebook whose source differs from what the generator wrote (the parameters may be edited)."""
    edited = []
    for c in existing.get("cells", []):
        meta = c.get("metadata", {}).get("eda", {})
        if meta.get("sha") and meta.get("kind") != "params" and digest("".join(c.get("source", []))) != meta["sha"]:
            edited.append(c.get("id", "?"))
    return edited


def comparable(nb: dict) -> list:
    def sha(c: dict) -> str:
        return "" if c.get("metadata", {}).get("eda", {}).get("kind") == "params" else digest("".join(c["source"]))

    return [(c.get("id"), c["cell_type"], sha(c)) for c in nb["cells"]]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate EDA.ipynb from EDA.py.")
    p.add_argument("--src", default=str(ROOT / "EDA.py"), help="the script to convert [default: EDA.py]")
    p.add_argument("--out", default=None, help="notebook to write [default: EDA.ipynb next to --src]")
    p.add_argument("--check", action="store_true", help="exit 1 when the notebook is out of date; writes nothing")
    p.add_argument(
        "--smoke", action="store_true", help="also execute the definition cells once (needs the dependencies)"
    )
    p.add_argument("--plan", action="store_true", help="print the cell list and exit")
    p.add_argument("--force", action="store_true", help="overwrite even if cells of the existing notebook were edited")
    args = p.parse_args(argv)

    src_path = Path(args.src).resolve()
    out_path = Path(args.out).resolve() if args.out else src_path.with_suffix(".ipynb")
    try:
        src = src_path.read_text(encoding="utf-8")
        cells = build_cells(src)
        compile_cells(cells)
        check_shadowing(cells, src)
        if args.smoke:
            smoke(cells)
    except GenerationError as exc:
        print(f"make_notebook: {exc}", file=sys.stderr)
        return 2
    nb = as_notebook(cells, src)

    if args.plan:
        for i, c in enumerate(cells):
            first = c.source.strip().splitlines()[0][:70] if c.source.strip() else ""
            print(f"{i:3d} {c.kind:8s} {c.cid:16s} {c.source.count(chr(10)) + 1:5d} lines  {first}")
        return 0

    existing = None
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
    if args.check:
        if existing is not None and comparable(existing) == comparable(nb):
            print(f"{out_path.name} is up to date with {src_path.name}")
            return 0
        print(f"{out_path.name} is out of date: run `uv run python tools/make_notebook.py`", file=sys.stderr)
        return 1
    if out_path.exists() and not args.force:
        if existing is None:
            print(f"make_notebook: {out_path.name} is not a readable notebook; use --force or --out", file=sys.stderr)
            return 3
        edited = user_edited(existing)
        if edited:
            print(
                f"make_notebook: not overwriting {out_path.name}: cells edited by hand: {', '.join(edited[:8])}"
                f"{' ...' if len(edited) > 8 else ''}\n  use --force to overwrite, or --out other.ipynb for a copy",
                file=sys.stderr,
            )
            return 3
    out_path.write_text(dumps(nb), encoding="utf-8")
    n_code = sum(c.kind != "md" for c in cells)
    print(f"wrote {out_path} ({len(cells)} cells, {n_code} code) from {src_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
