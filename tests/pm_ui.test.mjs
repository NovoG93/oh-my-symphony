import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile, readdir } from 'node:fs/promises';
import vm from 'node:vm';

import { applyUnifiedDiff, capabilityAvailability, chooseAvailableChange, directHistoryChangeId, mountArtifacts, moveOperations, parseTsv, pmText, serializeWbsTsv, withProjectIdentity } from '../src/symphony/web/static/pm/index.js';
import { createPmApi } from '../src/symphony/web/static/pm/api.js';
import { diagramSummary, diagramWindows } from '../src/symphony/web/static/pm/diagrams.js';
import { exchangeControlState, validateWorkbookFilename } from '../src/symphony/web/static/pm/exchange.js';
import { sourceEditPolicy } from '../src/symphony/web/static/pm/documents.js';
import { storyboardPatchOperation } from '../src/symphony/web/static/pm/storyboard.js';
import { createPmOverlay } from '../src/symphony/web/static/pm/overlay.js';
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

// D7 is pinned here rather than read from an unshipped planning document.
const GUIDE_LABELS = {
  workspace: ['New change', 'Repair workspace', 'Convert legacy plan', 'Edit in external editor', 'Validate', 'Approve revision'],
  idea: ['Edit source', 'Save draft', 'Planning assistant', 'Add decision'],
  requirements: ['Add capability', 'Edit requirements', 'Add requirement', 'Add scenario', 'Waive coverage', 'Revoke waiver'],
  design: ['Storyboard', 'Save storyboard', 'Add storyboard', 'Add contract'],
  plan: ['Work breakdown', 'Dependencies', 'Add group', 'Add child', 'Delete', 'Cancel task', 'Undo', 'Download export', 'Preview import', 'Preview approved handoff', 'Preview archive'],
  code: ['Run verification', 'Submit completion evidence', 'Review Git commit', 'Open run evidence', 'Accept verified revision'],
};
const GUIDE_SUMMARY = 'What do these buttons do?';
const DEMO_HEADING = 'Demo workspace';
const DEMO_SENTENCE = 'This is a disposable demo. Planning actions are unlocked only on this server; no AI worker starts and nothing leaves this computer.';
const DEMO_ASSISTANCE_REASON = 'Off in the demo: it would contact an AI model.';
const DEVELOPMENT_SENTENCE = 'Development verification is active for configured actions. Dependency checks still apply, and release gates are not marked passed.';
const NODE_REASON = 'Bundled OpenSpec needs Node.js 20.19.0 or newer on PATH (found: none)';
const CODEX_REASON = 'Codex schema probe is missing restricted app-server controls. Install a compatible Codex CLI supporting restricted app-server controls, then retry. No fallback is used. Planning assistant needs a Codex login: ~/.codex/auth.json is missing. Run codex login.';
const ARCHIVED_REASON = 'Archived planning history is read-only.';
const PM_DIRECTORY = new URL('../src/symphony/web/static/pm/', import.meta.url);

async function guideEntries() {
  // A missing future module must fail these tests, not abort the original suite.
  const { PM_GUIDES } = await import(new URL('guide.js', PM_DIRECTORY));
  assert.ok(PM_GUIDES && typeof PM_GUIDES === 'object', 'guide.js must export PM_GUIDES (area -> label -> sentence)');
  return PM_GUIDES;
}

function withoutKoMaps(source) {
  return source.replace(/\bconst\s+KO\s*=\s*(?:new\s+Map\(Object\.entries\()?\{[\s\S]*?\}(?:\)\))?\s*;/g, '');
}

