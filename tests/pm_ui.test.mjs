import assert from 'node:assert/strict';
import test from 'node:test';

import { applyUnifiedDiff, capabilityAvailability, chooseAvailableChange, directHistoryChangeId, moveOperations, parseTsv, pmText, serializeWbsTsv, withProjectIdentity } from '../src/symphony/web/static/pm/index.js';
import { createPmApi } from '../src/symphony/web/static/pm/api.js';
import { diagramSummary, diagramWindows } from '../src/symphony/web/static/pm/diagrams.js';
import { exchangeControlState, validateWorkbookFilename } from '../src/symphony/web/static/pm/exchange.js';
import { sourceEditPolicy } from '../src/symphony/web/static/pm/documents.js';
import { storyboardPatchOperation } from '../src/symphony/web/static/pm/storyboard.js';
import { planningActionRequest, verificationActions } from '../src/symphony/web/static/pm/assistance.js';
import { analyzeSafeDiagram } from '../scripts/pm_diagram_entry.mjs';

test('quoted TSV preserves tabs, quotes, and multiline values', () => {
  assert.deepEqual(parseTsv('"T-1"\t"notes"\t"first\nsecond\t""quoted"""\n'), [
    ['T-1', 'notes', 'first\nsecond\t"quoted"'],
  ]);
});

test('copied WBS uses the same task-field-value grammar accepted by paste', () => {
  const rows = parseTsv(serializeWbsTsv([{ task_id: 'T-1', parent_id: null, order: 10, kind: 'Feature', title: 'A', acceptance: ['One'], verification: [], dependencies: [], requirement_refs: [], storyboard_refs: [], design_refs: [] }]));
  assert.deepEqual(rows[0], ['Task ID', 'Field', 'Value']);
  assert.ok(rows.some((row) => row[0] === 'T-1' && row[1] === 'title' && row[2] === 'A'));
  assert.ok(rows.some((row) => row[0] === 'T-1' && row[1] === 'acceptance' && row[2] === '["One"]'));
});

test('unified proposal applies only when context matches', () => {
  const patch = '@@ -1,2 +1,2 @@\n one\n-two\n+three';
  assert.equal(applyUnifiedDiff('one\ntwo', patch), 'one\nthree');
  assert.throws(() => applyUnifiedDiff('changed\ntwo', patch), /no longer matches/);
});

test('move undo restores both original sibling orders', () => {
  const tasks = [{ task_id: 'T-1', parent_id: null, order: 10 }, { task_id: 'T-2', parent_id: null, order: 20 }];
  const move = moveOperations(tasks, 'T-2', 'up');
  assert.deepEqual(move.operations.map((item) => [item.task_id, item.value]), [['T-2', 10], ['T-1', 20]]);
  assert.deepEqual(move.inverse.map((item) => [item.task_id, item.value]), [['T-2', 20], ['T-1', 10]]);
});

test('semantic creation uses the authenticated document mutation envelope', async () => {
  let captured;
  const api = createPmApi({ request: async (path, options) => { captured = { path, options }; return { impacted_ids: ['SB-12345678'] }; } }, 'p1');
  const result = await api.createSemantic('change1', 'revision1', 'ux', 'storyboard', 'Checkout');
  assert.equal(result.impacted_ids[0], 'SB-12345678');
  assert.equal(captured.path, '/changes/change1/documents/ux');
  assert.equal(captured.options.method, 'PATCH');
  assert.deepEqual(captured.options.body.payload.operations, [{ op: 'create_semantic', kind: 'storyboard', title: 'Checkout' }]);
  assert.equal(captured.options.body.project_id, 'p1');
  assert.equal(captured.options.body.expected_revision, 'revision1');
});

test('refresh drops an archived selection and chooses only a remaining change', () => {
  const changes = [{ change_id: 'remaining' }];
  assert.equal(chooseAvailableChange(changes, 'archived', 'archived').change_id, 'remaining');
  assert.equal(chooseAvailableChange([], 'archived', 'archived'), null);
});

