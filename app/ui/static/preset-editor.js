(() => {
  const form = document.querySelector('#preset-editor');
  if (!form) return;

  const payloadInput = document.querySelector('#preset-payload');
  const state = JSON.parse(form.dataset.preset || '{}');
  state.weights ||= {};
  state.weights.category_weights ||= {};
  state.weights.positive_terms ||= {};
  state.weights.negative_terms ||= {};
  state.weights.cumulative_categories ||= [];
  state.weights.exclusive_categories ||= ['location', 'location_preferences'];

  const clean = (value) => String(value || '').trim();
  const numberOr = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const escapeHtml = (value) => String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');

  function weightedRow(name = '', weight = 0, options = {}) {
    const row = document.createElement('div');
    row.className = 'profile-item-row compact preset-weight-row';
    const min = options.min ?? -1;
    const max = options.max ?? 1;
    const step = options.step ?? 0.05;
    row.innerHTML = `
      <label class="profile-term-label">
        ${options.label || 'Field'}
        <input data-role="name" type="text" value="${escapeHtml(name)}" placeholder="interests, compensation...">
      </label>
      <label class="profile-weight-label">
        Weight <span data-role="weight-value">${Number(weight).toFixed(2)}</span>
        <input data-role="weight" type="range" min="${min}" max="${max}" step="${step}" value="${Number(weight)}">
      </label>
      <button type="button" class="danger subtle" data-remove-item>Remove</button>
    `;
    const slider = row.querySelector('[data-role="weight"]');
    const output = row.querySelector('[data-role="weight-value"]');
    slider.addEventListener('input', () => {
      output.textContent = Number(slider.value || 0).toFixed(2);
      syncPayload();
    });
    row.querySelector('[data-remove-item]').addEventListener('click', () => {
      row.remove();
      syncPayload();
    });
    row.addEventListener('input', syncPayload);
    return row;
  }

  function plainRow(value = '', placeholder = 'category') {
    const row = document.createElement('div');
    row.className = 'profile-item-row compact';
    row.innerHTML = `
      <label class="profile-term-label">
        Category
        <input data-role="name" type="text" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}">
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

  function hydrateMeta() {
    for (const input of form.querySelectorAll('[data-preset-field]')) {
      const key = input.dataset.presetField;
      const value = state[key];
      if (input.type === 'checkbox') input.checked = Boolean(value);
      else if (value !== undefined && value !== null) input.value = value;
      input.addEventListener('input', syncPayload);
      input.addEventListener('change', syncPayload);
    }
    for (const input of form.querySelectorAll('[data-weight-field]')) {
      const key = input.dataset.weightField;
      const value = state.weights[key];
      if (value !== undefined && value !== null) input.value = value;
      input.addEventListener('input', syncPayload);
      input.addEventListener('change', syncPayload);
    }
  }

  function readWeightedMap(root) {
    const output = {};
    for (const row of root.querySelectorAll('.profile-item-row')) {
      const name = clean(row.querySelector('[data-role="name"]')?.value);
      if (!name) continue;
      output[name] = numberOr(row.querySelector('[data-role="weight"]')?.value, 0);
    }
    return output;
  }

  function readStringList(root) {
    return Array.from(root.querySelectorAll('[data-role="name"]'))
      .map((input) => clean(input.value))
      .filter(Boolean);
  }

  function syncPayload() {
    for (const input of form.querySelectorAll('[data-preset-field]')) {
      const key = input.dataset.presetField;
      if (input.type === 'checkbox') {
        state[key] = input.checked;
      } else if (input.type === 'number') {
        state[key] = numberOr(input.value, key === 'order' ? 100 : 0);
      } else {
        state[key] = clean(input.value);
      }
    }

    for (const input of form.querySelectorAll('[data-weight-field]')) {
      state.weights[input.dataset.weightField] = numberOr(input.value, 0);
    }

    state.weights.category_weights = readWeightedMap(document.querySelector('#category-weights'));
    state.weights.positive_terms = readWeightedMap(document.querySelector('#positive-terms'));
    state.weights.negative_terms = readWeightedMap(document.querySelector('#negative-terms'));
    state.weights.cumulative_categories = readStringList(document.querySelector('#cumulative-categories'));
    state.weights.exclusive_categories = readStringList(document.querySelector('#exclusive-categories'));

    payloadInput.value = JSON.stringify(state);
  }

  function renderWeightedMap(root, values, options = {}) {
    root.innerHTML = '';
    for (const [name, weight] of Object.entries(values || {})) {
      root.append(weightedRow(name, weight, options));
    }
    if (!root.children.length) root.append(weightedRow('', options.defaultWeight ?? 0, options));
  }

  function renderStringList(root, values) {
    root.innerHTML = '';
    for (const value of values || []) root.append(plainRow(value));
    if (!root.children.length) root.append(plainRow(''));
  }

  function render() {
    hydrateMeta();
    renderWeightedMap(document.querySelector('#category-weights'), state.weights.category_weights, { label: 'Category', min: -1, max: 1, step: 0.05 });
    renderWeightedMap(document.querySelector('#positive-terms'), state.weights.positive_terms, { label: 'Term', min: 0, max: 2, step: 0.05, defaultWeight: 1 });
    renderWeightedMap(document.querySelector('#negative-terms'), state.weights.negative_terms, { label: 'Term', min: -2, max: 0, step: 0.05, defaultWeight: -1 });
    renderStringList(document.querySelector('#cumulative-categories'), state.weights.cumulative_categories);
    renderStringList(document.querySelector('#exclusive-categories'), state.weights.exclusive_categories);
    syncPayload();
  }

  document.querySelector('[data-add-category-weight]')?.addEventListener('click', () => {
    document.querySelector('#category-weights').append(weightedRow('', 0, { label: 'Category', min: -1, max: 1, step: 0.05 }));
  });
  document.querySelector('[data-add-positive-term]')?.addEventListener('click', () => {
    document.querySelector('#positive-terms').append(weightedRow('', 1, { label: 'Term', min: 0, max: 2, step: 0.05 }));
  });
  document.querySelector('[data-add-negative-term]')?.addEventListener('click', () => {
    document.querySelector('#negative-terms').append(weightedRow('', -1, { label: 'Term', min: -2, max: 0, step: 0.05 }));
  });
  document.querySelector('[data-add-cumulative]')?.addEventListener('click', () => {
    document.querySelector('#cumulative-categories').append(plainRow(''));
  });
  document.querySelector('[data-add-exclusive]')?.addEventListener('click', () => {
    document.querySelector('#exclusive-categories').append(plainRow(''));
  });
  form.addEventListener('submit', syncPayload);
  render();
})();