function controlLabels(source) {
  const clean = withoutKoMaps(source);
  const literals = [...clean.matchAll(/(['"])((?:\\.|(?!\1)[^\\\r\n])*)\1/g)].map((match) => match[2]);
  const labels = new Set(literals);
  // Only this actual template and its declared kind list satisfy Add <kind>.
  if (clean.includes('`Add ${kind}`')) {
    const kinds = clean.match(/\bconst\s+kinds\s*=([\s\S]*?);/)?.[1] || '';
    for (const match of kinds.matchAll(/['"](decision|storyboard|contract|requirement|scenario)['"]/g)) labels.add(`Add ${match[1]}`);
  }
  return labels;
}

test('R9 guide table pins all six areas and exactly 36 D7 labels', async () => {
  const entries = await guideEntries();
  assert.deepEqual(Object.keys(entries).sort(), Object.keys(GUIDE_LABELS).sort());
  assert.equal(Object.values(entries).reduce((count, area) => count + Object.keys(area).length, 0), 36);
  for (const [area, labels] of Object.entries(GUIDE_LABELS)) {
    assert.deepEqual(Object.keys(entries[area]).sort(), [...labels].sort(), area);
    for (const sentence of Object.values(entries[area])) {
      assert.equal(typeof sentence, 'string', area);
      assert.ok(sentence.trim().length > 0 && !/<[^>]+>/.test(sentence), `${area}: a plain explanation is required`);
    }
  }
});

test('R9 guide labels exist outside guide.js and KO maps, including Add ${kind}', async () => {
  // Counterexamples prove that translation keys and arbitrary kinds cannot pass.
  assert.equal(controlLabels("const KO = new Map(Object.entries({ 'Only in KO': '번역' }));").has('Only in KO'), false);
  assert.equal(controlLabels("const KO = { 'Only in KO': '번역' };").has('Only in KO'), false);
  assert.equal(controlLabels("const kinds = ['decision']; button(`Add ${kind}`);").has('Add decision'), true);
  assert.equal(controlLabels("const kinds = ['decision']; button(`Add ${kind}`);").has('Add scenario'), false);
  assert.equal(controlLabels("const kinds = ['scenario']; button(`Other ${kind}`);").has('Add scenario'), false);
  const files = (await readdir(PM_DIRECTORY)).filter((name) => name.endsWith('.js') && name !== 'guide.js');
  const labels = new Set((await Promise.all(files.map(async (name) => [...controlLabels(await readFile(new URL(name, PM_DIRECTORY), 'utf8'))]))).flat());
  for (const label of Object.values(GUIDE_LABELS).flat()) assert.ok(labels.has(label), `D7 label has no control literal: ${label}`);
  const entries = await guideEntries();
  for (const label of Object.values(entries).flatMap(Object.keys)) assert.ok(labels.has(label), `Guide label has no control literal: ${label}`);
});

function assertKorean(strings) {
  const missing = strings.filter((value) => pmText(value, {}, 'ko') === value || !/[가-힣]/.test(pmText(value, {}, 'ko')));
  assert.deepEqual(missing, [], 'Every guide/status string must have a Korean entry in index.js');
}

test('R9 every guide summary, label and explanation has a Korean entry', async () => {
  const entries = await guideEntries();
  assertKorean([GUIDE_SUMMARY, ...Object.values(entries).flatMap((area) => Object.entries(area).flat())]);
});

test('R9 index.js translates Save storyboard, Review Git commit, Open run evidence and Cancel task', () => {
  assertKorean(['Save storyboard', 'Review Git commit', 'Open run evidence', 'Cancel task']);
});

// Small DOM adapter for the real mountArtifacts renderer. It implements only
// construction, queries and text walking; no browser library or UI exports added.
class UiNode {
  constructor(tag = '', text = '') {
    this.tag = tag; this.nodeType = tag ? 1 : 3; this.nodeValue = text;
    this.childNodes = []; this.attributes = new Map(); this.dataset = {};
    this.disabled = false; this.title = ''; this.open = false; this.listeners = new Map();
    this.classList = {
      add: (name) => this.setAttribute('class', `${this.getAttribute('class') || ''} ${name}`.trim()),
      remove: (name) => this.setAttribute('class', (this.getAttribute('class') || '').split(' ').filter((item) => item !== name).join(' ')),
    };
  }
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map((node) => node.textContent).join(''); }
  set textContent(value) { this.replaceChildren(String(value)); }
  append(...nodes) {
    for (const node of nodes) this.childNodes.push(node instanceof UiNode ? node : new UiNode('', String(node)));
    for (const child of this.childNodes) child.parentNode = this;
  }
  appendChild(node) { this.append(node); return node; }
  prepend(...nodes) { const children = this.childNodes; this.childNodes = []; this.append(...nodes); this.childNodes.push(...children); }
  remove() { if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter((node) => node !== this); this.parentNode = null; }
  get isConnected() { return this.tag === 'body' || !!this.parentNode?.isConnected; }
  contains(node) { return this === node || this.childNodes.some((child) => child.contains(node)); }
  closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
  get className() { return this.getAttribute('class') || ''; }
  set className(value) { this.setAttribute('class', value); }
  get options() { return this.querySelectorAll('option'); }
  get selectedOptions() { return this.options.filter((node) => node.selected); }
  get children() { return this.childNodes.filter((node) => node.nodeType === 1); }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  setAttribute(key, value) {
    this.attributes.set(key, String(value));
    if (['open', 'disabled', 'hidden', 'selected'].includes(key)) this[key] = true;
    if (['value', 'title'].includes(key)) this[key] = String(value);
  }
  getAttribute(key) { return this.attributes.get(key) ?? null; }
  matches(selector) {
    const match = selector.match(/^([\w-]+)?(?:\.([\w-]+))?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/);
    if (!match || this.nodeType !== 1) return false;
    const [, tag, className, attr, value] = match;
    return (!tag || this.tag === tag) && (!className || (this.getAttribute('class') || '').split(' ').includes(className))
      && (!attr || (this.attributes.has(attr) && (value === undefined || this.getAttribute(attr) === value)));
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    return this.childNodes.flatMap((node) => [...(selectors.some((item) => node.matches(item)) ? [node] : []), ...node.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener); this.listeners.set(type, listeners);
  }
  async trigger(type, extra = {}) { for (const listener of this.listeners.get(type) || []) await listener({ type, target: this, preventDefault() {}, stopPropagation() {}, ...extra }); }
  focus() { globalThis.document.activeElement = this; }
}

function storageStub() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: (key) => values.delete(key) };
}

async function mountedPm(t, { tab = 'idea', view = '', demo = false, language = 'en', hostTools = {}, gated = false, archived = false, development = false, demoAssistance = false, dirtyChanges = [], apiRequest = null, draft = null, announcements = [] } = {}) {
  const el = (tag, attrs, children) => {
    const node = new UiNode(tag);
    for (const [key, value] of Object.entries(attrs || {})) if (value !== null && value !== undefined && value !== false) node.setAttribute(key, typeof value === 'boolean' ? '' : value);
    for (const child of (Array.isArray(children) ? children : children == null ? [] : [children])) if (child != null && child !== false) node.append(child);
    if (tag === 'select') node.value = node.options.find((item) => item.selected)?.value || node.options[0]?.value || '';
    return node;
  };
  const globals = {
    window: { i18n: { lang: language } },
    location: new URL(`http://127.0.0.1/?pmTab=${tab}&pmView=${view}#/artifacts`),
    history: { replaceState() {} }, sessionStorage: storageStub(), localStorage: storageStub(), NodeFilter: { SHOW_TEXT: 4 },
    document: {
      // WBS enhancement stays disconnected; these tests exercise native controls.
      querySelector: () => null, head: { appendChild() {} }, createElement: (tag) => el(tag), body: el('body'),
      createTreeWalker: (scope) => {
        const texts = (node) => node.nodeType === 3 ? [node] : node.childNodes.flatMap(texts);
        const nodes = texts(scope); let index = 0;
        return { nextNode: () => nodes[index++] || null };
      },
    },
  };
  const previous = Object.fromEntries(Object.keys(globals).map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  const restore = () => {
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  };
  let mount;
  t.after(() => { try { mount?.unmount(); } finally { restore(); } });
  const capabilities = {
    project_id: 'p1', demo: { active: demo }, host_tools: hostTools,
    capabilities: Object.fromEntries(['authoring', 'exchange', 'assistance', 'handoff'].map((name) => [name, {
      requested: true, effective: !gated, reasons: gated ? [`${name} requires G0 and G1.`] : [], development_verification: development,
    }])),
  };
  if (demoAssistance) capabilities.capabilities.assistance = { requested: false, effective: false, reasons: [DEMO_ASSISTANCE_REASON] };
  const change = { change_id: 'c1', locator: 'team-leave-request', revision: 'rev1', state: archived ? 'Archived' : 'Draft', workspace_state: archived ? 'RELEASED' : 'ATTACHED', documents: [{ document_id: 'proposal', path: 'proposal.md', editable: true }], storyboard_frames: [] };
  const root = el('div'); document.body.append(root);
  if (draft !== null) localStorage.setItem('symphony.pm.draft.p1.c1.proposal.rev1', draft);
  mount = mountArtifacts({ root, projectId: 'p1', onDirtyChange: (dirty) => dirtyChanges.push(dirty), ui: { el, renderMarkdown: (source) => el('p', null, source), announce: (...args) => announcements.push(args) }, api: {
    request: async (path, options) => {
      const extra = await apiRequest?.(path, options); if (extra !== undefined) return extra;
      const pathname = path.split('?')[0];
      if (pathname === '/capabilities') return capabilities;
      if (pathname === '/changes') return { changes: [change] };
      if (pathname === '/changes/c1') return change;
      if (pathname.includes('/documents/')) return { document_id: pathname.split('/').at(-1), source: 'Demo document', path: 'proposal.md' };
      if (pathname.endsWith('/wbs')) return { tasks: [], findings: [] };
      if (pathname.endsWith('/managed-tickets')) return { tickets: [] };
      if (pathname.endsWith('/coverage')) return { entities: [] };
      throw new Error(`Unexpected PM request: ${path}`);
    },
  } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(root.querySelector('.pm-action-error'), null, root.textContent);
  assert.ok(root.querySelector('.pm-revision-bar'), `PM renderer must finish without an error: ${root.textContent}`);
  return root;
}

function findButton(root, label) {
  const node = root.querySelectorAll('button').find((item) => item.textContent === label);
  assert.ok(node, `Missing button: ${label}`); return node;
}

test('document review/source toggles retain language, focus, draft and input handlers', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const dirtyChanges = [];
    const root = await mountedPm(context, { language, dirtyChanges });
    const layout = root.querySelector('.pm-document-layout');
    const toggle = layout.querySelector('.pm-view-switcher').querySelector('button');
    const source = layout.querySelector('textarea');
    const sourcePane = layout.querySelector('.pm-editor-pane');
    const reviewPane = layout.querySelector('.pm-preview-pane');
    assert.equal(toggle.textContent, pmText('Edit source', {}, language));
    await toggle.trigger('click');
    assert.equal(toggle.textContent, pmText('Review document', {}, language));
    assert.equal(toggle.getAttribute('aria-pressed'), 'true');
    assert.equal(sourcePane.hidden, false);
    assert.equal(reviewPane.hidden, true);
    assert.equal(document.activeElement, source);
    source.value += '\nLocal draft';
    await source.trigger('input');
    assert.equal(dirtyChanges.at(-1), true);
    assert.equal(findButton(layout, pmText('Save draft', {}, language)).disabled, false);
    const draft = source.value;
    assert.equal(layout.querySelector('.pm-document-preview').textContent, draft);
    await toggle.trigger('click');
    assert.equal(toggle.textContent, pmText('Edit source', {}, language));
    assert.equal(toggle.getAttribute('aria-pressed'), 'false');
    assert.equal(sourcePane.hidden, true);
    assert.equal(reviewPane.hidden, false);
    await toggle.trigger('click');
    assert.equal(toggle.textContent, pmText('Review document', {}, language));
    assert.equal(layout.querySelector('textarea'), source);
    assert.equal(source.value, draft);
    assert.equal(document.activeElement, source);
  });
});

test('Design direct source replacements localize both callbacks and the nested toggle', async (t) => {
  for (const language of ['en', 'ko']) for (const view of ['storyboard', 'ux', 'api']) {
    await t.test(`${view}, ${language}`, async (context) => {
      const root = await mountedPm(context, { tab: 'design', view, language });
      const surface = root.querySelector('.pm-design-stack');
      const selector = surface.querySelector('.pm-segmented');
      await findButton(surface, pmText('Edit source', {}, language)).trigger('click');
      assert.equal(root.querySelector('.pm-action-error'), null, root.textContent);
      assert.equal(surface.querySelector('.pm-segmented'), selector);
      findButton(surface, pmText(view === 'api' ? 'Add contract' : 'Add storyboard', {}, language));
      const layout = surface.querySelector('.pm-document-layout');
      assert.ok(layout, 'The document editor is rendered after the direct replacement');
      findButton(layout, pmText('Save draft', {}, language));
      const toggle = findButton(layout, pmText('Edit source', {}, language));
      await toggle.trigger('click');
      assert.equal(toggle.textContent, pmText('Review document', {}, language));
      assert.equal(document.activeElement, layout.querySelector('textarea'));
      assert.equal(layout.querySelector('textarea').value, 'Demo document');
      await toggle.trigger('click');
      assert.equal(toggle.textContent, pmText('Edit source', {}, language));
    });
  }
});

test('R9 each area renders visible guide entries and follows demo.active in English and Korean', async (t) => {
  for (const language of ['en', 'ko']) for (const demo of [false, true]) for (const tab of ['idea', 'requirements', 'design', 'plan', 'code']) {
    await t.test(`${tab}, ${language}, demo=${demo}`, async (context) => {
      const root = await mountedPm(context, { tab, language, demo });
      const guides = root.querySelectorAll('details.pm-guide');
      assert.ok(guides.length >= 2, `${tab}: workspace and area guides must both be visible`);
      const entries = await guideEntries();
      for (const [area, labels] of [['workspace', GUIDE_LABELS.workspace], [tab, GUIDE_LABELS[tab]]]) {
        const guide = guides.find((node) => labels.every((label) => node.textContent.includes(pmText(label, {}, language))));
        assert.ok(guide, `${area}: missing visible guide labels`);
        assert.equal(guide.open, demo, `${area}: open must follow capabilities.demo.active`);
        assert.equal(guide.querySelector('summary')?.textContent, pmText(GUIDE_SUMMARY, {}, language));
        for (const label of labels) {
          const text = guide.textContent;
          const translatedLabel = pmText(label, {}, language), sentence = pmText(entries[area][label], {}, language);
          assert.ok(text.indexOf(sentence, text.indexOf(translatedLabel) + translatedLabel.length) >= 0, `${area}/${label}: sentence must follow its label`);
        }
      }
    });
  }
});

async function loadI18n({ href, stored = null, storageDisabled = false }) {
  const source = await readFile(new URL('../src/symphony/web/static/i18n.js', import.meta.url), 'utf8');
  const values = new Map(stored === null ? [] : [['symphony.lang', stored]]), writes = [], replacements = [];
  const location = new URL(href);
  const history = { state: { retained: true }, replaceState: (state, title, url) => { replacements.push(String(url)); location.href = new URL(url, location).href; } };
  const document = { documentElement: { lang: '' }, querySelectorAll: () => [] };
  const window = { location, history };
  const localStorage = {
    getItem: (key) => { if (storageDisabled) throw new Error('Storage disabled'); return values.get(key) ?? null; },
    setItem: (key, value) => { if (storageDisabled) throw new Error('Storage disabled'); writes.push([key, String(value)]); values.set(key, String(value)); },
  };
  vm.runInNewContext(source, { window, document, location, history, localStorage, URL, URLSearchParams }, { filename: 'i18n.js' });
  return { i18n: window.i18n, document, values, writes, replacements, location };
}

test('R10 ?lang=ko overrides storage, persists like the switch, and is consumed before reload', async () => {
  for (const stored of [null, 'en', 'ko']) {
    const page = await loadI18n({ href: 'http://127.0.0.1/?keep=1&lang=ko&pmTab=design#/artifacts', stored });
    assert.equal(page.i18n.lang, 'ko');
    assert.equal(page.document.documentElement.lang, 'ko');
    assert.equal(page.values.get('symphony.lang'), 'ko');
    assert.equal(page.location.searchParams.has('lang'), false);
    assert.equal(page.location.searchParams.get('keep'), '1');
    assert.equal(page.location.searchParams.get('pmTab'), 'design');
    assert.equal(page.location.hash, '#/artifacts');
    assert.ok(page.replacements.length > 0, 'Consume lang through history.replaceState');
    let changed;
    page.i18n.onChange((language) => { changed = language; });
    page.i18n.setLang('en');
    assert.equal(changed, 'en');
    assert.deepEqual(page.writes.at(-1), ['symphony.lang', 'en']);
    const reload = await loadI18n({ href: page.location.href, stored: page.values.get('symphony.lang') });
    assert.equal(reload.i18n.lang, 'en', 'Reload must preserve a later manual language choice (P3)');
  }
});

test('R10 query values are exactly en or ko; xx and ko-KR keep stored or default language', async () => {
  for (const value of ['xx', 'ko-KR', 'KO']) for (const stored of [null, 'en', 'ko']) {
    const page = await loadI18n({ href: `http://127.0.0.1/?lang=${value}#/artifacts`, stored });
    assert.equal(page.i18n.lang, stored || 'en', `${value}, stored=${stored}`);
    assert.equal(page.document.documentElement.lang, stored || 'en');
    assert.deepEqual(page.writes, [], 'An invalid URL value must not change storage');
  }
  const english = await loadI18n({ href: 'http://127.0.0.1/?lang=en#/artifacts', stored: 'ko' });
  assert.equal(english.i18n.lang, 'en');
  assert.equal(english.values.get('symphony.lang'), 'en');
  assert.equal(english.location.searchParams.has('lang'), false);
});

test('R10 URL language still applies and is consumed when localStorage is unavailable', async () => {
  const page = await loadI18n({ href: 'http://127.0.0.1/?lang=ko&keep=1#/artifacts', storageDisabled: true });
  assert.equal(page.i18n.lang, 'ko');
  assert.equal(page.document.documentElement.lang, 'ko');
  assert.equal(page.location.searchParams.has('lang'), false);
  assert.equal(page.location.searchParams.get('keep'), '1');
});

test('R15 Node titles follow P6, gate approval/archive, and leave Validate enabled', async (t) => {
  for (const gated of [false, true]) for (const node of [undefined, { available: true, reason: null }, { available: false, reason: NODE_REASON }]) {
    await t.test(`gated=${gated}, node=${node?.available ?? 'absent'}`, async (context) => {
      const root = await mountedPm(context, { tab: 'plan', gated, hostTools: node ? { node } : {} });
      const controls = [['Approve revision', 'authoring'], ['Preview archive', 'handoff'], ['Validate', null]];
      const expected = controls.map(([label, capability]) => {
        if (!capability) return { label, disabled: false, title: node?.available === false ? NODE_REASON : '' };
        const oldTitle = gated ? `${capability} requires G0 and G1.` : '';
        const title = node?.available === false ? [oldTitle, NODE_REASON].filter(Boolean).join(' ') : oldTitle;
        return { label, disabled: gated || node?.available === false, title };
      });
      const actual = controls.map(([label]) => { const button = findButton(root, label); return { label, disabled: button.disabled, title: button.title }; });
      assert.deepEqual({ controls: actual, toolReasonShown: Boolean(root.querySelector('.pm-status-card')?.textContent.includes(NODE_REASON)) },
        { controls: expected, toolReasonShown: node?.available === false });
    });
  }
  await t.test('archived controls keep only the read-only title', async (context) => {
    const root = await mountedPm(context, { tab: 'plan', archived: true, gated: true, hostTools: { node: { available: false, reason: NODE_REASON } } });
    for (const label of ['Approve revision', 'Preview archive', 'Validate']) {
      assert.equal(findButton(root, label).title, ARCHIVED_REASON, label);
      assert.equal(findButton(root, label).disabled, true, label);
    }
  });
});

test('R15 unavailable Codex folds into assistance and lists capability/login reasons up front', async (t) => {
  for (const gated of [false, true]) for (const codex of [undefined, { available: true, reason: null }, { available: false, reason: CODEX_REASON }]) {
    await t.test(`gated=${gated}, codex=${codex?.available ?? 'absent'}`, async (context) => {
      const root = await mountedPm(context, { gated, hostTools: codex ? { codex } : {} });
      const button = findButton(root, 'Planning assistant');
      assert.equal(button.disabled, gated || codex?.available === false);
      if (codex?.available === false) {
        assert.ok(button.title.includes(CODEX_REASON), button.title);
        assert.ok(root.querySelector('.pm-status-card')?.textContent.includes(CODEX_REASON), 'Status card lists the unavailable Codex reason');
        if (gated) assert.ok(button.title.includes('assistance requires G0 and G1.'));
      } else assert.equal(button.title, gated ? 'assistance requires G0 and G1.' : '');
    });
  }
});

test('R15 demo status and assistance reason render in en/ko in place of development text', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const root = await mountedPm(context, { demo: true, language, development: true, demoAssistance: true });
    const card = root.querySelector('.pm-status-card');
    assert.ok(card);
    assert.equal(card.querySelector('strong').textContent, pmText(DEMO_HEADING, {}, language));
    assert.ok(card.textContent.includes(pmText(DEMO_SENTENCE, {}, language)));
    assert.ok(card.textContent.includes(pmText(DEMO_ASSISTANCE_REASON, {}, language)));
    assert.ok(!card.textContent.includes(DEVELOPMENT_SENTENCE));
    assert.ok(!card.textContent.includes('Development verification context'));
    assertKorean([DEMO_HEADING, DEMO_SENTENCE, DEMO_ASSISTANCE_REASON]);
    const button = findButton(root, pmText('Planning assistant', {}, language));
    assert.equal(button.disabled, true);
    assert.ok(button.title.includes(DEMO_ASSISTANCE_REASON) || button.title.includes(pmText(DEMO_ASSISTANCE_REASON, {}, language)));
  });
  await t.test('demo status is visible even without unavailable actions or development flags', async (context) => {
    const root = await mountedPm(context, { demo: true });
    const card = root.querySelector('.pm-status-card');
    assert.ok(card, 'An active demo always identifies itself');
    assert.equal(card.querySelector('strong').textContent, DEMO_HEADING);
    assert.ok(card.textContent.includes(DEMO_SENTENCE));
  });
  await t.test('ordinary development status is preserved', async (context) => {
    const root = await mountedPm(context, { development: true });
    const card = root.querySelector('.pm-status-card');
    assert.equal(card.querySelector('strong').textContent, 'Development verification context');
    assert.ok(card.textContent.includes(DEVELOPMENT_SENTENCE));
    assert.ok(!card.textContent.includes(DEMO_SENTENCE));
  });
});


test('hardening recovered source is dirty against saved document and immediately savable', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const dirtyChanges = [];
    const root = await mountedPm(context, { language, draft: 'Recovered unsaved text', dirtyChanges });
    assert.equal(root.querySelector('textarea').value, 'Recovered unsaved text');
    assert.equal(findButton(root, pmText('Save draft', {}, language)).disabled, false);
    assert.equal(dirtyChanges.at(-1), true);
    root.querySelector('textarea').value = 'Demo document';
    await root.querySelector('textarea').trigger('input');
    assert.equal(findButton(root, pmText('Save draft', {}, language)).disabled, true);
    assert.equal(dirtyChanges.at(-1), false);
  });
});

