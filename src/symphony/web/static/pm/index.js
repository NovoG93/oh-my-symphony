import { createPmApi } from './api.js';
import { createPmState } from './state.js';
import { renderDocumentEditor, renderFindings } from './documents.js';
import { renderWbs } from './wbs.js';
import { renderExchangePanel } from './exchange.js';
import { renderDiagram, diagramSummary } from './diagrams.js';
import { renderStoryboard } from './storyboard.js';
import { PLANNING_ACTIONS, planningActionRequest, verificationActions, evidenceProvenance } from './assistance.js';

const TABS = [['idea', 'Idea'], ['requirements', 'Requirements'], ['design', 'Design'], ['plan', 'Plan'], ['code', 'Code Files']];
const DESIGN_VIEWS = [['ux', 'UX'], ['storyboard', 'Storyboard'], ['architecture', 'Architecture'], ['erd', 'ERD'], ['api', 'API'], ['integration', 'Integration']];
const PLAN_VIEWS = [['wbs', 'Work breakdown'], ['dependencies', 'Dependencies'], ['source', 'Plan document']];

export function capabilityAvailability(payload, name) {
  const explicit = payload?.capabilities?.[name];
  if (explicit) return {
    requested: Boolean(explicit.requested),
    effective: Boolean(explicit.effective),
    reasons: Array.isArray(explicit.reasons) ? explicit.reasons.map(String) : [],
    developmentVerification: Boolean(explicit.development_verification),
  };
  const requested = Boolean(payload?.flags?.[name]);
  const effective = payload?.effective_flags?.[name] ?? requested;
  return { requested, effective: Boolean(effective), reasons: [], developmentVerification: false };
}