test('an original archived deep link is loaded directly outside the active list', () => {
  const changes = [{ change_id: 'remaining' }];
  assert.equal(directHistoryChangeId(changes, 'archived'), 'archived');
  assert.equal(directHistoryChangeId(changes, 'remaining'), '');
  assert.equal(directHistoryChangeId(changes, ''), '');
});

test('capability availability distinguishes requested and effective actions', () => {
  const payload = {
    flags: { authoring: true }, effective_flags: { authoring: false },
    capabilities: { authoring: { requested: true, effective: false, reasons: ['Requires G0 and G1'], development_verification: false } },
  };
  assert.deepEqual(capabilityAvailability(payload, 'authoring'), {
    requested: true, effective: false, reasons: ['Requires G0 and G1'], developmentVerification: false,
  });
  assert.deepEqual(capabilityAvailability({ flags: { exchange: true } }, 'exchange'), {
    requested: true, effective: true, reasons: [], developmentVerification: false,
  });
});

test('missing workbook dependencies leave readable exports available', () => {
  const unavailable = { effective: false };
  assert.deepEqual(exchangeControlState('xlsx', unavailable), {
    formatDisabled: false, workbookDisabled: true, exportDisabled: true,
  });
  for (const format of ['csv', 'markdown', 'html', 'zip']) {
    assert.equal(exchangeControlState(format, unavailable).exportDisabled, false, format);
  }
  assert.deepEqual(exchangeControlState('markdown', { effective: true }, true), {
    formatDisabled: false, workbookDisabled: true, exportDisabled: false,
  });
  assert.equal(exchangeControlState('xlsx', { effective: true }, true).exportDisabled, false);
});

test('workbook filenames preserve an exact plain XLSX basename', async () => {
  assert.equal(validateWorkbookFilename('Planning Review.XLSX'), 'Planning Review.XLSX');
  for (const filename of ['', '.xlsx', ' review.xlsx', 'review.xlsx ', 'folder/review.xlsx', 'folder\\review.xlsx', 'review.xlsm', 'review.xlsx\0ignored']) {
    assert.throws(() => validateWorkbookFilename(filename), /XLSX|plain filename/, filename);
  }

  const calls = [];
  const api = createPmApi({ request: async (path, options = {}) => { calls.push({ path, options }); return {}; } }, 'project-a');
  await api.importPreview('change-a', 'rev-a', 'Planning Review.XLSX', 'eHl6');
  assert.equal(calls[0].options.body.payload.filename, 'Planning Review.XLSX');
  assert.throws(() => api.importPreview('change-a', 'rev-a', '../review.xlsx', 'eHl6'), /plain filename/);
  assert.equal(calls.length, 1);
});

test('Korean PM text covers dynamic import conflicts and approval', () => {
  assert.equal(pmText('{count} proposed changes', { count: 3 }, 'ko'), '제안된 변경 3개');
  assert.equal(pmText('{count} conflicts require resolution', { count: 1 }, 'ko'), '해결이 필요한 충돌 1개');
  assert.equal(pmText('Resolve {key}', { key: 'TASK-1:title' }, 'ko'), 'TASK-1:title 해결');
  assert.equal(pmText('Apply reviewed import', {}, 'ko'), '검토한 가져오기 적용');
  assert.equal(pmText('Approve this exact planning revision locally?', {}, 'ko'), '이 정확한 기획 리비전을 로컬에서 승인하시겠습니까?');
});

test('diagram preflight accepts supported syntax before parser analysis', () => {
  const flow = diagramSummary('flowchart LR\n A[Long human readable label] --> B[Another label]');
  assert.equal(flow.allowed, true);
  assert.equal(flow.text, 'Diagram preview');
  const erd = diagramSummary('erDiagram\n USER ||--o{ ORDER : places\n USER {\n string display_name\n }');
  assert.equal(erd.allowed, true);
  assert.equal(erd.text, 'Diagram preview');
});