test('hardening empty assistant review omits absent nodes without null text or authority changes', async (t) => {
  const calls = [];
  const root = await mountedPm(t, { apiRequest: (path, options) => {
    if (path.endsWith('/planning-actions')) { calls.push(options); return { operation_id: 'op-empty', result: { explanation: 'No changes suggested.', proposals: [], blocked_instruction: null, reserved_ids: {} } }; }
  } });
  await findButton(root, 'Planning assistant').trigger('click');
  await findButton(document.body, 'Prepare proposal').trigger('click');
  await new Promise((resolve) => setImmediate(resolve));
  const dialog = document.body.querySelector('[aria-label="Planning proposal"]');
  assert.ok(dialog, document.body.textContent);
  assert.ok(!dialog.textContent.includes('null'), dialog.textContent);
  assert.ok(!dialog.textContent.includes('undefined'), dialog.textContent);
  assert.equal(findButton(dialog, 'Apply proposal').disabled, true);
  assert.equal(calls.length, 1);
});

test('hardening Korean tab discard is localized and cancellation retains unsaved input', async (t) => {
  const root = await mountedPm(t, { language: 'ko' });
  const editor = root.querySelector('textarea'); editor.value = 'Unsaved'; await editor.trigger('input');
  let message; window.confirm = (value) => { message = value; return false; };
  await findButton(root, '설계').trigger('click');
  assert.match(message, /저장하지 않은/);
  assert.equal(root.querySelector('textarea').value, 'Unsaved');
});


