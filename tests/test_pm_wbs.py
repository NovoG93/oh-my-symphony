from __future__ import annotations

import textwrap
import re

import pytest

from symphony.pm.models import PmError, WbsDependency, WbsTask
from symphony.pm.wbs import parse_wbs, patch_task_field, patch_task_fields, render_task_block, validate_wbs


def _managed_wbs(depends: str = "[]") -> str:
    fence = "`" * 3
    return textwrap.dedent(
        f"""\
        # Implementation plan
        <!-- symphony-wbs-format: 1 -->

        <!-- symphony-group:G-ABCDEFGH -->
        ## 1. Platform
        {fence}yaml symphony-group
        id: G-ABCDEFGH
        parent_id: null
        order: 10
        {fence}
        <!-- /symphony-group -->

        <!-- symphony-task:T-ABCDEFGH -->
        - [ ] Implement API
        {fence}yaml symphony-task
        id: T-ABCDEFGH
        parent_id: G-ABCDEFGH
        order: 10
        type: Backend
        depends_on: {depends}
        requirements:
          - REQ-1
        unknown_extension: keep-me
        {fence}

        ### Acceptance criteria
        - Cross-project requests are refused.
        <!-- /symphony-task -->
        """
    )


def test_unchanged_managed_wbs_is_byte_identical_and_counts_only_leaf():
    source = _managed_wbs()
    parsed = parse_wbs(source)
    assert parsed.serialize() == source


def test_explicit_verification_policy_is_typed_and_strict() -> None:
    source = _managed_wbs().replace(
        "unknown_extension: keep-me",
        "unknown_extension: keep-me\nverification_policy:\n  version: 1\n  procedures:\n    - procedure_id: focused\n      kind: native\n    - procedure_id: manual-ux\n      kind: manual",
    )
    parsed = parse_wbs(source)
    assert not [item for item in parsed.findings if item.severity == "blocking"]
    assert [(item.procedure_id, item.kind) for item in parsed.tasks[1].verification_policy or ()] == [
        ("focused", "native"), ("manual-ux", "manual")
    ]
    invalid = parse_wbs(source.replace("kind: manual", "kind: typo"))
    assert "invalid_verification_policy" in {item.code for item in invalid.findings}
    for replacement in (
        "verification_policy: null",
        "verification_policy: []",
        "verification_policy:\n  version: true\n  procedures: []",
        "verification_policy:\n  version: '1'\n  procedures: []",
        "verification_policy:\n  version: 1\n  procedures:\n    - procedure_id: unknown\n      kind: other",
    ):
        malformed = re.sub(
            r"verification_policy:\n  version: 1\n  procedures:\n    - procedure_id: focused\n      kind: native\n    - procedure_id: manual-ux\n      kind: manual",
            replacement,
            source,
        )
        assert "invalid_verification_policy" in {item.code for item in parse_wbs(malformed).findings}
    assert [item.task_id for item in parsed.tasks] == ["G-ABCDEFGH", "T-ABCDEFGH"]
    assert [item.task_id for item in parsed.tasks if item.executable] == ["T-ABCDEFGH"]
    assert parsed.findings == ()


def test_render_task_block_supports_group_identity_and_grammar() -> None:
    source = render_task_block({
        "task_id": "G-12345678", "kind": "Group", "title": "Platform",
        "parent_id": None, "order": 10,
    })
    parsed = parse_wbs(f"<!-- symphony-wbs-format: 1 -->\n{source}\n")
    assert parsed.findings == ()
    assert len(parsed.tasks) == 1
    assert parsed.tasks[0].task_id == "G-12345678"
    assert parsed.tasks[0].executable is False


def test_group_title_and_order_are_structured_editable() -> None:
    source = _managed_wbs()
    updated = patch_task_fields(source, [
        ("G-ABCDEFGH", "title", "Renamed group"),
        ("G-ABCDEFGH", "order", 20),
        ("T-ABCDEFGH", "order", 10),
    ])
    tasks = {item.task_id: item for item in parse_wbs(updated).tasks}
    assert tasks["G-ABCDEFGH"].title == "Renamed group"
    assert tasks["G-ABCDEFGH"].order == 20
    assert tasks["T-ABCDEFGH"].order == 10