test('Mermaid parsed model enforces dotted chains and parallel edge caps', async () => {
  const dotted = `flowchart LR;${Array.from({ length: 101 }, (_, index) => `N${index}-->N${index + 1}`).join(';')}`;
  await assert.rejects(() => analyzeSafeDiagram(dotted), /102 nodes, 101 edges/);
  const parallel = `flowchart LR;${Array.from({ length: 20 }, (_, source) => Array.from({ length: 11 }, (_, target) => `S${source}-->T${target}`).join(';')).join(';')}`;
  await assert.rejects(() => analyzeSafeDiagram(parallel), /31 nodes, 220 edges/);
  assert.deepEqual(await analyzeSafeDiagram('flowchart LR;A-->B;B-->C'), { nodes: 3, edges: 2 });
});

test('storyboard field edits use the stable server identity and separate transitions', () => {
  const operation = storyboardPatchOperation({ storyboard_id: 'SB-12345678', legacy_combined: false }, {
    title: 'Review', screen_route: '/review', actor_goal: 'Review invitation', initial_state: 'Pending',
    ui: 'Card', user_action: 'Approve', system_response: 'Persist', success: 'Approved', alternate: 'Already approved', failure: 'Denied',
    requirement_refs: ['REQ-12345678'], design_refs: ['API-12345678'],
  });
  assert.equal(operation.storyboard_id, 'SB-12345678');
  assert.deepEqual(operation.fields.success, 'Approved');
  assert.deepEqual(operation.fields.alternate, 'Already approved');
  assert.deepEqual(operation.fields.failure, 'Denied');
});

test('legacy storyboard conversion refuses partial transition replacement', () => {
  assert.throws(() => storyboardPatchOperation({ storyboard_id: 'SB-12345678', legacy_combined: true }, { ui: 'Card' }), /all three/);
  const operation = storyboardPatchOperation({ storyboard_id: 'SB-12345678', legacy_combined: true }, {
    ui: 'Card', user_action: 'Approve', system_response: 'Persist',
    success: 'Approved', alternate: 'Already approved', failure: 'Denied',
  });
  assert.equal(operation.fields.system_response, 'Persist');
});

test('all seven assistance actions retain server-scoped selections', () => {
  const actionIds = ['draft_artifact', 'refine_section', 'find_gaps', 'review_consistency', 'explain_dependency', 'propose_wbs', 'update_impacted_artifacts'];
  for (const action of actionIds) {
    const request = planningActionRequest(action, { documentId: action === 'propose_wbs' ? 'tasks' : 'ux', sectionId: 'ux:states', dependency: { task_id: 'T-2', predecessor_id: 'T-1' }, impactedDocumentIds: ['ux', 'design'], instruction: 'Bounded request' });
    assert.equal(request.action, action);
    assert.ok(Array.isArray(request.impacted_document_ids));
  }
});

test('assistance scope validation rejects incomplete or stale hidden selections', () => {
  assert.throws(() => planningActionRequest('refine_section', { documentId: 'ux' }), /section/);
  assert.throws(() => planningActionRequest('explain_dependency', { documentId: 'tasks' }), /dependency/);
  assert.throws(() => planningActionRequest('review_consistency', { documentId: 'ux', impactedDocumentIds: ['ux'] }), /at least two/);
  assert.throws(() => planningActionRequest('update_impacted_artifacts', { documentId: 'ux' }), /at least one/);
  assert.deepEqual(planningActionRequest('draft_artifact', { documentId: 'ux', sectionId: 'stale', dependency: { task_id: 'T-2', predecessor_id: 'T-1' }, impactedDocumentIds: ['design'] }), {
    action: 'draft_artifact', document_id: 'ux', section_id: null, dependency: null, instruction: '', impacted_document_ids: ['ux'],
  });
});