test('hardening owned overlay refuses dirty replacement and restores focus and original inert values', async (t) => {
  const root = await mountedPm(t);
  const trigger = findButton(root, 'Edit source'); trigger.focus();
  const unrelated = new UiNode('section'); unrelated.inert = true; document.body.append(unrelated);
  const overlay = createPmOverlay(root);
  const panel = new UiNode('section'), close = new UiNode('button'), last = new UiNode('input'); panel.append(close, last);
  let mayClose = false;
  assert.equal(overlay.open(panel, { canClose: () => mayClose }), true);
  assert.equal(root.inert, true); assert.equal(document.activeElement, close);
  await panel.trigger('keydown', { key: 'Tab', shiftKey: true });
  assert.equal(document.activeElement, last);
  assert.equal(overlay.open(new UiNode('section')), false);
  assert.equal(overlay.panel, panel);
  assert.equal(overlay.close(), false);
  mayClose = true;
  assert.equal(overlay.close(), true);
  assert.equal(root.inert, undefined); assert.equal(unrelated.inert, true);
  assert.equal(document.activeElement, trigger);
  assert.equal(document.body.querySelector('.pm-overlay-backdrop'), null);
  overlay.open(new UiNode('section'));
  overlay.dispose();
  assert.equal(document.body.querySelector('.pm-overlay-backdrop'), null);
  assert.equal(root.inert, undefined);
  overlay.destroy();
  assert.equal(overlay.open(new UiNode('section')), false, 'Late responses cannot reopen an unmounted project overlay');
});

