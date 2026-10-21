"""Tests for tools/make_notebook.py (EDA.py -> EDA.ipynb).

They read the real EDA.py (nothing here touches ./Data) and write only into pytest's tmp_path.  Whether the committed
EDA.ipynb is up to date is not a test on purpose - that is `make notebook-check`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_generator():
    spec = importlib.util.spec_from_file_location("make_notebook", ROOT / "tools" / "make_notebook.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_notebook"] = module
    spec.loader.exec_module(module)
    return module


mn = _load_generator()
SRC = (ROOT / "EDA.py").read_text(encoding="utf-8")


@pytest.fixture
def cells():
    return mn.build_cells(SRC)  # a fresh list per test: some tests mutate it


def test_cells_have_unique_ids_and_every_registered_section_has_exactly_one_run_cell(cells):
    ids = [c.cid for c in cells]
    assert len(ids) == len(set(ids))
    runs = [c.source for c in cells if c.kind == "run"]
    assert len(runs) == len(set(runs)) == SRC.count("\n@register(")
    # support code comes before Start, the sections after it, and the summary after the last section
    kinds = [c.kind for c in cells]
    assert kinds.index("rt") < kinds.index("start") < kinds.index("run")
    assert kinds.index("finish") > max(i for i, k in enumerate(kinds) if k == "run")


def test_only_section_functions_are_defined_after_start(cells):
    # section 9 calls `model_settings`, a helper that EDA.py defines under section 10's banner: every helper must
    # therefore exist before the first run(n), as it does in the script
    kinds = [c.kind for c in cells]
    after = [c for c in cells[kinds.index("start") :] if c.kind == "def"]
    assert len(after) == SRC.count("\n@register(")
    assert all("@register(" in c.source for c in after)
    before = "".join(c.source for c in cells[: kinds.index("start")] if c.kind == "def")
    assert "def model_settings(" in before


def test_a_section_that_calls_a_later_section_function_is_refused():
    anchor = "def section_overview(ctx: Ctx) -> None:\n"
    assert SRC.count(anchor) == 1
    with pytest.raises(mn.GenerationError, match="section_quality"):
        mn.build_cells(SRC.replace(anchor, anchor + "    section_quality(ctx)\n"))


def test_generated_python_is_valid_and_binds_no_name_that_eda_py_also_defines(cells):
    mn.compile_cells(cells)
    mn.check_shadowing(cells, SRC)


def test_definition_cells_execute_in_notebook_order_without_reading_data(cells):
    mn.smoke(cells)  # raises when a name is used at definition time before the cell that defines it


def test_main_loop_and_tail_are_lifted_from_eda_py_not_retyped(cells):
    by_id = {c.cid: c.source for c in cells}
    assert "wanted is not None and num not in wanted" in by_id["eda-runtime"]  # the loop body of main()
    run_section = by_id["eda-runtime"].split("def _run_section")[1].split("def run(")[0]
    assert "continue" not in run_section  # a loop-body `continue` is a `return` inside the function
    assert "rep.clear_outputs()" in by_id["eda-start"]  # the head of main()
    assert "rep.render_html(header)" in by_id["eda-finish"]  # the tail of main()
    code = "".join(c.source for c in cells if c.kind in ("def", "imports"))
    assert "__main__" not in code
    assert "__file__" not in code


def test_a_parameter_named_like_a_module_global_is_refused(cells):
    # the bug this guards against: a `SECTIONS = "1,2"` parameter was overwritten by EDA.py's SECTIONS registry
    for c in cells:
        if c.kind == "params":
            c.source = c.source.replace("ONLY_SECTIONS", "SECTIONS")
    with pytest.raises(mn.GenerationError, match="SECTIONS"):
        mn.check_shadowing(cells, SRC)


def test_hand_edited_cells_are_protected_but_parameters_may_change(tmp_path, capsys):
    out = tmp_path / "EDA.ipynb"
    assert mn.main(["--out", str(out)]) == 0
    assert mn.main(["--out", str(out), "--check"]) == 0

    nb = json.loads(out.read_text(encoding="utf-8"))
    params = next(c for c in nb["cells"] if c["metadata"]["eda"]["kind"] == "params")
    params["source"] = [ln.replace("FAST = False", "FAST = True") for ln in params["source"]]
    out.write_text(json.dumps(nb), encoding="utf-8")
    assert mn.main(["--out", str(out)]) == 0  # the parameters cell is meant to be edited

    nb = json.loads(out.read_text(encoding="utf-8"))
    next(c for c in nb["cells"] if c["id"] == "eda-s04-run")["source"] = ["run(4)  # my experiment"]
    out.write_text(json.dumps(nb), encoding="utf-8")
    capsys.readouterr()
    assert mn.main(["--out", str(out)]) == 3  # refuses to overwrite
    assert "eda-s04-run" in capsys.readouterr().err
    assert "my experiment" in out.read_text(encoding="utf-8")  # the edit is still there
    assert mn.main(["--out", str(out), "--check"]) == 1
    assert mn.main(["--out", str(out), "--force"]) == 0
    assert mn.main(["--out", str(out), "--check"]) == 0


def test_a_script_that_no_longer_matches_the_expected_layout_fails_loudly(tmp_path, capsys):
    pool = "with threadpool_limits(limits=thread_limit(args.n_jobs)):"
    assert SRC.count(pool) == 1  # the anchor this test breaks; if EDA.py renames it, update the test and the generator
    broken = tmp_path / "EDA.py"
    broken.write_text(SRC.replace(pool, "if True:"), encoding="utf-8")
    assert mn.main(["--src", str(broken), "--out", str(tmp_path / "x.ipynb")]) == 2
    assert "threadpool_limits" in capsys.readouterr().err
    assert not (tmp_path / "x.ipynb").exists()  # nothing half-written