def test_single_field_patch_preserves_unrelated_source_and_unknown_metadata():
    source = _managed_wbs()
    updated = patch_task_field(source, "T-ABCDEFGH", "priority", "high")
    assert "priority: high" in updated
    assert "unknown_extension: keep-me" in updated
    assert "Cross-project requests are refused." in updated
    assert parse_wbs(updated).findings == ()


def test_batch_field_patch_preserves_source_and_request_order():
    source = _managed_wbs() + "\n<!-- unrelated-tail -->\n"
    updated = patch_task_fields(source, [
        ("T-ABCDEFGH", "notes", "first"),
        ("T-ABCDEFGH", "notes", "second"),
        ("T-ABCDEFGH", "priority", "high"),
    ])
    assert "### Notes\nsecond" in updated
    assert "priority: high" in updated
    assert "unknown_extension: keep-me" in updated
    assert updated.endswith("<!-- unrelated-tail -->\n")
    assert parse_wbs(updated).findings == ()


def test_projection_retains_task_detail_sections():
    source = _managed_wbs().replace(
        "### Acceptance criteria",
        "### Scope\nBounded API work.\n\n### Acceptance criteria",
    ).replace(
        "<!-- /symphony-task -->",
        "### Verification\n- Run contract tests.\n\n### Integration impact\nDialog consumes API.\n\n### Risk\nAuthorization drift.\n\n### Notes\nReview with security.\n<!-- /symphony-task -->",
    )
    task = next(item for item in parse_wbs(source).tasks if item.executable)
    assert task.description == "Bounded API work."
    assert task.acceptance == ("Cross-project requests are refused.",)
    assert task.verification == ("Run contract tests.",)
    assert task.integration_impact == "Dialog consumes API."
    assert task.risk == "Authorization drift."
    assert task.notes == "Review with security."


def test_checkbox_inside_fence_and_dangling_dependency_are_blocking():
    fence = "`" * 3
    source = _managed_wbs("[T-MISSING1]") + f"\n{fence}text\n- [ ] fake\n{fence}\n"
    parsed = parse_wbs(source)
    codes = {item.code for item in parsed.findings}
    assert "dangling_dependency" in codes
    assert "unsafe_checkbox" in codes
    with pytest.raises(PmError, match="not safe"):
        patch_task_field(source, "T-ABCDEFGH", "title", "Changed")


def test_checkbox_reasons_classify_each_position_between_task_lines():
    fence = "`" * 3
    first = _managed_wbs()
    second = first.split("<!-- symphony-task:T-ABCDEFGH -->", 1)[1].replace(
        "T-ABCDEFGH", "T-BCDEFGHI"
    )
    source = (
        first
        + "\n- [ ] stray before second\n"
        + f"{fence}text\n- [x] fenced between\n{fence}\n"
        + "<!-- symphony-task:T-BCDEFGHI -->"
        + second
        + "\n- [ ] stray after last\n"
    )
    parsed = parse_wbs(source)
    assert {"T-ABCDEFGH", "T-BCDEFGHI"} <= {task.task_id for task in parsed.tasks}
    unsafe = [
        item.message for item in parsed.findings if item.code == "unsafe_checkbox"
    ]
    assert unsafe == [
        "checkbox outside executable task marker",
        "checkbox inside fenced code",
        "checkbox outside executable task marker",
    ]


def test_title_edit_preserves_a_crlf_title_line_in_a_mixed_newline_document():
    source = _managed_wbs().replace("- [ ] Implement API\n", "- [ ] Implement API\r\n", 1)
    assert source.count("\r\n") == 1
    assert not [item for item in parse_wbs(source).findings if item.severity == "blocking"]
    updated = patch_task_field(source, "T-ABCDEFGH", "title", "Implement gateway")
    assert "- [ ] Implement gateway\r\n" in updated
    assert updated.count("\r\n") == 1
    assert updated == source.replace("Implement API\r\n", "Implement gateway\r\n", 1)
    assert {task.task_id: task.title for task in parse_wbs(updated).tasks}["T-ABCDEFGH"] == "Implement gateway"