test('hardening delayed previews cannot open or confirm on a later PM view', async (t) => {
  for (const kind of ['handoffs', 'archive']) await t.test(kind, async (context) => {
    let complete;
    const waiting = new Promise((resolve) => { complete = resolve; });
    const root = await mountedPm(context, { tab: 'plan', apiRequest: (path) => path.endsWith(`/${kind}/preview`) ? waiting : undefined });
    const pending = findButton(root, kind === 'handoffs' ? 'Preview approved handoff' : 'Preview archive').trigger('click');
    await new Promise((resolve) => setImmediate(resolve));
    await findButton(root, 'Code Files').trigger('click');
    complete(kind === 'handoffs' ? { items: [], preview_token: 'old-view-token' } : { candidate: { target_paths: ['old-view-archive'] }, accepted_commits: {}, archive_id: 'old-view-archive', confirmation_token: 'old-token' });
    await pending;
    assert.equal(document.body.querySelector('[role="dialog"]'), null, 'An old preview must not create a dialog on the new view');
    assert.equal(root.querySelector('.pm-action-error'), null);
  });
});

test('hardening detached confirmation cannot submit after PM navigation', async (t) => {
  let writes = 0;
  const root = await mountedPm(t, { tab: 'plan', apiRequest: (path) => {
    if (path.endsWith('/handoffs/preview')) return { items: [], preview_token: 'view-token' };
    if (path.endsWith('/handoffs')) { writes += 1; return {}; }
  } });
  await findButton(root, 'Preview approved handoff').trigger('click');
  const confirm = findButton(document.body, 'Create native tickets');
  await findButton(root, 'Code Files').trigger('click');
  await confirm.trigger('click');
  assert.equal(writes, 0, 'An event queued on the detached old confirmation cannot authorize a new view');
  assert.equal(document.body.querySelector('[role="dialog"]'), null);
});