test('zero-run evidence exposes native and manual actions without cross-kind fields', () => {
  const actions = verificationActions([
    { procedure_id: 'focused', kind: 'native', definition_digest: 'sha256:n' },
    { procedure_id: 'manual-ux', kind: 'manual', definition_digest: 'sha256:m' },
  ]);
  assert.deepEqual(actions.map((item) => [item.kind, item.label]), [
    ['native', 'Run verification'], ['manual', 'Submit completion evidence'],
  ]);
  assert.equal(actions[0].acceptsReportedResult, false);
  assert.equal(actions[1].acceptsReportedResult, true);
});

test('source editor refuses newline dialects a textarea would normalize', () => {
  assert.deepEqual(sourceEditPolicy('one\ntwo\n'), { editable: true, reason: '' });
  assert.equal(sourceEditPolicy('one\r\ntwo\r\n').editable, false);
  assert.equal(sourceEditPolicy('one\r\ntwo\n').editable, false);
});

test('PM API uses typed storyboard, assistance, evidence, coverage, and waiver contracts', async () => {
  const calls = [];
  const api = createPmApi({ request: async (path, options = {}) => { calls.push({ path, options }); return { ok: true }; } }, 'project-a');
  await api.patchStoryboard('change-a', 'rev-a', { op: 'set_storyboard_fields', storyboard_id: 'SB-1', fields: { title: 'A' } });
  api.startPlanning('change-a', 'rev-a', planningActionRequest('find_gaps', { documentId: 'ux', impactedDocumentIds: ['ux'] }));
  await api.evidencePreview('change-a', 'rev-a', { ticket_identifier: 'PM-1', procedure_id: 'manual-ux' });
  await api.coverage('change-a', 'rev-a');
  await api.waiverPreview('change-a', 'rev-a', 'SC-1', 'Reason', 'Reviewer');
  await api.importPreview('change-a', 'rev-a', 'review.xlsx', 'eHl6');
  assert.equal(calls[0].options.body.payload.operations[0].storyboard_id, 'SB-1');
  assert.equal(calls[1].options.body.action, 'find_gaps');
  assert.equal(calls[2].options.body.project_id, 'project-a');
  assert.match(calls[3].path, /revision=rev-a/);
  assert.equal(calls[4].options.body.semantic_id, 'SC-1');
  assert.deepEqual(calls[5].options.body.payload, { filename: 'review.xlsx', base64: 'eHl6' });
});

test('absolute PM evidence URLs retain one API prefix and stable project identity', () => {
  assert.equal(withProjectIdentity('/api/v1/pm/evidence/r1/report', 'project-a'), '/api/v1/pm/evidence/r1/report?project_id=project-a');
  assert.equal(withProjectIdentity('/api/v1/pm/evidence/r1/report?project_id=project-a', 'project-a'), '/api/v1/pm/evidence/r1/report?project_id=project-a');
});

test('an explicit pmView deep link wins over the remembered subview once', async () => {
  const store = new Map();
  globalThis.sessionStorage = { getItem: (key) => (store.has(key) ? store.get(key) : null), setItem: (key, value) => store.set(key, String(value)) };
  globalThis.history = { replaceState: () => {} };
  globalThis.location = { href: 'http://127.0.0.1/?pmChange=c1&pmTab=design&pmView=architecture#/artifacts' };
  store.set('symphony.pm.p1.c1.design', 'storyboard');
  const { createPmState } = await import('../src/symphony/web/static/pm/state.js');
  const state = createPmState(() => {}, 'p1');
  state.change = { change_id: 'c1' };
  state.restoreViewState();
  assert.equal(state.designView, 'architecture');
  assert.equal(store.get('symphony.pm.p1.c1.design'), 'architecture');
  state.setDesignView('erd');
  state.restoreViewState();
  assert.equal(state.designView, 'erd');
});