export function mountArtifacts({ root, projectId, api: hostApi, ui, onDirtyChange }) {
  const api = createPmApi(hostApi, projectId);
  const state = createPmState(onDirtyChange, projectId);
  const history = { undo: [], redo: [] };
  let editingRequirement = null;
  let selectedStoryboardId = '';
  root.classList.add('pm-workspace-host');

  const guard = (message = 'Discard unsaved changes?') => !state.dirty || window.confirm(message);
  const hostAvailability = (name) => capabilityAvailability(state.capabilities, name);
  const archived = () => state.change?.state === 'Archived' || state.change?.workspace_state === 'RELEASED';
  const availability = (name) => archived()
    ? { ...hostAvailability(name), effective: false, reasons: ['Archived planning history is read-only.'] }
    : hostAvailability(name);
  const unavailableReason = (name) => {
    const value = availability(name);
    return value.reasons.join(' ') || `PM ${name} is unavailable in this host.`;
  };
  const limitAction = (item, name) => {
    const value = availability(name);
    if (!value.effective) {
      item.disabled = true;
      item.dataset.capability = name;
      item.title = unavailableReason(name);
      item.setAttribute('aria-describedby', 'pm-capability-status');
    }
    return item;
  };
  const limitHostAction = (item, name) => {
    const value = hostAvailability(name);
    if (!value.effective) {
      item.disabled = true;
      item.dataset.capability = name;
      item.title = value.reasons.join(' ') || `PM ${name} is unavailable in this host.`;
      item.setAttribute('aria-describedby', 'pm-capability-status');
    }
    return item;
  };
  const limitArchivedAction = (item) => {
    if (archived()) { item.disabled = true; item.title = 'Archived planning history is read-only.'; }
    return item;
  };
  const safeAction = (handler, source = null) => (...args) => Promise.resolve()
    .then(() => handler(...args))
    .catch((error) => reportActionError(error, source || document.activeElement));

  function reportActionError(error, source = null) {
    if (error?.name === 'AbortError') return;
    const code = error?.code ? `${error.code}: ` : '';
    const message = `${code}${error?.message || 'The action failed.'}`;
    root.querySelector('.pm-action-error')?.remove();
    const alert = ui.el('section', { class: 'banner banner-danger pm-action-error', role: 'alert' }, [
      ui.el('strong', null, 'Action unavailable'), ui.el('span', null, message),
    ]);
    (root.querySelector('.pm-body') || root).prepend(alert);
    ui.announce(message);
    if (source?.isConnected && typeof source.focus === 'function') source.focus({ preventScroll: true });
  }

  function capabilityStatus() {
    const names = ['authoring', 'exchange', 'assistance', 'handoff'];
    const unavailable = names.filter((name) => !availability(name).effective);
    const development = names.filter((name) => availability(name).developmentVerification);
    if (!unavailable.length && !development.length) return null;
    const rows = unavailable.map((name) => ui.el('li', null, [
      ui.el('strong', null, `${name}: `), ui.el('span', null, unavailableReason(name)),
    ]));
    if (development.length) rows.push(ui.el('li', null, 'Development verification is active for configured actions. Dependency checks still apply, and release gates are not marked passed.'));
    return ui.el('section', { id: 'pm-capability-status', class: 'pm-status-card', role: 'status' }, [
      ui.el('strong', null, unavailable.length ? 'Some planning actions are unavailable' : 'Development verification context'),
      ui.el('ul', { class: 'pm-capability-list' }, rows),
    ]);
  }

  async function load() {
    const token = state.beginLoad();
    const capabilities = await api.capabilities(token.signal);
    if (capabilities.project_id) api.setProjectId(capabilities.project_id);
    const changes = await api.changes(token.signal);
    if (!state.isCurrent(token.epoch)) return;
    state.capabilities = capabilities; state.changes = changes.changes || [];
    const selected = chooseAvailableChange(state.changes, state.change?.change_id, state.requestedChangeId);
    const historyId = directHistoryChangeId(state.changes, state.requestedChangeId);
    let loaded = null;
    if (historyId) {
      try { loaded = await api.change(historyId, token.signal); }
      catch (error) {
        if (error?.name === 'AbortError') throw error;
        if (!['change_not_found', 'workspace_unavailable'].includes(error?.code)) throw error;
      }
    }
    if (!loaded && selected) loaded = await api.change(selected.change_id, token.signal);
    if (loaded) {
      state.change = loaded;
      state.requestedChangeId = loaded.change_id; state.restoreViewState(); state.externalEdit = state.change.workspace_state === 'EXTERNAL_EDIT'; state.updateUrl();
    } else {
      state.change = null; state.requestedChangeId = ''; state.externalEdit = false;
      state.planFilter = ''; state.selectedTask = ''; state.planScroll = 0;
      editingRequirement = null; history.undo = []; history.redo = [];
      state.setDirty(false); state.updateUrl();
    }
    if (!state.isCurrent(token.epoch)) return;
    await hydrate(token); render();
  }

  function requiredDocumentIds() {
    if (!state.change) return [];
    if (state.tab === 'idea') return ['proposal'];
    if (state.tab === 'requirements') return state.change.documents.filter((item) => item.document_id.startsWith('spec:')).map((item) => item.document_id);
    if (state.tab === 'design') return [state.designView === 'ux' || state.designView === 'storyboard' ? 'ux' : 'design'];
    if (state.tab === 'plan' && state.planView === 'source') return ['tasks'];
    return [];
  }

  async function hydrate(existingToken = null) {
    const token = existingToken || state.beginLoad();
    state.documents.clear(); state.baselines.clear(); state.errors.clear(); state.wbs = null; state.tickets = []; state.coverage = null;
    if (!state.change) return;
    const jobs = [];
    if (state.tab === 'plan') jobs.push(api.wbs(state.change.change_id, token.signal).then((value) => { if (state.isCurrent(token.epoch)) state.wbs = value; }));
    if (state.tab === 'code') jobs.push(api.managedTickets(state.change.change_id, token.signal).then((value) => { if (state.isCurrent(token.epoch)) state.tickets = value.tickets || []; }));
    if (state.tab === 'requirements' || state.tab === 'code') jobs.push(api.coverage(state.change.change_id, state.change.revision, token.signal).then((value) => { if (state.isCurrent(token.epoch)) state.coverage = value; }));
    for (const docId of requiredDocumentIds()) jobs.push(
      api.document(state.change.change_id, docId, token.signal)
        .then((value) => { if (state.isCurrent(token.epoch)) state.documents.set(docId, value); })
        .catch((error) => { if (error.name !== 'AbortError' && state.isCurrent(token.epoch)) state.errors.set(docId, error.message); })
    );
    if (state.tab === 'requirements') for (const docId of requiredDocumentIds()) {
      const capability = docId.slice(5);
      jobs.push(api.baseline(state.change.change_id, capability, token.signal).then((value) => { if (state.isCurrent(token.epoch)) state.baselines.set(capability, value); }).catch(() => {}));
    }
    await Promise.all(jobs);
  }

  async function refreshChange() {
    const changeId = state.change.change_id; const token = state.beginLoad();
    const change = await api.change(changeId, token.signal);
    if (!state.isCurrent(token.epoch) || change.change_id !== changeId) return;
    state.change = change; state.externalEdit = change.workspace_state === 'EXTERNAL_EDIT';
    await hydrate(token); if (state.isCurrent(token.epoch)) render();
  }

  async function selectChange(id) {
    if (!guard('Discard unsaved PM edits and switch changes?')) return;
    const previous = {
      change: state.change, requestedChangeId: state.requestedChangeId, dirty: state.dirty,
      externalEdit: state.externalEdit, documents: new Map(state.documents), baselines: new Map(state.baselines),
      errors: new Map(state.errors), wbs: state.wbs, tickets: state.tickets, coverage: state.coverage,
      tab: state.tab, designView: state.designView, planView: state.planView, planFilter: state.planFilter,
      selectedTask: state.selectedTask, planScroll: state.planScroll, editingRequirement, selectedStoryboardId,
      undo: [...history.undo], redo: [...history.redo], url: location.href,
    };
    const token = state.beginLoad();
    root.setAttribute('aria-busy', 'true');
    for (const control of root.querySelectorAll('button, input, textarea, select')) control.disabled = true;
    try {
      const change = id ? await api.change(id, token.signal) : null;
      if (!state.isCurrent(token.epoch) || (change && change.change_id !== id)) return;
      state.change = change; state.requestedChangeId = change?.change_id || ''; state.externalEdit = change?.workspace_state === 'EXTERNAL_EDIT';
      state.restoreViewState(); await hydrate(token);
      if (!state.isCurrent(token.epoch)) return;
      state.setDirty(false); state.updateUrl(); history.undo = []; history.redo = []; selectedStoryboardId = ''; render();
    } catch (error) {
      if (!state.isCurrent(token.epoch)) return;
      state.change = previous.change; state.requestedChangeId = previous.requestedChangeId; state.externalEdit = previous.externalEdit;
      state.documents = previous.documents; state.baselines = previous.baselines; state.errors = previous.errors;
      state.wbs = previous.wbs; state.tickets = previous.tickets; state.coverage = previous.coverage;
      state.tab = previous.tab; state.designView = previous.designView; state.planView = previous.planView;
      state.planFilter = previous.planFilter; state.selectedTask = previous.selectedTask; state.planScroll = previous.planScroll;
      editingRequirement = previous.editingRequirement; selectedStoryboardId = previous.selectedStoryboardId;
      history.undo = previous.undo; history.redo = previous.redo; state.setDirty(previous.dirty);
      window.history.replaceState(null, '', previous.url); render();
      queueMicrotask(() => root.querySelector('.pm-change-select')?.focus({ preventScroll: true }));
      throw error;
    } finally {
      if (state.isCurrent(token.epoch)) root.removeAttribute('aria-busy');
    }
  }

  async function selectTab(tab) {
    if (tab === state.tab || !guard('Discard unsaved changes and leave this tab?')) return;
    state.setDirty(false); state.setTab(tab); await hydrate(); render();
  }

  function render() {
    if (state.disposed) return;
    root.replaceChildren(buildHeader(), buildTabs(), buildBody());
    localizePm(root);
    root.querySelector('[aria-current="page"]')?.focus({ preventScroll: true });
  }

  function buildHeader() {
    const visibleChanges = state.change && !state.changes.some((item) => item.change_id === state.change.change_id)
      ? [state.change, ...state.changes] : state.changes;
    const select = ui.el('select', { class: 'pm-change-select', 'aria-label': 'Planning change' }, [
      ui.el('option', { value: '' }, 'Select a change'),
      ...visibleChanges.map((item) => ui.el('option', { value: item.change_id, selected: state.change?.change_id === item.change_id }, `${item.locator} · ${item.state}`)),
    ]);
    select.addEventListener('change', () => {
      safeAction(() => selectChange(select.value), select)();
    });
    const create = limitHostAction(button('New change', openCreateDialog, 'btn btn-primary'), 'authoring');
    const repair = limitAction(button('Repair workspace', repairWorkspace), 'authoring');
    const convert = limitHostAction(button('Convert legacy plan', convertLegacy), 'handoff');
    return ui.el('header', { class: 'pm-header' }, [
      ui.el('div', { class: 'pm-heading-copy' }, [ui.el('span', { class: 'pm-kicker' }, 'Product planning'), ui.el('h1', null, 'Artifacts'), ui.el('p', null, 'Shape the idea, align requirements, and hand off executable work.')]),
      ui.el('div', { class: 'pm-header-controls' }, [ui.el('div', { class: 'pm-actions' }, [select, repair, convert, create])]),
    ]);
  }

  function buildTabs() {
    return ui.el('nav', { class: 'pm-tabs', 'aria-label': 'Planning artifacts' }, TABS.map(([id, label]) => {
      const item = button(label, () => selectTab(id), `pm-tab ${state.tab === id ? 'active' : ''}`);
      if (state.tab === id) item.setAttribute('aria-current', 'page');
      return item;
    }));
  }

  function buildBody() {
    const status = capabilityStatus();
    if (!state.change) return ui.el('main', { class: 'pm-body' }, [status, buildWelcome()]);
    const body = ui.el('main', { class: 'pm-body' }, [status, buildRevisionBar()]);
    if (state.externalEdit) {
      body.append(ui.el('section', { class: 'pm-empty-state', role: 'status' }, [ui.el('h2', null, 'External editing is active'), ui.el('p', null, 'Workspace writes are paused. Finish external editing to reindex, validate, and resume here.') ]));
      return body;
    }
    if (state.tab === 'plan') body.append(buildPlan());
    else if (state.tab === 'design') body.append(buildDesign());
    else if (state.tab === 'requirements') body.append(buildRequirements());
    else if (state.tab === 'code') body.append(buildCodeFiles());
    else body.append(buildDocument('proposal'));
    return body;
  }

  function buildRevisionBar() {
    const external = state.externalEdit
      ? limitAction(button('Finish external edit & reindex', async () => { const report = await api.endExternal(state.change.change_id); state.externalEdit = false; await refreshChange(); showFindings(report); }), 'authoring')
      : limitAction(button('Edit in external editor', async () => { await api.beginExternal(state.change.change_id, state.change.revision); state.externalEdit = true; render(); ui.announce('App writes paused while external editing is active.'); }), 'authoring');
    const validate = limitArchivedAction(button('Validate', async () => showFindings(await api.validate(state.change.change_id, state.change.revision))));
    const approve = limitAction(button('Approve revision', async () => {
      if (!window.confirm(pmText('Approve this exact planning revision locally?'))) return;
      await api.approve(state.change.change_id, state.change.revision, state.change.approval_confirmation_token); await refreshChange(); ui.announce(pmText('Revision approved.'));
    }, 'btn btn-primary'), 'authoring');
    return ui.el('section', { class: 'pm-revision-bar' }, [
      ui.el('div', null, [ui.el('strong', null, state.change.locator), ui.el('span', { class: `pm-state ${state.change.state.toLowerCase()}` }, state.change.state), ui.el('code', { title: state.change.revision }, state.change.revision)]),
      ui.el('div', { class: 'pm-actions' }, [external, validate, approve]),
    ]);
  }

  function buildDocument(docId) {
    const doc = state.documents.get(docId); const draft = doc ? state.readDraft(docId) : null;
    const editor = renderDocumentEditor({
      ui, document: doc, error: state.errors.get(docId), draft,
      onDirty: state.setDirty, onDraft: (source) => state.writeDraft(docId, source),
      onSave: safeAction(async (source) => {
        await api.saveDocument(state.change.change_id, state.change.revision, docId, source);
        state.clearDraft(docId); state.setDirty(false); await refreshChange(); ui.announce('Draft saved as a new revision.');
      }),
      onAssist: safeAction(async () => openPlanningDialog(docId)),
      authoring: availability('authoring'), assistance: availability('assistance'),
      onError: reportActionError,
    });
    const kinds = docId === 'proposal' ? ['decision'] : docId === 'ux' ? ['storyboard'] : docId === 'design' ? ['contract'] : docId.startsWith('spec:') ? ['requirement', 'scenario'] : [];
    if (!kinds.length) return editor;
    const actions = kinds.map((kind) => limitAction(button(`Add ${kind}`, async () => {
      if (state.dirty) { ui.announce('Save or discard the current draft before adding a semantic item.'); return; }
      const title = window.prompt(`${kind[0].toUpperCase()}${kind.slice(1)} title`); if (!title) return;
      const result = await api.createSemantic(state.change.change_id, state.change.revision, docId, kind, title);
      await refreshChange(); ui.announce(`Created ${result.impacted_ids?.[0] || kind}.`);
    }), 'authoring'));
    return ui.el('div', { class: 'pm-card-grid' }, [ui.el('div', { class: 'pm-plan-action-row' }, actions), editor]);
  }

  function buildRequirements() {
    const ids = requiredDocumentIds();
    if (editingRequirement && ids.includes(editingRequirement)) return ui.el('div', { class: 'pm-card-grid' }, [ui.el('div', { class: 'pm-plan-action-row' }, [button('Back to requirements', () => { editingRequirement = null; render(); })]), buildDocument(editingRequirement)]);
    const create = limitAction(button('Add capability', createCapability, 'btn btn-primary'), 'authoring');
    if (!ids.length) return ui.el('div', { class: 'pm-card-grid' }, [ui.el('div', { class: 'pm-plan-action-row' }, [create]), empty('No requirement specifications are available for this change.')]);
    const cards = ids.map((id) => {
      const doc = state.documents.get(id);
      if (!doc) return ui.el('section', { class: 'pm-error-state', role: 'alert' }, state.errors.get(id) || `Could not load ${id}`);
      const capability = id.slice(5); const baseline = state.baselines.get(capability);
      const delta = baseline?.exists ? lineDelta(baseline.source, doc.source) : { added: doc.source.split('\n').length, removed: 0 };
      return ui.el('section', { class: 'pm-readable-card' }, [
        ui.el('div', { class: 'pm-card-heading' }, [ui.el('div', null, [ui.el('h2', null, capability), ui.el('span', { class: 'pm-count' }, `+${delta.added} / −${delta.removed} lines from baseline`)]), ui.el('div', { class: 'pm-actions' }, [ui.el('code', null, doc.path), limitArchivedAction(button('Edit requirements', () => { editingRequirement = id; render(); }))])]),
        ui.el('div', { class: 'pm-requirement-delta' }, [
          ui.el('article', { class: 'pm-document-preview' }, [ui.el('h3', null, 'Proposed'), ui.renderMarkdown(doc.source)]),
          ui.el('article', { class: 'pm-document-preview pm-baseline' }, [ui.el('h3', null, baseline?.exists ? 'Current baseline' : 'Current baseline · new capability'), baseline?.exists ? ui.renderMarkdown(baseline.source) : ui.el('p', null, 'No baseline specification exists yet.')]),
        ]),
        ui.el('details', { class: 'pm-source-details' }, [ui.el('summary', null, 'Source'), ui.el('pre', null, doc.source)]),
      ]);
    });
    return ui.el('div', { class: 'pm-card-grid' }, [ui.el('div', { class: 'pm-plan-action-row' }, [create]), ...cards, buildCoverage()]);
  }

  async function createCapability() {
    const capability = window.prompt('Capability identifier (lowercase words with hyphens)'); if (!capability) return;
    const title = window.prompt('Requirement title', capability.replaceAll('-', ' ')); if (!title) return;
    await api.createSpec(state.change.change_id, state.change.revision, capability, title); await refreshChange(); ui.announce(`Created ${capability} requirements.`);
  }

  function buildDesign() {
    const selector = segmented(DESIGN_VIEWS, state.designView, async (value) => { state.setDesignView(value); await hydrate(); render(); });
    const docId = state.designView === 'ux' || state.designView === 'storyboard' ? 'ux' : 'design';
    const doc = state.documents.get(docId);
    const surface = ui.el('div', { class: 'pm-design-stack' }, [selector]);
    if (!doc) { surface.append(empty(state.errors.get(docId) || 'Loading design document…')); return surface; }
    if (state.designView === 'storyboard') {
      surface.append(renderStoryboard({
        ui, frames: state.change.storyboard_frames || [], selectedId: selectedStoryboardId,
        authoring: availability('authoring'), onError: reportActionError,
        onSelect: (id) => { selectedStoryboardId = id; render(); },
        onClose: () => { const prior = selectedStoryboardId; selectedStoryboardId = ''; render(); queueMicrotask(() => root.querySelector(`[data-storyboard-id="${prior}"]`)?.focus()); },
        onSave: async (operation) => {
          await api.patchStoryboard(state.change.change_id, state.change.revision, operation);
          selectedStoryboardId = ''; await refreshChange(); ui.announce('Storyboard frame saved.');
        },
      }));
      surface.append(limitArchivedAction(button('Edit source', () => { state.setDesignView('ux'); surface.replaceChildren(selector, buildDocument('ux')); })));
      return surface;
    }
    const section = extractSection(doc.source, state.designView);
    surface.append(ui.el('section', { class: 'pm-readable-card' }, [ui.el('article', { class: 'pm-document-preview' }, ui.renderMarkdown(section || doc.source))]));
    for (const diagram of diagramBlocks(section || doc.source)) {
      const target = ui.el('div', { class: 'pm-diagram', role: 'status' }, diagramSummary(diagram).text);
      surface.append(target, ui.el('details', { class: 'pm-source-details' }, [ui.el('summary', null, 'Diagram text alternative'), ui.el('pre', null, diagram)]));
      queueMicrotask(() => renderDiagram(target, diagram, diagramSummary(diagram).text));
    }
    surface.append(limitArchivedAction(button('Edit source', () => { state.setDesignView(docId === 'ux' ? 'ux' : 'architecture'); surface.replaceChildren(selector, buildDocument(docId)); })));
    return surface;
  }

  function buildPlan() {
    const stack = ui.el('div', { class: 'pm-plan-stack' }, [segmented(PLAN_VIEWS, state.planView, async (value) => { state.setPlanView(value); await hydrate(); render(); })]);
    if (state.planView === 'source') { stack.append(buildDocument('tasks')); return stack; }
    if (!state.wbs) { stack.append(empty('Loading plan…')); return stack; }
    if (state.planView === 'dependencies') {
      const rows = state.wbs.tasks.flatMap((task) => (task.dependencies || []).map((dep) => [task.task_id, dep.task_id, dep.kind, dep.justification || '—']));
      stack.append(simpleTable(['Task', 'Needs', 'Dependency', 'Reason'], rows)); return stack;
    }
    const handoff = limitAction(button('Preview approved handoff', previewHandoff, 'btn btn-primary'), 'handoff');
    const archive = limitAction(button('Preview archive', previewArchive), 'handoff');
    stack.append(ui.el('div', { class: 'pm-plan-action-row' }, [archive, handoff]));
    stack.append(renderWbs({ ui, view: state.wbs, semanticIndex: state.change.semantic_index || [], onNavigateReference: safeAction(navigateSemantic), onSetField: safeAction(setTaskField), onPaste: safeAction(previewPaste), onCommand: safeAction(commandWbs), onCopy: safeAction(copyTsv), onSelect: state.setSelectedTask, onFilter: state.setPlanFilter, onScroll: state.setPlanScroll, selectedTask: state.selectedTask, filter: state.planFilter, scrollTop: state.planScroll, canUndo: history.undo.length > 0, canRedo: history.redo.length > 0, approved: Boolean(state.change.approval_id), readOnlyReason: availability('authoring').effective ? '' : unavailableReason('authoring') }));
    stack.append(renderExchangePanel({ ui, onExport: exportPlanning, onImport: importWorkbook, onApply: applyImport, workbookAvailability: hostAvailability('exchange'), readOnly: archived(), onError: reportActionError, translate: pmText }), renderFindings(ui, state.wbs.findings));
    return stack;
  }

  async function navigateSemantic(item) {
    if (!item) return;
    if (item.kind === 'requirement' || item.kind === 'scenario') {
      state.setTab('requirements'); editingRequirement = item.document_id;
    } else if (item.kind === 'storyboard') {
      state.setTab('design'); state.setDesignView('storyboard');
    } else if (item.kind === 'contract') {
      state.setTab('design'); state.setDesignView('api');
    } else {
      state.setTab('idea');
    }
    await hydrate(); render();
  }

  function buildCoverage() {
    if (!state.coverage) return empty('Coverage is loading…');
    const entities = state.coverage.entities || [];
    return ui.el('section', { class: 'pm-readable-card pm-coverage' }, [
      ui.el('h2', null, 'Requirement and scenario coverage'),
      ui.el('p', null, 'Specified, linked, implemented, tested, accepted, and waived remain independent.'),
      ...entities.map((entity) => {
        const states = ['specified', 'linked', 'implemented', 'tested', 'accepted', 'waived'];
        const waiverActions = (entity.waivers || []).filter((item) => item.state === 'ACTIVE').map((item) => limitAction(button('Revoke waiver', () => revokeWaiver(item)), 'authoring'));
        return ui.el('details', { class: 'pm-coverage-entity' }, [
          ui.el('summary', null, `${entity.semantic_id} · ${entity.title}`),
          ui.el('div', { class: 'pm-coverage-states' }, states.map((name) => {
            const value = entity.states?.[name] || { satisfied: false, qualified_count: 0, total_count: 0 };
            return ui.el('span', { class: `pm-coverage-state ${value.satisfied ? 'ready' : ''}` }, `${name} · ${value.qualified_count}/${value.total_count}`);
          })),
          simpleTable(['Task', 'Revision', 'Ticket', 'Implemented', 'Tested', 'Accepted', 'Provenance'], (entity.task_links || []).map((link) => [link.task_id, link.task_revision, link.ticket_identifier, link.implemented ? 'Yes' : 'No', link.tested ? 'Yes' : 'No', link.accepted ? 'Yes' : 'No', link.test_provenance || '—'])),
          ...(entity.waivers || []).map((waiver) => ui.el('p', { class: 'pm-waiver' }, `${waiver.state} waiver · ${waiver.reason} · ${waiver.reviewer || 'unknown reviewer'} · ${waiver.revision}`)),
          ui.el('div', { class: 'pm-actions' }, [limitAction(button('Waive coverage', () => openWaiverDialog(entity)), 'authoring'), ...waiverActions]),
        ]);
      }),
      renderFindings(ui, state.coverage.findings || []),
    ]);
  }

  function openWaiverDialog(entity) {
    const previousFocus = document.activeElement;
    const reason = ui.el('textarea', { class: 'input pm-drawer-textarea', 'aria-label': 'Waiver reason' });
    const reviewer = ui.el('input', { class: 'input', 'aria-label': 'Reviewer label', value: 'local operator' });
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Waive coverage' });
    const close = () => { dialog.remove(); previousFocus?.focus?.(); };
    const preview = button('Preview waiver', async () => {
      const result = await api.waiverPreview(state.change.change_id, state.change.revision, entity.semantic_id, reason.value, reviewer.value);
      showConfirmation('Confirm coverage waiver', 'This waiver does not grant execution, acceptance, ancestry, completion, or archive authority.', `${entity.semantic_id}\n${reason.value}\n${state.change.revision}`, 'Confirm waiver', async () => {
        await api.createWaiver(state.change.change_id, state.change.revision, { semantic_id: entity.semantic_id, reason: reason.value, reviewer_label: reviewer.value }, result.confirmation_token);
        await refreshChange(); ui.announce('Coverage waiver recorded without granting completion authority.');
      }); close();
    }, 'btn btn-primary');
    dialog.append(ui.el('h2', null, `Waive ${entity.semantic_id}`), field('Reason', reason), field('Reviewer', reviewer), ui.el('div', { class: 'pm-actions' }, [preview, button('Cancel', close)]));
    dialog.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
    root.querySelector('.pm-body')?.prepend(dialog); reason.focus();
  }

  async function revokeWaiver(waiver) {
    if (!window.confirm(`Revoke waiver ${waiver.waiver_id}?`)) return;
    await api.revokeWaiver(state.change.change_id, state.change.revision, waiver.waiver_id, 'Revoked after review', 'local operator');
    await refreshChange(); ui.announce('Coverage waiver revoked.');
  }

  function renderEvidenceReceipt(item) {
    const hostExecuted = item.test_provenance === 'host_executed' || item.procedure_kind === 'native';
    const rows = [
      ['Procedure', item.procedure_id], ['Result', item.result], ['Provenance', hostExecuted ? 'Host executed' : 'Manual reported'],
      ['Tested commit', item.tested_commit], ['Receipt', item.receipt_id],
    ];
    if (hostExecuted) rows.push(['Return code', item.return_code ?? 'Unavailable'], ['Timed out', item.timed_out ? 'Yes' : 'No'], ['Stdout hash', item.stdout_hash], ['Stderr hash', item.stderr_hash]);
    else rows.push(['Environment', item.environment_summary || '—'], ['Submitted by', item.submitted_by || '—']);
    return ui.el('article', { class: 'pm-evidence-receipt' }, [ui.el('h4', null, `${item.procedure_id} · ${item.result}`), ui.el('dl', { class: 'pm-task-meta' }, rows.flatMap(([name, value]) => [ui.el('dt', null, name), ui.el('dd', { class: name.includes('hash') || name.includes('commit') ? 'pm-mono' : '' }, value ?? '—')]))]);
  }

  function buildCodeFiles() {
    const files = state.change.documents.map((doc) => [doc.path, doc.state, doc.editable ? 'Planning source' : 'Read only']);
    const ticketCards = state.tickets.map((ticket) => {
      const accept = limitAction(button(ticket.acceptance ? 'Accepted' : 'Review verification', () => reviewAcceptance(ticket), 'btn'), 'handoff');
      accept.disabled = accept.disabled || Boolean(ticket.acceptance || !ticket.verified_commit);
      const allowed = ticket.allowed_procedures || (ticket.required_procedure_ids || []).map((procedure_id) => ({ procedure_id, kind: 'native', label: procedure_id }));
      const procedures = verificationActions(allowed).flatMap((procedure) => {
        const noRun = limitAction(button(procedure.label, () => openEvidenceDialog(ticket, procedure)), 'handoff');
        const runBound = procedure.kind === 'native' && ticket.run_id ? limitAction(button(`Verify native run with ${procedure.procedure_id}`, () => runVerification(ticket, procedure.procedure_id)), 'handoff') : null;
        return [noRun, runBound].filter(Boolean);
      });
      const codeRows = (ticket.code_files || []).map((item) => [item.status, item.path]);
      const receipts = ticket.evidence || ticket.receipts || [];
      const artifactLinks = receipts.flatMap((item) => [item.report_ref, ...(item.artifact_refs || [])].filter(Boolean).map((raw) => {
        const ref = typeof raw === 'string' ? { ref: raw } : raw;
        if (ref.download_url) return button(`${item.procedure_id} · ${ref.ref}`, () => ui.downloadArtifact(withProjectIdentity(ref.download_url, state.capabilities?.project_id || state.projectId), ref.ref.split('/').pop()), 'pm-evidence-link');
        const prefix = `artifact://${ticket.ticket_identifier}/`; const name = String(ref.ref || '').startsWith(prefix) ? String(ref.ref).slice(prefix.length) : '';
        return name ? button(`${item.procedure_id} · ${name}`, () => ui.downloadArtifact(`/issues/${encodeURIComponent(ticket.ticket_identifier)}/artifacts/${encodeURIComponent(name)}`, name), 'pm-evidence-link') : null;
      })).filter(Boolean);
      return ui.el('article', { class: 'pm-ticket-card' }, [
        ui.el('div', { class: 'pm-card-heading' }, [ui.el('h3', null, ticket.ticket_identifier), ui.el('span', { class: 'pm-state' }, ticket.state)]),
        ui.el('dl', { class: 'pm-task-meta' }, [ui.el('dt', null, 'Task'), ui.el('dd', null, ticket.task_id), ui.el('dt', null, 'Revision'), ui.el('dd', null, ticket.task_revision), ui.el('dt', null, 'Base commit'), ui.el('dd', { class: 'pm-mono' }, ticket.base_commit || '—'), ui.el('dt', null, 'Head commit'), ui.el('dd', { class: 'pm-mono' }, ticket.head_commit || 'No completed run'), ui.el('dt', null, 'Verified candidate'), ui.el('dd', { class: 'pm-mono' }, ticket.verified_commit || 'No complete passing evidence set'), ui.el('dt', null, 'Accepted commit'), ui.el('dd', { class: 'pm-mono' }, ticket.acceptance?.tested_commit || 'No explicit acceptance')]),
        ui.el('details', { class: 'pm-source-details' }, [ui.el('summary', null, `Code delta · ${codeRows.length} files`), simpleTable(['Status', 'Path'], codeRows)]),
        ui.el('details', { class: 'pm-source-details' }, [ui.el('summary', null, `Verification evidence · ${receipts.length} receipts · ${evidenceProvenance(receipts)}`), ...receipts.map(renderEvidenceReceipt), ...artifactLinks]),
        ui.el('div', { class: 'pm-actions' }, [ticket.head_commit ? button('Review Git commit', () => ui.openGitCommit(ticket.head_commit)) : null, ticket.run_id ? button('Open run evidence', () => ui.openRun(ticket.run_id)) : null, ...procedures, accept]),
      ]);
    });
    return ui.el('div', { class: 'pm-card-grid' }, [
      ui.el('section', { class: 'pm-readable-card' }, [ui.el('h2', null, 'Planning files'), simpleTable(['Path', 'State', 'Role'], files)]),
      ui.el('section', { class: 'pm-readable-card' }, [ui.el('h2', null, 'Handoff tickets'), ui.el('p', null, ticketCards.length ? 'Verification and acceptance are bound to the exact task revision and tested commit.' : 'No managed tickets have been handed off.'), ...ticketCards]),
      buildCoverage(),
    ]);
  }

  function openEvidenceDialog(ticket, procedure) {
    const previousFocus = document.activeElement;
    const native = procedure.kind === 'native';
    const testedCommit = ui.el('input', { class: 'input', 'aria-label': 'Tested commit', value: ticket.head_commit || ticket.base_commit || '' });
    const environment = ui.el('textarea', { class: 'input', 'aria-label': 'Environment summary' });
    const result = ui.el('select', { class: 'input', 'aria-label': 'Manual result' }, [ui.el('option', { value: 'passed' }, 'Passed'), ui.el('option', { value: 'failed' }, 'Failed')]);
    const started = ui.el('input', { class: 'input', type: 'datetime-local', 'aria-label': 'Started at' });
    const completed = ui.el('input', { class: 'input', type: 'datetime-local', 'aria-label': 'Completed at' });
    const report = ui.el('input', { class: 'input', 'aria-label': 'Report path' });
    const artifacts = ui.el('textarea', { class: 'input', 'aria-label': 'Artifact references' });
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': native ? 'Run verification' : 'Submit completion evidence' });
    const close = () => { dialog.remove(); previousFocus?.focus?.(); };
    const preview = button(native ? 'Preview host verification' : 'Preview evidence', async () => {
      if (!testedCommit.value.trim()) throw new Error('Tested commit is required.');
      if (!native && (!environment.value.trim() || !started.value || !completed.value || !report.value.trim())) throw new Error('Environment, timestamps, and report path are required.');
      if (!native && procedure.artifacts === 'required' && !artifacts.value.trim()) throw new Error('This procedure requires at least one artifact reference.');
      const payload = {
        ticket_identifier: ticket.ticket_identifier, task_revision: ticket.task_revision,
        tested_commit: testedCommit.value.trim(), procedure_id: procedure.procedure_id,
      };
      if (!native) Object.assign(payload, {
        environment_summary: environment.value, result: result.value,
        started_at: new Date(started.value).toISOString(), completed_at: new Date(completed.value).toISOString(),
        report_ref: { ref_kind: 'git_blob', ref: report.value.trim() },
        artifact_refs: artifacts.value.split('\n').map((ref) => ref.trim()).filter(Boolean).map((ref) => ref.startsWith('artifact://') ? { ref_kind: 'artifact_store', ref } : { ref_kind: 'git_blob', ref }),
      });
      const response = await api.evidencePreview(state.change.change_id, state.change.revision, payload);
      showConfirmation(native ? 'Confirm host verification' : 'Confirm completion evidence', native ? 'The host will run the approved command and derive the actual result.' : 'This stores inspectable evidence but does not complete or accept the task.', JSON.stringify(response.normalized || payload, null, 2), native ? 'Run approved command' : 'Submit evidence', async () => {
        await api.evidenceSubmit(state.change.change_id, state.change.revision, response.normalized || payload, response.confirmation_token);
        await refreshChange(); ui.announce(native ? 'Host verification receipt recorded.' : 'Manual completion evidence recorded. Explicit acceptance is still required.');
      }); close();
    }, 'btn btn-primary');
    dialog.append(ui.el('h2', null, native ? 'Run verification' : 'Submit completion evidence'), ui.el('p', null, `${procedure.label || procedure.procedure_id} · ${procedure.definition_digest || ''}`), field('Tested commit', testedCommit));
    if (native) dialog.append(ui.el('p', { class: 'pm-empty' }, 'The host derives return code, output hashes, timeout, and result. You cannot report a native pass.'));
    else dialog.append(field('Environment summary', environment), field('Result', result), field(`Started at · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`, started), field(`Completed at · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`, completed), field('Report path at tested commit', report), field('Artifact references', artifacts));
    dialog.append(ui.el('div', { class: 'pm-actions' }, [preview, button('Cancel', close)]));
    dialog.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
    localizePm(dialog); root.querySelector('.pm-body')?.prepend(dialog); testedCommit.focus();
  }

  async function reviewAcceptance(ticket) {
    const preview = await api.acceptancePreview(ticket.ticket_identifier, ticket.task_revision, ticket.verified_commit);
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Acceptance review' });
    const confirm = button('Accept verified revision', async () => {
      await api.accept(ticket.ticket_identifier, ticket.task_revision, ticket.verified_commit, preview.confirmation_token);
      dialog.remove(); await refreshChange(); ui.announce('Verified revision accepted.');
    }, 'btn btn-primary');
    confirm.disabled = !preview.ready;
    dialog.append(ui.el('h2', null, preview.ready ? 'Verification is complete' : 'Verification is incomplete'), ui.el('p', null, `${preview.passing_receipt_ids.length} passing receipts · ${preview.missing_procedure_ids.length} missing procedures`), renderFindings(ui, preview.findings), ui.el('div', { class: 'pm-actions' }, [confirm, button('Cancel', () => dialog.remove())]));
    localizePm(dialog); root.querySelector('.pm-body')?.prepend(dialog); dialog.querySelector('button:not([disabled])')?.focus();
  }

  async function runVerification(ticket, procedureId) {
    const runId = window.prompt(`Run ID whose checkout should execute ${procedureId}`); if (!runId) return;
    const receipt = await api.verify(state.change.change_id, ticket.ticket_identifier, ticket.task_revision, procedureId, runId);
    ui.announce(`${procedureId}: ${receipt.result}`); await refreshChange();
  }

  async function runWbs(operations, inverse = null, record = true) {
    const result = await api.patchWbs(state.change.change_id, state.change.revision, operations);
    if (record && inverse) { history.undo.push({ operations: inverse, inverse: operations }); history.redo = []; }
    await refreshChange();
    return result;
  }

  async function setTaskField(taskId, field, value) {
    const task = state.wbs.tasks.find((item) => item.task_id === taskId); if (!task) return;
    const normalized = ['acceptance', 'verification', 'requirement_refs', 'storyboard_refs', 'design_refs'].includes(field) && typeof value === 'string' ? value.split('\n').map((item) => item.trim()).filter(Boolean) : value;
    const apiField = ({ kind: 'type', agent_kind_override: 'agent_kind', dependencies: 'depends_on', requirement_refs: 'requirements', storyboard_refs: 'storyboards' })[field] || field;
    await runWbs([{ op: 'set_field', task_id: taskId, field: apiField, value: normalized }], [{ op: 'set_field', task_id: taskId, field: apiField, value: task[field] }]);
  }

  async function commandWbs(command, selectedId) {
    if (command === 'undo' || command === 'redo') {
      const from = command === 'undo' ? history.undo : history.redo, to = command === 'undo' ? history.redo : history.undo;
      const item = from.pop(); if (!item) return; await runWbs(item.operations, null, false); to.push({ operations: item.inverse, inverse: item.operations }); render(); return;
    }
    if (command === 'copy') { await copyTsv(state.wbs.tasks); return; }
    const tasks = state.wbs.tasks; const selected = tasks.find((item) => item.task_id === selectedId);
    if (command === 'add-root' || command === 'add-child' || command === 'add-sibling' || command === 'add-group') {
      const group = command === 'add-group';
      if (command === 'add-sibling' && !selected) { ui.announce('Select a task first.'); return; }
      const parentId = command === 'add-child' ? selectedId : command === 'add-sibling' ? selected.parent_id : null;
      const siblings = tasks.filter((item) => item.parent_id === parentId);
      const task = { parent_id: parentId, order: Math.max(0, ...siblings.map((item) => item.order)) + 10, kind: group ? 'Group' : 'Feature', title: group ? 'New group' : 'New task', description: '', dependencies: [], requirement_refs: [], storyboard_refs: [], design_refs: [], acceptance: [], verification: [], integration_impact: '', risk: '', notes: '', executable: !group };
      const result = await runWbs([{ op: 'create_task', task }], null, false);
      const id = result.impacted_ids?.[0]; if (!id) throw new Error('Server did not assign a work item identity.');
      const restored = { ...task, task_id: id };
      history.undo.push({ operations: [{ op: 'delete_task', task_id: id }], inverse: [{ op: 'restore_task', task_id: id, task: restored }] }); history.redo = []; render();
      return;
    }
    if (!selected) { ui.announce('Select a task first.'); return; }
    if (command === 'delete' && state.change.approval_id) {
      const reason = window.prompt(`Cancellation reason for ${selected.task_id}`); if (!reason?.trim()) return;
      const raw = window.prompt('Successor task IDs (comma-separated, optional)', '') || '';
      const successorRefs = raw.split(',').map((item) => item.trim()).filter(Boolean);
      if (window.confirm(`Cancel ${selected.task_id} and retain its reviewed scope?`)) await runWbs([{ op: 'cancel_task', task_id: selected.task_id, reason: reason.trim(), successor_refs: successorRefs }]);
      return;
    }
    if (command === 'delete') { if (window.confirm(`Delete ${selected.task_id}?`)) await runWbs([{ op: 'delete_task', task_id: selected.task_id }], [{ op: 'restore_task', task_id: selected.task_id, task: selected }]); return; }
    const siblings = tasks.filter((item) => item.parent_id === selected.parent_id).sort((a, b) => a.order - b.order);
    const index = siblings.findIndex((item) => item.task_id === selected.task_id);
    if ((command === 'up' && index > 0) || (command === 'down' && index < siblings.length - 1)) {
      const { operations: ops, inverse } = moveOperations(tasks, selected.task_id, command);
      await runWbs(ops, inverse); return;
    }
    if (command === 'indent' && index > 0) await runWbs([{ op: 'set_field', task_id: selected.task_id, field: 'parent_id', value: siblings[index - 1].task_id }], [{ op: 'set_field', task_id: selected.task_id, field: 'parent_id', value: selected.parent_id }]);
    if (command === 'outdent' && selected.parent_id) {
      const parent = tasks.find((item) => item.task_id === selected.parent_id);
      await runWbs([{ op: 'set_field', task_id: selected.task_id, field: 'parent_id', value: parent?.parent_id || null }], [{ op: 'set_field', task_id: selected.task_id, field: 'parent_id', value: selected.parent_id }]);
    }
  }

  async function previewPaste(text) {
    let rows;
    try { rows = parseTsv(text); } catch (error) { ui.announce(error.message); return; }
    if (rows[0]?.[0].toLowerCase() === 'task id' && rows[0]?.[1].toLowerCase() === 'field') rows.shift();
    const allowed = new Set(['parent_id', 'order', 'type', 'title', 'human_owner', 'agent_kind', 'priority', 'target_date', 'estimate', 'description', 'acceptance', 'verification', 'depends_on', 'requirements', 'storyboards', 'design_refs', 'integration_impact', 'risk', 'notes']);
    const taskIds = new Set(state.wbs.tasks.map((item) => item.task_id));
    const seen = new Set();
    for (const row of rows) {
      const key = `${row[0]}:${row[1]}`;
      if (!taskIds.has(row[0])) { ui.announce(`Unknown task ${row[0]}.`); return; }
      if (!allowed.has(row[1])) { ui.announce(`Field ${row[1]} is not editable.`); return; }
      if (seen.has(key)) { ui.announce(`Duplicate TSV field ${key}.`); return; }
      seen.add(key);
      try { parseTsvValue(row[1], row.slice(2).join('\t')); } catch (error) { ui.announce(error.message); return; }
    }
    const operations = rows.map((row) => ({ op: 'set_field', task_id: row[0], field: row[1], value: parseTsvValue(row[1], row.slice(2).join('\t')) }));
    const serverPreview = await api.previewWbs(state.change.change_id, state.change.revision, operations);
    const blocking = (serverPreview.findings || []).filter((item) => item.severity === 'blocking');
    const preview = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Paste preview' });
    const apply = button(`Apply ${rows.length} rows`, async () => {
      await runWbs(operations); preview.remove();
    }, 'btn btn-primary');
    apply.disabled = blocking.length > 0;
    preview.append(ui.el('h2', null, 'Transactional TSV preview'), ui.el('p', null, 'Columns: Task ID, field, value. Quoted cells and multiline values are preserved.'), simpleTable(['Task', 'Field', 'Value'], rows.map((row) => [row[0], row[1], row.slice(2).join('\t')])), renderFindings(ui, serverPreview.findings), ui.el('div', { class: 'pm-actions' }, [apply, button('Cancel', () => preview.remove())]));
    root.querySelector('.pm-plan-stack')?.prepend(preview); apply.focus();
  }

  async function copyTsv(tasks) {
    await navigator.clipboard.writeText(serializeWbsTsv(tasks)); ui.announce('Canonical WBS TSV copied.');
  }

  async function exportPlanning(format) { const item = await api.export(state.change.change_id, state.change.revision, format); await api.download(`/exports/${item.export_id}/file`, item.filename); }
  async function importWorkbook(file) { return api.importPreview(state.change.change_id, state.change.revision, file.name, await fileToBase64(file)); }
  async function applyImport(preview, resolutions) { await api.importApply(state.change.change_id, state.change.revision, preview.import_id, preview.preview_digest, resolutions, preview.apply_operation_id); await refreshChange(); ui.announce(pmText('Reviewed workbook changes applied.')); }

  async function previewHandoff() {
    const preview = await api.handoffPreview(state.change.change_id, state.change.revision);
    const korean = window.i18n?.lang === 'ko';
    const summary = preview.items.map((item) => `${item.task_id} → ${item.deterministic_ticket_id}${item.runnable ? '' : korean ? ' (차단됨)' : ' (blocked)'}`).join('\n');
    showConfirmation('Confirm native handoff', korean ? `이 승인된 리비전에서 티켓 ${preview.items.length}개를 생성합니다.` : `${preview.items.length} tickets will be created from this exact approved revision.`, summary, 'Create native tickets', async () => {
      await api.handoff(state.change.change_id, state.change.revision, preview.preview_token, preview.preview_token); ui.announce(korean ? '승인된 계획을 전달했습니다.' : 'Approved plan handed off.'); await refreshChange();
    });
  }

  async function previewArchive() {
    const preview = await api.archivePreview(state.change.change_id, state.change.revision);
    const target = preview.candidate?.target_paths?.[0] || 'archive';
    const accepted = Object.entries(preview.accepted_commits || {}).map(([ticket, commit]) => `${ticket} · ${commit}`).join('\n');
    showConfirmation('Confirm archive', 'All active scope is explicitly accepted. Archiving mutates only the controlled planning worktree.', `${target}\n\nAccepted evidence:\n${accepted}`, 'Archive reviewed revision', async () => {
      await api.archive(state.change.change_id, state.change.revision, preview.archive_id, preview.confirmation_token);
      ui.announce('Planning change archived in its controlled worktree.'); await load();
    });
  }

  function showConfirmation(title, description, details, actionLabel, action) {
    const previousFocus = document.activeElement;
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': title });
    const close = () => { dialog.remove(); if (previousFocus?.isConnected) previousFocus.focus(); };
    const confirm = button(actionLabel, async () => { confirm.disabled = true; try { await action(); close(); } finally { confirm.disabled = false; } }, 'btn btn-primary');
    dialog.append(ui.el('h2', null, title), ui.el('p', null, description), ui.el('pre', null, details), ui.el('div', { class: 'pm-actions' }, [confirm, button('Cancel', close)]));
    dialog.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
    });
    localizePm(dialog); root.querySelector('.pm-body')?.prepend(dialog); confirm.focus();
  }

  function showFindings(report) {
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Validation results' }, [ui.el('h2', null, report.valid ? 'Ready for review' : 'Needs attention'), renderFindings(ui, report.findings), button('Close', () => dialog.remove())]);
    localizePm(dialog); root.querySelector('.pm-body')?.prepend(dialog); dialog.querySelector('button')?.focus();
  }

  function showProposal(response) {
    const proposal = response.result || response;
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Planning proposal' });
    const apply = limitAction(limitAction(button('Apply proposal', async () => {
      await api.applyPlanning(state.change.change_id, state.change.revision, response.operation_id, proposal.proposal_digest);
      dialog.remove(); await refreshChange(); ui.announce('Proposal applied as a new draft revision.');
    }, 'btn btn-primary'), 'assistance'), 'authoring');
    const proposals = proposal.proposals || (proposal.proposal_patch ? [{ document_id: proposal.document_id, proposal_patch: proposal.proposal_patch }] : []);
    if (!proposals.length) apply.disabled = true;
    const scope = proposal.scope || {};
    const reserved = Object.entries(proposal.reserved_ids || {}).map(([placeholder, permanentId]) => [placeholder, permanentId]);
    const discard = button('Discard proposal', async () => {
      await api.cancelOperation(response.operation_id);
      dialog.remove(); ui.announce('Planning proposal discarded and reserved identities retired.');
    });
    dialog.append(ui.el('h2', null, 'Review proposal'), ui.el('p', null, proposal.explanation || 'This proposal has not changed the plan.'),
      ui.el('dl', { class: 'pm-task-meta' }, [
        ui.el('dt', null, 'Action'), ui.el('dd', null, proposal.action || '—'),
        ui.el('dt', null, 'Target artifact'), ui.el('dd', null, scope.document_id || 'Whole change'),
        ui.el('dt', null, 'Section'), ui.el('dd', null, scope.section_id || '—'),
        ui.el('dt', null, 'Dependency'), ui.el('dd', null, scope.dependency ? `${scope.dependency.task_id} → ${scope.dependency.predecessor_id}` : '—'),
        ui.el('dt', null, 'Impacted artifacts'), ui.el('dd', null, (scope.impacted_document_ids || []).join(', ') || '—'),
        ui.el('dt', null, 'External writes or dispatch'), ui.el('dd', null, proposal.write_or_dispatch_performed ? 'Reported' : 'None performed'),
      ]),
      proposal.blocked_instruction ? ui.el('p', { class: 'pm-status-card danger', role: 'alert' }, `Blocked instruction: ${proposal.blocked_instruction}`) : null,
      reserved.length ? ui.el('section', { class: 'pm-proposal-ids' }, [ui.el('h3', null, 'Reserved permanent identities'), simpleTable(['Placeholder', 'Permanent ID'], reserved)]) : null,
      ...proposals.map((item) => ui.el('section', { class: 'pm-proposal-diff' }, [ui.el('h3', null, item.document_id), ui.el('pre', null, item.proposal_patch || '')])),
      ui.el('div', { class: 'pm-actions' }, [apply, discard]));
    localizePm(dialog); root.querySelector('.pm-body')?.prepend(dialog); apply.focus();
  }

  async function openPlanningDialog(docId) {
    const previousFocus = document.activeElement;
    const planningWbs = state.wbs || await api.wbs(state.change.change_id);
    const dialog = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Planning assistant' });
    const action = ui.el('select', { class: 'input', 'aria-label': 'Planning action' }, PLANNING_ACTIONS.map(([id, label]) => ui.el('option', { value: id, selected: id === 'find_gaps' }, label)));
    const documents = state.change.documents.filter((item) => item.editable);
    const documentSelect = ui.el('select', { class: 'input', 'aria-label': 'Artifact' }, documents.map((item) => ui.el('option', { value: item.document_id, selected: item.document_id === docId }, item.document_id)));
    const section = ui.el('select', { class: 'input', 'aria-label': 'Section' });
    const dependencyOptions = (planningWbs?.tasks || []).flatMap((task) => (task.dependencies || []).map((dependency) => ({ task_id: task.task_id, predecessor_id: dependency.task_id })));
    const dependency = ui.el('select', { class: 'input', 'aria-label': 'Dependency' }, [ui.el('option', { value: '' }, 'Choose dependency'), ...dependencyOptions.map((item) => ui.el('option', { value: `${item.task_id}|${item.predecessor_id}` }, `${item.task_id} needs ${item.predecessor_id}`))]);
    const impacted = ui.el('select', { class: 'input', multiple: true, size: Math.min(6, Math.max(2, documents.length)), 'aria-label': 'Impacted artifacts' }, documents.map((item) => ui.el('option', { value: item.document_id, selected: item.document_id === docId }, item.document_id)));
    const instruction = ui.el('textarea', { class: 'input pm-drawer-textarea', 'aria-label': 'Instructions' });
    const sectionField = field('Section', section), dependencyField = field('Dependency', dependency), impactField = field('Impacted artifacts', impacted);
    const updateSections = () => {
      const prior = section.value;
      section.replaceChildren(ui.el('option', { value: '' }, 'Choose section'), ...(state.change.planning_sections || []).filter((item) => item.document_id === documentSelect.value).map((item) => ui.el('option', { value: item.section_id }, item.label)));
      if ([...section.options].some((item) => item.value === prior)) section.value = prior;
    };
    const updateScope = () => {
      if (action.value === 'propose_wbs' && documents.some((item) => item.document_id === 'tasks')) documentSelect.value = 'tasks';
      updateSections();
      sectionField.hidden = action.value !== 'refine_section';
      dependencyField.hidden = action.value !== 'explain_dependency';
      impactField.hidden = !['review_consistency', 'update_impacted_artifacts', 'find_gaps'].includes(action.value);
    };
    action.addEventListener('change', updateScope); documentSelect.addEventListener('change', updateSections); updateScope();
    const close = () => { dialog.remove(); previousFocus?.focus?.(); };
    const run = button('Prepare proposal', async () => {
      const [taskId, predecessorId] = dependency.value.split('|');
      const request = planningActionRequest(action.value, {
        documentId: documentSelect.value, sectionId: section.value || null,
        dependency: taskId && predecessorId ? { task_id: taskId, predecessor_id: predecessorId } : null,
        impactedDocumentIds: [...impacted.selectedOptions].map((item) => item.value), instruction: instruction.value,
      });
      dialog.remove(); await runPlanning(request);
    }, 'btn btn-primary');
    dialog.append(ui.el('h2', null, 'Planning assistant'),
      field('Action', action), field('Artifact', documentSelect), sectionField, dependencyField, field('Instructions', instruction), impactField,
      ui.el('p', { class: 'pm-empty' }, 'The server reloads the selected revision and enforces allowed targets. Viewing never starts AI.'),
      ui.el('div', { class: 'pm-actions' }, [run, button('Cancel', close)]));
    dialog.addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.preventDefault(); close(); } });
    localizePm(dialog); root.querySelector('.pm-body')?.prepend(dialog); action.focus();
  }

  async function runPlanning(request) {
    const running = ui.el('section', { class: 'pm-inline-dialog', role: 'dialog', 'aria-label': 'Planning assistance' });
    const task = api.startPlanning(state.change.change_id, state.change.revision, request);
    const cancel = button('Cancel proposal', async () => {
      cancel.disabled = true;
      try {
        await api.cancelOperation(task.operationId);
        running.remove(); ui.announce('Planning proposal cancelled.');
      } finally {
        if (running.isConnected) cancel.disabled = false;
      }
    });
    running.append(ui.el('h2', null, 'Preparing a proposal'), ui.el('p', { role: 'status' }, 'The assistant can propose a diff only. Project code and workflow dispatch are unavailable.'), cancel);
    root.querySelector('.pm-body')?.prepend(running); cancel.focus();
    try { const response = await task.promise; if (running.isConnected) { running.remove(); showProposal(response); } }
    catch (error) { if (running.isConnected) { running.remove(); reportActionError(error, cancel); } }
  }

  async function openCreateDialog() {
    const locator = window.prompt('Change locator (lowercase words with hyphens)'); if (!locator) return;
    const title = window.prompt('Change title', locator.replaceAll('-', ' ')); const workspacePath = window.prompt('Existing isolated Git worktree path'); if (!workspacePath) return;
    await api.create(workspacePath, locator, title || locator); await load();
  }

  async function repairWorkspace() {
    if (!state.change) { ui.announce('Choose a change first.'); return; }
    const path = window.prompt('Attached planning worktree path'); if (!path) return;
    await api.repair(state.change.change_id, path); await refreshChange(); ui.announce('Workspace claim repaired and reindexed.');
  }

  async function convertLegacy() {
    const sourceTicket = window.prompt('Legacy ticket identifier'); if (!sourceTicket) return;
    const workspacePath = window.prompt('Attached planning worktree path'); if (!workspacePath) return;
    const locator = window.prompt('New change locator'); if (!locator) return;
    await api.convertLegacy(sourceTicket, workspacePath, locator, locator.replaceAll('-', ' ')); await load(); ui.announce('Legacy plan converted into a new planning change.');
  }

  function buildWelcome() { return ui.el('section', { class: 'pm-welcome' }, [ui.el('div', { class: 'pm-welcome-mark' }, 'A'), ui.el('h2', null, 'Choose or create a planning change'), ui.el('p', null, 'Planning remains isolated until explicit approval and handoff.')]); }
  function empty(text) { return ui.el('section', { class: 'pm-empty-state' }, [ui.el('p', null, text)]); }
  function field(label, control) { return ui.el('label', { class: 'pm-drawer-field' }, [ui.el('span', null, label), control]); }
  function button(label, handler, className = 'btn') {
    const item = ui.el('button', { class: className, type: 'button' }, label);
    item.addEventListener('click', async (event) => {
      try { await handler(event); } catch (error) { reportActionError(error, item); }
    });
    return item;
  }
  function segmented(items, active, handler) { return ui.el('div', { class: 'pm-segmented', role: 'tablist' }, items.map(([value, label]) => { const item = button(label, () => handler(value), `pm-segment ${active === value ? 'active' : ''}`); item.setAttribute('role', 'tab'); item.setAttribute('aria-selected', String(active === value)); return item; })); }
  function simpleTable(headers, rows) { return ui.el('div', { class: 'pm-table-scroll' }, ui.el('table', { class: 'pm-compact-table' }, [ui.el('thead', null, ui.el('tr', null, headers.map((name) => ui.el('th', { scope: 'col' }, name)))), ui.el('tbody', null, rows.map((row) => ui.el('tr', null, row.map((value) => ui.el('td', null, String(value ?? '—'))))))])); }

  load().catch((error) => { if (error.name !== 'AbortError') root.replaceChildren(ui.el('div', { class: 'banner banner-danger', role: 'alert' }, error.message)); });
  return { unmount: () => { state.dispose(); root.classList.remove('pm-workspace-host'); }, beforeProjectSwitch: () => guard('Discard unsaved PM Workspace edits and switch projects?'), isDirty: () => state.dirty };
}