test('hardening failed source save keeps dirty input and allows an immediate retry', async (t) => {
  const dirtyChanges = [];
  const root = await mountedPm(t, { dirtyChanges, apiRequest: (path, options) => {
    if (options?.method === 'PATCH' && path.includes('/documents/')) throw new Error('Fixture save failed; retry.');
  } });
  const source = root.querySelector('textarea'); source.value += '\nRetry marker'; await source.trigger('input');
  const save = findButton(root, 'Save draft'); await save.trigger('click');
  assert.equal(source.value, 'Demo document\nRetry marker');
  assert.ok(root.querySelector('.pm-action-error').textContent.includes('Fixture save failed'));
  assert.equal(save.disabled, false, 'Failure must not masquerade as a saved clean document');
  assert.equal(dirtyChanges.at(-1), true);
});


test('round5 exact archive guidance preserves unknown failures and silent aborts', async (t) => {
  for (const language of ['en', 'ko']) for (const variant of ['exact', 'other-message', 'other-code', 'abort']) {
    await t.test(`${language}/${variant}`, async (context) => {
      const announcements = [];
      const error = Object.assign(new Error(variant === 'other-message' ? 'Another approval is required' : 'archive requires the approved revision'), {
        code: variant === 'other-code' ? 'different_code' : 'approval_required', name: variant === 'abort' ? 'AbortError' : 'Error',
      });
      const root = await mountedPm(context, { tab: 'plan', language, announcements, apiRequest: (path) => {
        if (path.includes('/archive/preview')) throw error;
      } });
      const source = findButton(root, pmText('Preview archive', {}, language));
      await source.trigger('click');
      const alert = root.querySelector('.pm-action-error');
      if (variant === 'abort') { assert.equal(alert, null); assert.deepEqual(announcements, []); return; }
      assert.equal(alert.getAttribute('role'), 'alert');
      assert.equal(document.activeElement, source);
      const expected = variant === 'exact' ? (language === 'ko'
        ? '승인된 리비전이 있어야 아카이브할 수 있습니다. 현재 리비전을 검토하고 승인한 뒤 아카이브 미리보기를 다시 여세요.'
        : 'Archive requires an approved revision. Review and approve this revision, then preview archive again.') : error.message;
      assert.ok(alert.textContent.includes(`${error.code}: ${expected}`), alert.textContent);
      assert.deepEqual(announcements, [[`${error.code}: ${expected}`, 'error']]);
      assert.equal(error.message, variant === 'other-message' ? 'Another approval is required' : 'archive requires the approved revision');
    });
  }
});


