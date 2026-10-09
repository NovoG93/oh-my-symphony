from __future__ import annotations

import io
import re
import zipfile

import openpyxl
import pytest

from symphony.pm.exchange import MAX_UNCOMPRESSED, WorkbookExchange
from symphony.pm.models import ChangeView, DocumentView, PmError
from symphony.pm.routes import _render_export


def _rewrite_member(data: bytes, name: str, transform) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            payload = source.read(info)
            target.writestr(info, transform(payload) if info.filename == name else payload)
    return output.getvalue()


def _change(title: str = "Implement API", revision: str = "sha256:base") -> ChangeView:
    fence = "`" * 3
    source = f"""# Plan
<!-- symphony-wbs-format: 1 -->
<!-- symphony-group:G-ABCDEFGH -->
## 1. Group
{fence}yaml symphony-group
id: G-ABCDEFGH
parent_id: null
order: 10
{fence}
<!-- /symphony-group -->
<!-- symphony-task:T-ABCDEFGH -->
- [ ] {title}
{fence}yaml symphony-task
id: T-ABCDEFGH
parent_id: G-ABCDEFGH
order: 10
type: Backend
depends_on:
  - T-ZZZZZZZZ
requirements:
  - REQ-1
{fence}
### Scope
Base description.
### Acceptance criteria
- Base acceptance.
### Verification
- Base verification.
### Integration impact
Base integration.
### Risk
Base risk.
### Notes
Base notes.
<!-- /symphony-task -->
<!-- symphony-task:T-ZZZZZZZZ -->
- [ ] Foundation
{fence}yaml symphony-task
id: T-ZZZZZZZZ
parent_id: G-ABCDEFGH
order: 20
type: Backend
depends_on: []
{fence}
### Scope
Foundation scope.
### Acceptance criteria
- Foundation works.
### Verification
- Run foundation tests.
### Integration impact
None.
<!-- /symphony-task -->
"""
    return ChangeView(
        project_id="p1",
        change_id="c1",
        locator="demo",
        revision=revision,
        scope_hash="sha256:scope",
        state="Draft",
        base_git_sha="a" * 40,
        schema_hash="schema",
        documents=(
            DocumentView("tasks", "tasks.md", source, "hash", "draft", True),
        ),
    )


def test_six_sheet_export_forces_formula_prefix_to_literal_and_round_trips() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change("=SUM(1,1)"), {})
    workbook = openpyxl.load_workbook(
        io.BytesIO(payload.data), read_only=True, data_only=False, keep_links=False
    )
    try:
        assert tuple(workbook.sheetnames) == (
            "Read Me",
            "WBS",
            "Task Details",
            "Dependencies",
            "Traceability",
            "Metadata",
        )
        title = workbook["WBS"]["F3"]
        assert title.value == "=SUM(1,1)"
        assert title.data_type == "s"
    finally:
        workbook.close()

    candidate = exchange.preview_import(payload.data, _change(), _change())
    assert candidate.base_revision == "sha256:base"
    assert candidate.filename == "workbook.xlsx"
    assert candidate.conflicts == ()

    named = exchange.preview_import(
        payload.data, _change(), _change(), filename="reviewed.XLSX"
    )
    assert named.filename == "reviewed.XLSX"
    assert named.preview_digest != candidate.preview_digest


def test_export_id_is_embedded_for_durable_trusted_base_lookup() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change(), {}, export_id="a" * 32)
    metadata = exchange.inspect_metadata(payload.data)
    assert metadata["export_id"] == "a" * 32
    assert metadata["revision"] == "sha256:base"


