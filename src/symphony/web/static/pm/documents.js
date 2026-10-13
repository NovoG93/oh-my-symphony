export function sourceEditPolicy(source) {
  return /\r/.test(String(source || ''))
    ? { editable: false, reason: 'This document uses CRLF or mixed newlines. Use External edit mode to preserve exact bytes.' }
    : { editable: true, reason: '' };
}

export function renderDocumentEditor({ ui, document, error, draft, onSave, onAssist, onDirty, onDraft, authoring = { effective: true, reasons: [] }, assistance = { effective: true, reasons: [] }, onError = () => {}, translate = (value) => value }) {
  const { el, renderMarkdown } = ui;
  if (error) return el('section', { class: 'pm-error-state', role: 'alert' }, [el('h2', null, 'Document unavailable'), el('p', null, error)]);
  if (!document) return el('div', { class: 'pm-loading', role: 'status' }, 'Loading document…');
  if (document.unsupported_reason) return el('section', { class: 'pm-error-state', role: 'status' }, [el('h2', null, 'Read-only document'), el('p', null, document.unsupported_reason), el('pre', null, document.source || '')]);
  const initial = draft === null ? document.source : draft;
  const editPolicy = sourceEditPolicy(document.source);
  let changed = false;
  const source = el('textarea', {
    class: 'pm-source-editor',
    'aria-label': `${document.document_id} source`,
    spellcheck: true,
  });
  source.value = initial;
  source.disabled = !editPolicy.editable;
  const preview = el('article', { class: 'pm-document-preview' }, renderMarkdown(initial));
  const sourcePane = el('section', { class: 'pm-editor-pane', hidden: true });
  const reviewPane = el('section', { class: 'pm-preview-pane pm-preview-readable' }, [el('h2', null, 'Readable view'), preview]);
  const toggle = el('button', { class: 'btn', type: 'button', 'aria-pressed': 'false' }, translate('Edit source'));
  const save = el('button', { class: 'btn btn-primary', type: 'button' }, 'Save draft');
  const assist = el('button', { class: 'btn', type: 'button' }, 'Planning assistant');
  save.disabled = true;
  if (!authoring.effective) { save.title = (authoring.reasons || []).join(' '); save.dataset.capability = 'authoring'; }
  else if (!editPolicy.editable) save.title = editPolicy.reason;
  if (!assistance.effective) { assist.disabled = true; assist.title = (assistance.reasons || []).join(' '); assist.dataset.capability = 'assistance'; }
  source.addEventListener('input', () => {
    changed = source.value !== initial; save.disabled = !changed || !authoring.effective;
    onDirty(changed);
    onDraft?.(source.value);
    preview.replaceChildren(renderMarkdown(source.value));
  });
  save.addEventListener('click', async () => {
    if (!changed || !editPolicy.editable) return;
    save.disabled = true;
    try { await onSave(source.value); changed = false; onDirty(false); }
    catch (error) { onError(error, save); }
    finally { save.disabled = !changed || !authoring.effective; }
  });
  assist.addEventListener('click', async () => {
    try { await onAssist(source.value); } catch (error) { onError(error, assist); }
  });
  toggle.addEventListener('click', () => {
    const editing = sourcePane.hidden;
    sourcePane.hidden = !editing;
    reviewPane.hidden = editing;
    toggle.textContent = translate(editing ? 'Review document' : 'Edit source');
    toggle.setAttribute('aria-pressed', String(editing));
    if (editing) source.focus();
  });
  sourcePane.append(el('div', { class: 'pm-pane-heading' }, [el('h2', null, 'Source'), el('div', { class: 'pm-actions' }, [assist, save])]));
  if (!editPolicy.editable) sourcePane.append(el('p', { class: 'pm-status-card', role: 'status' }, editPolicy.reason));
  sourcePane.append(source);
  return el('div', { class: 'pm-document-layout' }, [
    el('div', { class: 'pm-view-switcher' }, [toggle, draft !== null && draft !== document.source ? el('span', { class: 'pm-draft-badge' }, 'Recovered local draft') : null]),
    sourcePane, reviewPane,
  ]);
}

export function renderFindings(ui, findings = []) {
  if (!findings.length) return ui.el('p', { class: 'pm-empty' }, 'No findings.');
  return ui.el('ul', { class: 'pm-findings' }, findings.map((finding) =>
    ui.el('li', { class: `pm-finding ${finding.severity}` }, [
      ui.el('strong', null, finding.code),
      ui.el('span', null, finding.message),
    ])
  ));
}