test('round5 validation guidance changes only the exact Problem finding', async (t) => {
  for (const language of ['en', 'ko']) await t.test(language, async (context) => {
    const findings = [
      { code: 'idea_field_required', field: 'problem', message: 'Idea field problem is required', severity: 'error' },
      { code: 'idea_field_required', field: 'goal', message: 'Idea field goal is required', severity: 'error' },
      { code: 'other_code', field: 'problem', message: 'Idea field problem is required', severity: 'error' },
      { code: 'idea_field_required', field: 'problem', message: 'Different problem message', severity: 'error' },
    ];
    const snapshot = JSON.stringify(findings);
    const root = await mountedPm(context, { language, apiRequest: (path) => path.endsWith('/validate') ? { valid: false, findings } : undefined });
    await findButton(root, pmText('Validate', {}, language)).trigger('click');
    const dialog = document.body.querySelector('[role="dialog"]');
    assert.ok(dialog);
    const rows = dialog.querySelectorAll('.pm-finding');
    assert.equal(rows.length, 4);
    assert.ok(rows[0].textContent.includes(language === 'ko'
      ? '문제 항목이 빠져 있습니다. 아이디어 소스에 문제 항목을 추가하고 저장한 뒤 다시 검증하세요.'
      : 'The Problem field is missing. Add it to the idea source, save, then validate again.'), rows[0].textContent);
    for (let index = 1; index < rows.length; index++) assert.ok(rows[index].textContent.includes(findings[index].message));
    for (let index = 0; index < rows.length; index++) assert.equal(rows[index].querySelector('strong').textContent, findings[index].code);
    assert.equal(JSON.stringify(findings), snapshot, 'The server findings, fields and codes remain untouched');
  });
});