function extractSection(source, name) {
  const aliases = { ux: ['ux', 'experience'], storyboard: ['storyboard', 'flow'], architecture: ['architecture'], erd: ['erd', 'data model'], api: ['api'], integration: ['integration'] }[name] || [name];
  const lines = String(source || '').split('\n'); let start = -1, level = 0, end = lines.length;
  for (let i = 0; i < lines.length; i += 1) { const match = lines[i].match(/^(#{1,6})\s+(.+)/); if (match && aliases.some((item) => match[2].toLowerCase().includes(item))) { start = i; level = match[1].length; break; } }
  if (start < 0) return '';
  for (let i = start + 1; i < lines.length; i += 1) { const match = lines[i].match(/^(#{1,6})\s+/); if (match && match[1].length <= level) { end = i; break; } }
  return lines.slice(start, end).join('\n');
}
function diagramBlocks(source) { return [...String(source || '').matchAll(/```(?:mermaid)?\s*\n([\s\S]*?)```/gi)].map((match) => match[1]).filter((item) => /^\s*(flowchart|graph|erDiagram)\b/.test(item)); }
function lineDelta(base, proposed) {
  const left = new Set(String(base || '').split('\n')); const right = new Set(String(proposed || '').split('\n'));
  return { added: [...right].filter((line) => !left.has(line)).length, removed: [...left].filter((line) => !right.has(line)).length };
}
function fileToBase64(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || ''); reader.onerror = () => reject(reader.error); reader.readAsDataURL(file); }); }
function parseTsvValue(field, value) {
  if (field === 'order') { const number = Number(value); if (!Number.isInteger(number)) throw new Error('Order must be an integer.'); return number; }
  if (['acceptance', 'verification', 'depends_on', 'requirements', 'storyboards', 'design_refs'].includes(field)) {
    try { const parsed = JSON.parse(value || '[]'); if (!Array.isArray(parsed)) throw new Error(); return parsed; } catch (_) { throw new Error(`${field} must contain a JSON array.`); }
  }
  return value === '' && ['parent_id', 'human_owner', 'priority', 'estimate', 'target_date'].includes(field) ? null : value;
}

export function moveOperations(tasks, taskId, direction) {
  const selected = tasks.find((item) => item.task_id === taskId);
  if (!selected) throw new Error('Unknown selected task.');
  const siblings = tasks.filter((item) => item.parent_id === selected.parent_id).sort((a, b) => a.order - b.order);
  const index = siblings.findIndex((item) => item.task_id === taskId);
  const other = siblings[index + (direction === 'up' ? -1 : 1)];
  if (!other) throw new Error('Task cannot move farther.');
  return {
    operations: [{ op: 'set_field', task_id: selected.task_id, field: 'order', value: other.order }, { op: 'set_field', task_id: other.task_id, field: 'order', value: selected.order }],
    inverse: [{ op: 'set_field', task_id: selected.task_id, field: 'order', value: selected.order }, { op: 'set_field', task_id: other.task_id, field: 'order', value: other.order }],
  };
}

export function chooseAvailableChange(changes, currentId = '', requestedId = '') {
  return changes.find((item) => item.change_id === currentId)
    || changes.find((item) => item.change_id === requestedId)
    || changes[0]
    || null;
}

export function directHistoryChangeId(changes, requestedId = '') {
  return requestedId && !changes.some((item) => item.change_id === requestedId)
    ? requestedId : '';
}

export function withProjectIdentity(path, projectId) {
  const value = String(path || '');
  if (!projectId || /[?&]project_id=/.test(value)) return value;
  return `${value}${value.includes('?') ? '&' : '?'}project_id=${encodeURIComponent(projectId)}`;
}

export function parseTsv(text) {
  const rows = []; let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted && char === '"' && text[i + 1] === '"') { cell += '"'; i += 1; }
    else if (char === '"') quoted = !quoted;
    else if (!quoted && char === '\t') { row.push(cell); cell = ''; }
    else if (!quoted && (char === '\n' || char === '\r')) { if (char === '\r' && text[i + 1] === '\n') i += 1; row.push(cell); if (row.some((value) => value !== '')) rows.push(row); row = []; cell = ''; }
    else cell += char;
  }
  if (quoted) throw new Error('TSV contains an unclosed quoted field.');
  row.push(cell); if (row.some((value) => value !== '')) rows.push(row);
  if (rows.some((item) => item.length < 3)) throw new Error('Every TSV row needs Task ID, field, and value.');
  return rows;
}

export function serializeWbsTsv(tasks) {
  const quote = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`;
  const fields = [['parent_id', 'parent_id'], ['order', 'order'], ['type', 'kind'], ['title', 'title'], ['human_owner', 'human_owner'], ['agent_kind', 'agent_kind_override'], ['priority', 'priority'], ['estimate', 'estimate'], ['target_date', 'target_date'], ['description', 'description'], ['acceptance', 'acceptance'], ['verification', 'verification'], ['depends_on', 'dependencies'], ['requirements', 'requirement_refs'], ['storyboards', 'storyboard_refs'], ['design_refs', 'design_refs'], ['integration_impact', 'integration_impact'], ['risk', 'risk'], ['notes', 'notes']];
  const rows = tasks.flatMap((item) => fields.map(([field, key]) => [item.task_id, field, typeof item[key] === 'object' ? JSON.stringify(item[key]) : item[key] ?? '']));
  return [['Task ID', 'Field', 'Value'], ...rows].map((row) => row.map(quote).join('\t')).join('\n');
}

export function applyUnifiedDiff(source, patch) {
  const input = String(source).split('\n'); const output = []; let cursor = 0; const lines = String(patch || '').split('\n'); let i = 0, sawHunk = false;
  while (i < lines.length) {
    const header = lines[i].match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/); if (!header) { i += 1; continue; }
    sawHunk = true; const start = Number(header[1]) - 1; output.push(...input.slice(cursor, start)); cursor = start; i += 1;
    while (i < lines.length && !lines[i].startsWith('@@ ')) {
      const line = lines[i]; if (line.startsWith('\\ No newline')) { i += 1; continue; }
      const mark = line[0], value = line.slice(1);
      if (mark === ' ' || mark === '-') { if (input[cursor] !== value) throw new Error('Proposal no longer matches this revision.'); if (mark === ' ') output.push(value); cursor += 1; }
      else if (mark === '+') output.push(value); else if (!line.startsWith('---') && !line.startsWith('+++')) break;
      i += 1;
    }
  }
  if (!sawHunk) throw new Error('Proposal does not contain an applicable unified diff.');
  output.push(...input.slice(cursor)); return output.join('\n');
}

const KO = new Map(Object.entries({
  'Product planning': '제품 기획', 'Artifacts': '기획 산출물',
  'Shape the idea, align requirements, and hand off executable work.': '아이디어를 구체화하고 요구사항을 맞춘 뒤 실행 가능한 작업으로 전달합니다.',
  'Idea': '아이디어', 'Requirements': '요구사항', 'Design': '설계', 'Plan': '계획', 'Code Files': '코드 파일',
  'New change': '새 변경', 'Repair workspace': '작업공간 복구', 'Convert legacy plan': '기존 계획 변환',
  'Add capability': '기능 요구사항 추가', 'Back to requirements': '요구사항으로 돌아가기', 'Edit requirements': '요구사항 편집',
  'Add decision': '결정 추가', 'Add storyboard': '스토리보드 추가', 'Add contract': '계약 추가', 'Add requirement': '요구사항 추가', 'Add scenario': '시나리오 추가',
  'Edit in external editor': '외부 편집기로 열기', 'Finish external edit & reindex': '외부 편집 종료 및 재색인',
  'Validate': '검증', 'Approve revision': '리비전 승인', 'Edit source': '소스 편집', 'Review document': '문서 검토',
  'Readable view': '읽기 보기', 'Source': '소스', 'Save draft': '초안 저장', 'Find gaps': '누락 찾기', 'Planning assistant': '기획 도우미',
  'Draft artifact': '산출물 초안', 'Refine selected section': '선택 구역 다듬기', 'Review consistency': '일관성 검토', 'Explain dependency': '의존성 설명', 'Propose WBS': 'WBS 제안', 'Update impacted artifacts': '영향받는 산출물 업데이트',
  'Action': '작업', 'Planning action': '기획 작업', 'Artifact': '산출물', 'Section': '구역', 'Choose section': '구역 선택', 'Dependency': '의존성', 'Choose dependency': '의존성 선택',
  'Instructions': '지시사항', 'Impacted artifacts': '영향받는 산출물', 'Prepare proposal': '제안 준비', 'Cancel proposal': '제안 취소', 'Cancel': '취소',
  'Preparing a proposal': '제안 준비 중', 'Review proposal': '제안 검토', 'Apply proposal': '제안 적용', 'Discard proposal': '제안 폐기',
  'UX': '사용자 경험', 'Storyboard': '스토리보드', 'Architecture': '아키텍처', 'ERD': 'ERD', 'API': 'API', 'Integration': '통합',
  'Work breakdown': '작업 분해', 'Dependencies': '의존성', 'Plan document': '계획 문서',
  'Preview archive': '아카이브 미리보기', 'Preview approved handoff': '승인된 전달 미리보기',
  'Validation results': '검증 결과', 'Ready for review': '검토 준비 완료', 'Needs attention': '조치 필요', 'No findings.': '발견된 문제가 없습니다.', 'Close': '닫기',
  'Confirm native handoff': '네이티브 전달 확인', 'Create native tickets': '네이티브 티켓 생성',
  'Add group': '그룹 추가', 'Add root': '최상위 추가', 'Add sibling': '같은 단계 추가', 'Add child': '하위 추가', 'Indent': '들여쓰기', 'Outdent': '내어쓰기',
  'Move up': '위로', 'Move down': '아래로', 'Delete': '삭제', 'Undo': '실행 취소', 'Redo': '다시 실행', 'Copy TSV': 'TSV 복사',
  'Workbook exchange': '워크북 교환', 'Download export': '내보내기 다운로드', 'Preview import': '가져오기 미리보기',
  'Export format': '내보내기 형식', 'Excel workbook': 'Excel 워크북', 'Print HTML': '인쇄용 HTML', 'Planning package': '기획 패키지', 'Choose XLSX workbook': 'XLSX 워크북 선택',
  'Export a revision-bound workbook or preview a three-way import. Nothing is applied from preview.': '리비전에 연결된 워크북을 내보내거나 3방향 가져오기를 미리 봅니다. 미리보기만으로는 변경이 적용되지 않습니다.',
  'Apply reviewed import': '검토한 가져오기 적용', '{count} proposed changes': '제안된 변경 {count}개', '{count} conflicts require resolution': '해결이 필요한 충돌 {count}개',
  'Item': '항목', 'Field': '필드', 'Base': '기준', 'Current': '현재', 'Workbook': '워크북', 'Resolution': '해결',
  'Resolve {key}': '{key} 해결', 'Choose…': '선택…', 'Keep current': '현재 값 유지', 'Use workbook': '워크북 사용',
  'Choose a workbook first.': '먼저 워크북을 선택하세요.', 'Choose an XLSX workbook with a plain filename.': '경로가 없는 일반 파일명의 XLSX 워크북을 선택하세요.', 'Only XLSX workbooks are supported.': 'XLSX 워크북만 지원됩니다.',
  'Reviewed workbook changes applied.': '검토한 워크북 변경을 적용했습니다.', 'Approve this exact planning revision locally?': '이 정확한 기획 리비전을 로컬에서 승인하시겠습니까?', 'Revision approved.': '리비전을 승인했습니다.',
  'Type': '유형', 'Human owner': '담당자', 'Agent override': '에이전트 지정', 'Priority': '우선순위', 'Target date': '목표 날짜', 'Estimate': '예상치',
  'Description': '설명', 'Acceptance': '인수 조건', 'Verification': '검증', 'Dependencies': '의존성', 'Requirement references': '요구사항 참조', 'Storyboard references': '스토리보드 참조', 'Design references': '설계 참조', 'Integration impact': '통합 영향', 'Risk': '위험', 'Notes': '메모',
  'Planning files': '기획 파일', 'Handoff tickets': '전달 티켓', 'Review verification': '검증 검토', 'Accepted': '승인됨', 'Run verification': '검증 실행', 'Submit completion evidence': '완료 증거 제출', 'Host executed': '호스트 실행', 'Manual reported': '수동 보고', 'Mixed': '혼합',
  'Tested commit': '검증 커밋', 'Environment summary': '환경 요약', 'Manual result': '수동 결과', 'Started at': '시작 시각', 'Completed at': '완료 시각', 'Report path': '보고서 경로', 'Artifact references': '증거 자료 참조',
  'Preview host verification': '호스트 검증 미리보기', 'Preview evidence': '증거 미리보기', 'Run approved command': '승인된 명령 실행', 'Submit evidence': '증거 제출', 'Acceptance review': '인수 검토', 'Accept verified revision': '검증된 리비전 인수',
  'Requirement and scenario coverage': '요구사항 및 시나리오 커버리지', 'Waive coverage': '커버리지 면제', 'Revoke waiver': '면제 취소', 'Preview waiver': '면제 미리보기',
  'Waiver reason': '면제 사유', 'Reviewer label': '검토자 이름', 'Confirm waiver': '면제 확인',
  'Proposed': '변경안', 'Current baseline': '현재 기준', 'Source revision': '소스 리비전',
  'External editing is active': '외부 편집 중', 'Choose or create a planning change': '기획 변경을 선택하거나 만드세요',
}));

export function pmText(value, values = {}, language = globalThis.window?.i18n?.lang || 'en') {
  let result = language === 'ko' ? (KO.get(value) || value) : value;
  for (const [key, replacement] of Object.entries(values)) result = result.replaceAll(`{${key}}`, String(replacement));
  return result;
}

function localizePm(scope) {
  if (window.i18n?.lang !== 'ko') return;
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const raw = node.nodeValue; const trimmed = raw.trim(); const replacement = pmText(trimmed);
    if (replacement !== trimmed) node.nodeValue = raw.replace(trimmed, replacement);
  }
  const labelled = [...scope.querySelectorAll('[aria-label]')];
  if (scope.matches?.('[aria-label]')) labelled.unshift(scope);
  for (const item of labelled) {
    const current = item.getAttribute('aria-label'); const replacement = pmText(current); if (replacement !== current) item.setAttribute('aria-label', replacement);
  }
}