def test_nondefault_instance_limits_are_enforced() -> None:
    payload = WorkbookExchange().export(_change(), {})
    with pytest.raises(PmError, match="compressed workbook limit"):
        WorkbookExchange(max_workbook_bytes=len(payload.data) - 1).inspect_metadata(payload.data)
    with pytest.raises(PmError, match="uncompressed-size limit"):
        WorkbookExchange(max_uncompressed_bytes=1).inspect_metadata(payload.data)
    with pytest.raises(PmError, match="ZIP entry limit"):
        WorkbookExchange(max_zip_entries=1).inspect_metadata(payload.data)
    with pytest.raises(PmError) as exc:
        WorkbookExchange(max_nodes=2).export(_change(), {})
    assert exc.value.details["findings"] == ["node_limit_exceeded"]
    with pytest.raises(PmError) as exc:
        WorkbookExchange(max_edges=0).export(_change(), {})
    assert "edge_limit_exceeded" in exc.value.details["findings"]
    with pytest.raises(PmError) as exc:
        WorkbookExchange(max_text_chars=3).export(_change(), {})
    assert "text_limit_exceeded" in exc.value.details["findings"]


def test_print_html_is_readable_and_escapes_active_markup() -> None:
    change = _change("Readable")
    document = change.documents[0]
    source = "# Heading\n\nA paragraph.\n\n- One\n- Two\n\n<script>alert(1)</script>\n"
    change = ChangeView(**{**change.__dict__, "documents": (DocumentView(document.document_id, document.path, source, document.content_hash, document.state, document.editable),)})
    exported = _render_export(change, "html")
    text = exported.data.decode()
    assert "<h1>Heading</h1>" in text
    assert "<ul><li>One</li><li>Two</li></ul>" in text
    assert "<script>" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_three_way_conflict_is_explicit() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change("base"), {})
    workbook = openpyxl.load_workbook(io.BytesIO(payload.data))
    workbook["WBS"]["F3"] = "imported"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    candidate = exchange.preview_import(
        output.getvalue(), _change("base"), _change("current", "sha256:current")
    )
    assert [(item.item_id, item.field) for item in candidate.conflicts] == [
        ("T-ABCDEFGH", "title")
    ]


def test_formula_external_relationship_and_zip_bomb_are_rejected() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change(), {})
    workbook = openpyxl.load_workbook(io.BytesIO(payload.data))
    workbook["WBS"]["A2"] = "=1+1"
    formula = io.BytesIO()
    workbook.save(formula)
    workbook.close()
    with pytest.raises(PmError, match="formula cell"):
        exchange.preview_import(formula.getvalue(), _change(), _change())

    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"x" * (MAX_UNCOMPRESSED + 1))
    with pytest.raises(PmError, match="uncompressed-size"):
        exchange.preview_import(bomb.getvalue(), _change(), _change())


@pytest.mark.parametrize(
    ("member", "replacement"),
    [
        ("_rels/.rels", b"<Relationships><broken>"),
        ("xl/worksheets/sheet2.xml", b"<worksheet><broken>"),
        ("_rels/.rels", b'<!DOCTYPE x [<!ENTITY y "z">]><Relationships/>'),
    ],
)
def test_malformed_or_entity_xml_is_a_stable_unsafe_workbook(
    member: str, replacement: bytes,
) -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change(), {})
    hostile = _rewrite_member(payload.data, member, lambda _value: replacement)
    with pytest.raises(PmError) as exc_info:
        exchange.preview_import(hostile, _change(), _change())
    assert exc_info.value.code == "unsafe_workbook"


def test_duplicate_task_hidden_beyond_declared_dimension_is_rejected() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change(), {})
    workbook = openpyxl.load_workbook(io.BytesIO(payload.data))
    sheet = workbook["WBS"]
    sheet.append([cell.value for cell in sheet[2]])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    def hide_last_row(xml: bytes) -> bytes:
        return re.sub(br'<dimension ref="[^"]+"', b'<dimension ref="A1:L3"', xml)

    hostile = _rewrite_member(
        output.getvalue(), "xl/worksheets/sheet2.xml", hide_last_row
    )
    with pytest.raises(PmError, match="duplicate task ID"):
        exchange.preview_import(hostile, _change(), _change())


def test_csv_neutralizes_formula_prefix_and_preserves_unicode() -> None:
    data = WorkbookExchange().render_csv(_change("@위험"), excel_utf8=True)
    text = data.decode("utf-8-sig")
    assert "'@위험" in text