test('round5 all six compact-table consumers retain rows, column headings and local wrappers', async (t) => {
  const task = { task_id: 'T-1', parent_id: null, order: 1, kind: 'Feature', title: 'A task', dependencies: [{ task_id: 'T-0', kind: 'hard', justification: 'Required first' }], requirement_refs: [], storyboard_refs: [], design_refs: [], acceptance: [], verification: [] };
  const apiRequest = (path) => {
    path = path.split('?')[0];
    if (path.endsWith('/wbs')) return { tasks: [task], findings: [] };
    if (path.endsWith('/wbs/preview')) return { findings: [] };
    if (path.endsWith('/coverage')) return { entities: [{ semantic_id: 'REQ-12345678', title: 'Coverage', task_links: [{ task_id: 'T-1', task_revision: 'revision-1', ticket_identifier: 'TICKET-1', implemented: true, tested: false, accepted: false, test_provenance: 'manual_reported' }] }] };
    if (path.endsWith('/managed-tickets')) return { tickets: [{ ticket_identifier: 'TICKET-1', task_id: 'T-1', task_revision: 'revision-1', code_files: [{ status: 'M', path: 'src/a-very-long-path.js' }] }] };
    if (path.endsWith('/planning-actions')) return { operation_id: 'op-1', result: { reserved_ids: { '$new': 'REQ-87654321' } } };
  };
  const inspect = (scope, expectedHeaders, expectedText) => {
    const table = scope.querySelectorAll('.pm-compact-table').find((node) => node.querySelectorAll('th').map((th) => th.textContent).join('|') === expectedHeaders.join('|'));
    assert.ok(table, `Missing ${expectedHeaders.join('/')}`);
    assert.equal(table.parentNode.className, 'pm-table-scroll');
    assert.ok(table.closest('.pm-workspace-host') || table.closest('.pm-overlay-backdrop'));
    assert.ok(table.querySelectorAll('th').every((th) => th.getAttribute('scope') === 'col'));
    assert.ok(table.textContent.includes(expectedText), table.textContent);
    return table;
  };
  for (const language of ['en', 'ko']) for (const consumer of ['dependencies', 'coverage', 'code', 'planning', 'paste', 'proposal']) {
    await t.test(`${language}/${consumer}`, async (context) => {
      const tab = consumer === 'proposal' ? 'idea' : ['code', 'planning', 'coverage'].includes(consumer) ? 'code' : 'plan';
      const root = await mountedPm(context, { tab, view: consumer === 'dependencies' ? 'dependencies' : '', language, apiRequest });
      const headers = (values) => values.map((value) => pmText(value, {}, language));
      if (consumer === 'dependencies') inspect(root, headers(['Task', 'Needs', 'Dependency', 'Reason']), 'Required first');
      if (consumer === 'coverage') inspect(root, headers(['Task', 'Revision', 'Ticket', 'Implemented', 'Tested', 'Accepted', 'Provenance']), 'TICKET-1');
      if (consumer === 'code') inspect(root, headers(['Status', 'Path']), 'src/a-very-long-path.js');
      if (consumer === 'planning') inspect(root, headers(['Path', 'State', 'Role']), 'proposal.md');
      if (consumer === 'paste') {
        await root.querySelector('.pm-wbs-table').trigger('paste', { clipboardData: { getData: () => 'T-1\ttitle\tChanged title' } });
        const dialog = document.body.querySelector('[role="dialog"]');
        inspect(dialog, headers(['Task', 'Field', 'Value']), 'Changed title');
        await findButton(dialog, pmText('Cancel', {}, language)).trigger('click');
        assert.equal(document.body.querySelector('[role="dialog"]'), null);
      }
      if (consumer === 'proposal') {
        await findButton(root, pmText('Planning assistant', {}, language)).trigger('click');
        const dialog = document.body.querySelector('[role="dialog"]');
        await findButton(dialog, pmText('Prepare proposal', {}, language)).trigger('click');
        const proposal = document.body.querySelector('[role="dialog"]');
        inspect(proposal, headers(['Placeholder', 'Permanent ID']), 'REQ-87654321');
        assert.ok(proposal.closest('.pm-overlay-backdrop'));
      }
    });
  }
});


test('round5 shared toast adapter keeps ordinary host semantics and sets PM roles before append', async () => {
  const source = await readFile(new URL('../src/symphony/web/static/app.js', import.meta.url), 'utf8');
  const start = source.indexOf('  function showToast(');
  const toastSource = source.slice(start, source.indexOf('\n  // ------------------------------------------------------------------', start));
  const appended = [], pending = [];
  const el = (tag, attrs, text) => {
    const node = new UiNode(tag); node.textContent = text;
    for (const [key, value] of Object.entries(attrs)) if (value !== null && value !== undefined) node.setAttribute(key, value);
    return node;
  };
  const sandbox = { el, document: { getElementById: () => ({ appendChild: (node) => appended.push({node, role: node.getAttribute('role')}) }) }, setTimeout: (fn) => { pending.push(fn); return pending.length; }, clearTimeout() {} };
  vm.runInNewContext(`${toastSource}; globalThis.toast = showToast;`, sandbox);
  for (const type of ['info', 'success', 'error']) {
    sandbox.toast(`Host ${type}`, type);
    assert.equal(appended.at(-1).role, type === 'error' ? 'alert' : 'status');
    assert.equal(appended.at(-1).node.className, `toast toast-${type}`);
  }
  const adapter = source.match(/announce:\s*(\([^)]*\)\s*=>\s*(?:\{[\s\S]*?\n {10}\}|showToast\([^\n]+\)))/);
  assert.ok(adapter, 'Find the actual PM host adapter');
  sandbox.showToast = sandbox.toast;
  const announce = vm.runInNewContext(`(${adapter[1]})`, sandbox);
  announce('PM saved');
  assert.ok(appended.at(-1).node.className.includes('pm-toast'));
  assert.equal(appended.at(-1).role, 'status');
  announce('PM failed', 'error');
  const error = appended.at(-1);
  assert.ok(error.node.className.includes('toast-error'));
  assert.ok(error.node.className.includes('pm-toast'));
  assert.equal(error.role, null, 'PM inline error is already the live alert when the companion toast is appended');
  assert.equal(error.node.getAttribute('aria-live'), null);
  assert.equal(pending.length, 5);
});