def test_malformed_metadata_shapes_are_blocking_not_defaulted():
    source = _managed_wbs().replace("order: 10", "order: not-an-integer", 1)
    parsed = parse_wbs(source)
    assert "invalid_metadata_type" in {item.code for item in parsed.findings}


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda value: value.replace("order: 10", "order: 10\norder: 20", 1), "malformed_metadata"),
        (lambda value: value.replace("depends_on: []", "depends_on: T-ABCDEFGH"), "invalid_metadata_type"),
        (lambda value: value.replace("depends_on: []", "depends_on:\n  - task_id: T-ZZZZZZZZ\n    kind: mystery"), "invalid_metadata_type"),
        (lambda value: value.replace("<!-- /symphony-task -->", ""), "unbalanced_marker"),
        (lambda value: value + "\n<!-- symphony-widget:W-ABCDEFGH -->\n", "unknown_marker_kind"),
    ],
)
def test_ambiguous_or_malformed_managed_wbs_is_rejected(mutation, expected):
    parsed = parse_wbs(mutation(_managed_wbs()))
    assert expected in {item.code for item in parsed.findings}


def test_managed_markers_inside_fence_are_rejected():
    fence = "`" * 3
    source = _managed_wbs() + f"\n{fence}text\n<!-- symphony-task:T-ZZZZZZZZ -->\n{fence}\n"
    assert "managed_marker_in_fence" in {item.code for item in parse_wbs(source).findings}


def test_field_patch_preserves_following_comment():
    source = _managed_wbs().replace(
        "order: 10\ntype:", "order: 10\n# retain this comment\ntype:", 1
    )
    updated = patch_task_field(source, "T-ABCDEFGH", "order", 20)
    assert "# retain this comment" in updated


def test_list_field_patch_replaces_old_items_and_preserves_comment():
    source = _managed_wbs().replace(
        "requirements:\n  - REQ-1",
        "requirements:\n  # retain requirement note\n  - REQ-1",
    )
    updated = patch_task_field(source, "T-ABCDEFGH", "requirements", ["REQ-2"])
    assert "# retain requirement note" in updated
    assert "REQ-2" in updated
    assert "REQ-1" not in updated
    assert parse_wbs(updated).findings == ()


def test_nested_managed_blocks_are_rejected():
    source = _managed_wbs().replace(
        "## 1. Platform",
        "## 1. Platform\n<!-- symphony-task:T-ZZZZZZZZ -->\n- [ ] nested\n<!-- /symphony-task -->",
    )
    assert "nested_managed_block" in {item.code for item in parse_wbs(source).findings}


def test_multiline_acceptance_and_verification_items_are_retained():
    source = _managed_wbs().replace(
        "- Cross-project requests are refused.",
        "- Cross-project requests are refused.\n  No data changes are committed.",
    ).replace(
        "<!-- /symphony-task -->",
        "### Verification\n- Run contract tests.\n  Record the tested commit.\n<!-- /symphony-task -->",
    )
    task = next(item for item in parse_wbs(source).tasks if item.executable)
    assert task.acceptance == (
        "Cross-project requests are refused.\nNo data changes are committed.",
    )
    assert task.verification == ("Run contract tests.\nRecord the tested commit.",)


def test_deep_dependency_chain_is_validated_iteratively():
    tasks = tuple(
        WbsTask(
            task_id=f"T-{index:08d}",
            parent_id=None,
            order=index,
            kind="Feature",
            title=str(index),
            dependencies=(WbsDependency(f"T-{index - 1:08d}", "requires_code"),) if index else (),
        )
        for index in range(1500)
    )
    assert validate_wbs(tasks) == ()