def test_import_covers_task_details_and_new_task_fields_with_temp_references() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change(), {})
    workbook = openpyxl.load_workbook(io.BytesIO(payload.data))
    workbook["Task Details"]["B3"] = "Imported description"
    workbook["Task Details"]["C3"] = "One\nTwo"
    workbook["WBS"].append(("Add", "NEW-1", "G-ABCDEFGH", 20, "Backend", "New task", "Owner", "codex", "high", "2026-10-10", "2d", ""))
    workbook["Task Details"].append(("NEW-1", "New description", "Accepted", "Run tests", "API", "Low", "Note"))
    workbook["Dependencies"].append(("NEW-1", "T-ABCDEFGH", "requires_code", ""))
    workbook["Traceability"].append(("NEW-1", "requirement", "REQ-2", ""))
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    candidate = exchange.preview_import(output.getvalue(), _change(), _change())
    assert {(change.item_id, change.field) for change in candidate.changes} >= {
        ("T-ABCDEFGH", "description"),
        ("T-ABCDEFGH", "acceptance"),
    }
    assigned = candidate.assigned_ids["NEW-1"]
    addition = next(change for change in candidate.changes if change.action == "add")
    assert addition.item_id == assigned
    assert addition.imported["description"] == "New description"
    assert addition.imported["dependencies"][0].task_id == "T-ABCDEFGH"
    assert addition.imported["requirement_refs"] == ("REQ-2",)


def test_optional_sheet_omission_retains_content_and_version_is_strict() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change(), {})
    workbook = openpyxl.load_workbook(io.BytesIO(payload.data))
    workbook.remove(workbook["Dependencies"])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    candidate = exchange.preview_import(output.getvalue(), _change(), _change())
    assert not any(change.field == "dependencies" for change in candidate.changes)

    workbook = openpyxl.load_workbook(io.BytesIO(payload.data))
    workbook["Metadata"]["A2"] = "999"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    with pytest.raises(PmError, match="format_version"):
        exchange.preview_import(output.getvalue(), _change(), _change())


def test_missing_dependency_and_trace_rows_retain_until_explicit_delete() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change(), {})
    workbook = openpyxl.load_workbook(io.BytesIO(payload.data))
    workbook["Dependencies"].delete_rows(2)
    workbook["Traceability"].delete_rows(2)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    candidate = exchange.preview_import(output.getvalue(), _change(), _change())
    assert not any(change.field in {"dependencies", "requirement_refs"} for change in candidate.changes)

    workbook = openpyxl.load_workbook(io.BytesIO(payload.data))
    workbook["Dependencies"]["E2"] = "Delete"
    workbook["Traceability"]["E2"] = "Delete"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    candidate = exchange.preview_import(output.getvalue(), _change(), _change())
    fields = {change.field: change.imported for change in candidate.changes if change.item_id == "T-ABCDEFGH"}
    assert fields["dependencies"] == ()
    assert fields["requirement_refs"] == ()


def test_delete_conflicts_when_affected_task_changed_since_export() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change("base"), {})
    workbook = openpyxl.load_workbook(io.BytesIO(payload.data))
    workbook["WBS"]["A3"] = "Delete"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    candidate = exchange.preview_import(
        output.getvalue(), _change("base"), _change("current", "sha256:current")
    )
    assert [(item.item_id, item.field) for item in candidate.conflicts] == [
        ("T-ABCDEFGH", "__delete__")
    ]


def test_declared_full_grid_dimension_is_rejected_before_iteration() -> None:
    exchange = WorkbookExchange()
    payload = exchange.export(_change(), {})
    source = io.BytesIO(payload.data)
    output = io.BytesIO()
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(output, "w") as outgoing:
        for info in incoming.infolist():
            data = incoming.read(info)
            if info.filename == "xl/worksheets/sheet2.xml":
                data = re.sub(b'ref="A1:[A-Z]+[0-9]+"', b'ref="A1:XFD1048576"', data, count=1)
            outgoing.writestr(info, data)
    with pytest.raises(PmError, match="dimensions exceed"):
        exchange.preview_import(output.getvalue(), _change(), _change())