test('over-limit diagrams expand on demand in parser-checked parts of at most 100 nodes', async () => {
  const chain = (nodes) => 'flowchart LR\n' + Array.from({ length: nodes - 1 }, (_, i) => `N${i}-->N${i + 1}`).join('\n');
  await assert.rejects(analyzeSafeDiagram(chain(101)), /101 nodes/);
  for (const [nodes, parts] of [[101, 2], [250, 3]]) {
    const windows = diagramWindows(chain(nodes));
    assert.equal(windows.total, nodes);
    assert.equal(windows.count, parts);
    let covered = 0;
    for (let index = 0; index < windows.count; index += 1) {
      const part = windows.window(index);
      const model = await analyzeSafeDiagram(part.source);
      assert.ok(model.nodes <= 100 && model.edges <= 200);
      assert.equal(model.nodes, part.last - part.first + 1);
      covered += model.nodes;
    }
    assert.equal(covered, nodes);
  }
  const er = 'erDiagram\n' + Array.from({ length: 120 }, (_, i) => `E${i} ||--o{ E${i + 1} : has`).join('\n');
  const erWindows = diagramWindows(er);
  assert.equal(erWindows.count, 2);
  assert.deepEqual(await analyzeSafeDiagram(erWindows.window(0).source), { nodes: 100, edges: 99 });
});

test('diagram parts keep quoted semicolons, cross-part labels and the edge cap', async () => {
  const quoted = 'flowchart LR\nN0["Start; reviewed"]\n' + Array.from({ length: 100 }, (_, i) => `N${i}-->N${i + 1}`).join('\n');
  const quotedParts = diagramWindows(quoted);
  assert.equal(quotedParts.count, 2);
  // Labelled nodes need a browser DOM inside Mermaid; the installed browser
  // benchmark parses these exact parts. Here the split itself is checked.
  assert.match(quotedParts.window(0).source, /N0\["Start; reviewed"\]-->N1|N0\["Start; reviewed"\]/);
  assert.ok(!/N0\["Start$/m.test(quotedParts.window(0).source));

  const declared = Array.from({ length: 100 }, (_, i) => `n${i}["Node ${i}"]`).join('\n');
  const crossing = `flowchart TD\n${declared}\nn99 --> n100["Important Korean 결제 승인"]`;
  const crossingParts = diagramWindows(crossing);
  assert.equal(crossingParts.count, 2);
  const second = crossingParts.window(1);
  assert.match(second.source, /n100\["Important Korean 결제 승인"\]/);
  assert.equal(second.first, 101);

  const dense = 'flowchart LR\n' + Array.from({ length: 21 }, (_, a) => Array.from({ length: 21 }, (_, b) => (a < b ? `M${a}-->M${b}` : null)).filter(Boolean)).flat().join('\n');
  await assert.rejects(analyzeSafeDiagram(dense), /210 edges/);
  const denseParts = diagramWindows(dense);
  assert.ok(denseParts.count >= 2);
  let nodes = 0;
  for (let index = 0; index < denseParts.count; index += 1) {
    const model = await analyzeSafeDiagram(denseParts.window(index).source);
    assert.ok(model.nodes <= 100 && model.edges <= 200);
    nodes += model.nodes;
  }
  assert.equal(nodes, 21);
});

test('a redefined node label keeps its final definition inside a diagram part', () => {
  const declared = Array.from({ length: 101 }, (_, i) => `n${i}["Node ${i}"]`).join('\n');
  const parts = diagramWindows(`flowchart TD\n${declared}\nn99 --> n100["Final Korean 결제 승인"]`);
  assert.equal(parts.count, 2);
  const lines = parts.window(1).source.split('\n');
  const labels = lines.filter((line) => line.startsWith('n100['));
  assert.equal(labels.at(-1), 'n100["Final Korean 결제 승인"]');
});
