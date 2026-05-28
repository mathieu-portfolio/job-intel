(() => {
  const form = document.querySelector('#profile-editor');
  if (!form) return;

  const payloadInput = document.querySelector('#profile-payload');
  const queryRoot = document.querySelector('#query-categories');
  const mustMatchRoot = document.querySelector('#must-match-items');
  const signalRoot = document.querySelector('#signal-categories');
  const state = JSON.parse(form.dataset.profile || '{}');

  state.search_queries ||= {};
  state.must_match ||= { any: [] };
  state.must_match.any ||= [];
  state.signals ||= {};

  const clean = (value) => String(value || '').trim();
  const parseAliases = (value) => {
    const text = clean(value);
    if (!text) return {};
    try {
      const parsed = JSON.parse(text);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch (_) {
      return {};
    }
  };

  const itemTemplate = (item = {}, { weighted = false } = {}) => {
    const row = document.createElement('div');
    row.className = 'profile-item-row';
    row.innerHTML = `
      <label class="profile-term-label">
        Term
        <input data-role="term" type="text" value="${escapeHtml(item.term || '')}" placeholder="C++, simulation, remote...">
      </label>
      ${weighted ? `
        <label class="profile-weight-label">
          Weight <span data-role="weight-value">${Number(item.weight ?? 1).toFixed(2)}</span>
          <input data-role="weight" type="range" min="0" max="2" step="0.05" value="${Number(item.weight ?? 1)}">
        </label>` : ''}
      <details class="profile-aliases">
        <summary>Aliases</summary>
        <textarea data-role="aliases" rows="4" placeholder='{"en":["modern C++"],"fr":["C++ moderne"]}'>${escapeHtml(JSON.stringify(item.aliases || {}, null, 2))}</textarea>
      </details>
      <button type="button" class="danger subtle" data-remove-item>Remove</button>
    `;
    const weight = row.querySelector('[data-role="weight"]');
    const weightValue = row.querySelector('[data-role="weight-value"]');
    if (weight && weightValue) {
      weight.addEventListener('input', () => {
        weightValue.textContent = Number(weight.value || 0).toFixed(2);
        syncPayload();
      });
    }
    row.querySelector('[data-remove-item]').addEventListener('click', () => {
      row.remove();
      syncPayload();
    });
    row.addEventListener('input', syncPayload);
    return row;
  };

  function escapeHtml(value) {
    return String(value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;');
  }

  function queryCategoryTemplate(name = '', values = []) {
    const card = document.createElement('article');
    card.className = 'profile-category-card';
    card.innerHTML = `
      <div class="profile-category-head">
        <label>
          Category
          <input data-role="category-name" type="text" value="${escapeHtml(name)}" placeholder="en, fr, cpp...">
        </label>
        <div class="profile-category-actions">
          <button type="button" class="subtle" data-add-query>Add query</button>
          <button type="button" class="danger subtle" data-remove-category>Remove category</button>
        </div>
      </div>
      <div class="profile-items"></div>
    `;
    const items = card.querySelector('.profile-items');
    for (const value of values) items.append(nonWeightedQueryRow(value));
    if (!values.length) items.append(nonWeightedQueryRow(''));
    card.querySelector('[data-add-query]').addEventListener('click', () => {
      items.append(nonWeightedQueryRow(''));
      syncPayload();
    });
    card.querySelector('[data-remove-category]').addEventListener('click', () => {
      card.remove();
      syncPayload();
    });
    card.addEventListener('input', syncPayload);
    return card;
  }

  function nonWeightedQueryRow(value = '') {
    const row = document.createElement('div');
    row.className = 'profile-item-row compact';
    row.innerHTML = `
      <label class="profile-term-label">
        Query
        <input data-role="query" type="text" value="${escapeHtml(value)}" placeholder="software engineer">
      </label>
      <button type="button" class="danger subtle" data-remove-item>Remove</button>
    `;
    row.querySelector('[data-remove-item]').addEventListener('click', () => {
      row.remove();
      syncPayload();
    });
    row.addEventListener('input', syncPayload);
    return row;
  }

  function signalCategoryTemplate(name = '', category = { items: [] }) {
    const card = document.createElement('article');
    card.className = 'profile-category-card';
    card.innerHTML = `
      <div class="profile-category-head">
        <label>
          Category
          <input data-role="category-name" type="text" value="${escapeHtml(name)}" placeholder="interests, strengths...">
        </label>
        <div class="profile-category-actions">
          <button type="button" class="subtle" data-add-signal>Add field</button>
          <button type="button" class="danger subtle" data-remove-category>Remove category</button>
        </div>
      </div>
      <div class="profile-items"></div>
    `;
    const items = card.querySelector('.profile-items');
    for (const item of category.items || []) items.append(itemTemplate(item, { weighted: true }));
    if (!(category.items || []).length) items.append(itemTemplate({}, { weighted: true }));
    card.querySelector('[data-add-signal]').addEventListener('click', () => {
      items.append(itemTemplate({}, { weighted: true }));
      syncPayload();
    });
    card.querySelector('[data-remove-category]').addEventListener('click', () => {
      card.remove();
      syncPayload();
    });
    card.addEventListener('input', syncPayload);
    return card;
  }

  function readSignalItem(row, weighted) {
    const term = clean(row.querySelector('[data-role="term"]')?.value);
    if (!term) return null;
    const item = { term };
    if (weighted) item.weight = Number(row.querySelector('[data-role="weight"]')?.value || 1);
    const aliases = parseAliases(row.querySelector('[data-role="aliases"]')?.value);
    if (Object.keys(aliases).length) item.aliases = aliases;
    return item;
  }

  function syncPayload() {
    for (const input of form.querySelectorAll('[data-profile-field]')) {
      const key = input.dataset.profileField;
      if (input.type === 'number') {
        const value = input.value === '' ? undefined : Number(input.value);
        if (value === undefined || Number.isNaN(value)) delete state[key];
        else state[key] = value;
      } else {
        const value = clean(input.value);
        if (value) state[key] = value;
        else delete state[key];
      }
    }

    const searchQueries = {};
    for (const card of queryRoot.querySelectorAll('.profile-category-card')) {
      const name = clean(card.querySelector('[data-role="category-name"]')?.value);
      if (!name) continue;
      const values = Array.from(card.querySelectorAll('[data-role="query"]')).map((input) => clean(input.value)).filter(Boolean);
      if (values.length) searchQueries[name] = values;
    }
    state.search_queries = searchQueries;

    state.must_match = { any: Array.from(mustMatchRoot.querySelectorAll('.profile-item-row')).map((row) => readSignalItem(row, true)).filter(Boolean) };

    const signals = {};
    for (const card of signalRoot.querySelectorAll('.profile-category-card')) {
      const name = clean(card.querySelector('[data-role="category-name"]')?.value);
      if (!name) continue;
      const items = Array.from(card.querySelectorAll('.profile-item-row')).map((row) => readSignalItem(row, true)).filter(Boolean);
      if (items.length) signals[name] = { items };
    }
    state.signals = signals;

    payloadInput.value = JSON.stringify(state);
  }

  function hydrateMeta() {
    for (const input of form.querySelectorAll('[data-profile-field]')) {
      const value = state[input.dataset.profileField];
      if (value !== undefined && value !== null) input.value = value;
      input.addEventListener('input', syncPayload);
    }
  }

  function render() {
    hydrateMeta();
    queryRoot.innerHTML = '';
    for (const [name, values] of Object.entries(state.search_queries || {})) queryRoot.append(queryCategoryTemplate(name, values));
    if (!queryRoot.children.length) queryRoot.append(queryCategoryTemplate('en', []));

    mustMatchRoot.innerHTML = '';
    for (const item of state.must_match?.any || []) mustMatchRoot.append(itemTemplate(item, { weighted: true }));
    if (!mustMatchRoot.children.length) mustMatchRoot.append(itemTemplate({}, { weighted: true }));

    signalRoot.innerHTML = '';
    for (const [name, category] of Object.entries(state.signals || {})) signalRoot.append(signalCategoryTemplate(name, category));
    if (!signalRoot.children.length) signalRoot.append(signalCategoryTemplate('interests', { items: [] }));
    syncPayload();
  }

  document.querySelector('[data-add-query-category]')?.addEventListener('click', () => {
    queryRoot.append(queryCategoryTemplate('', []));
  });
  document.querySelector('[data-add-must-match]')?.addEventListener('click', () => {
    mustMatchRoot.append(itemTemplate({}, { weighted: true }));
  });
  document.querySelector('[data-add-signal-category]')?.addEventListener('click', () => {
    signalRoot.append(signalCategoryTemplate('', { items: [] }));
  });
  form.addEventListener('submit', syncPayload);
  render();
})();
