(() => {
  const storage = window.localStorage;

  const readPrefs = (key) => {
    try {
      return JSON.parse(storage.getItem(key) || "{}");
    } catch (_) {
      return {};
    }
  };

  const writePrefs = (key, prefs) => {
    try {
      storage.setItem(key, JSON.stringify(prefs));
    } catch (_) {
      // Ignore storage quota / privacy-mode failures; the UI still works with defaults.
    }
  };

  const isPersistableField = (field) => {
    if (!field.name) return false;
    if (["submit", "button", "reset", "file", "hidden"].includes(field.type)) return false;
    return true;
  };

  const fieldValue = (field) => {
    if (field.type === "checkbox") return field.checked ? "true" : "false";
    if (field.type === "radio") return field.checked ? field.value : undefined;
    return field.value;
  };

  const setFieldValue = (field, value) => {
    if (value === undefined || value === null) return;
    if (field.type === "checkbox") {
      field.checked = value === true || value === "true";
      return;
    }
    if (field.type === "radio") {
      field.checked = field.value === value;
      return;
    }
    field.value = value;
  };

  const formValues = (form) => {
    const values = {};
    for (const field of form.elements) {
      if (!isPersistableField(field)) continue;
      const value = fieldValue(field);
      if (value !== undefined) values[field.name] = value;
    }
    return values;
  };

  const hasQueryParams = () => new URLSearchParams(window.location.search).toString().length > 0;

  const syncUrlWithPrefs = (form, prefs) => {
    if (form.dataset.syncQuery !== "true") return;
    if (hasQueryParams()) return;
    if (!Object.keys(prefs).length) return;

    const params = new URLSearchParams();
    for (const [name, value] of Object.entries(prefs)) {
      if (value === undefined || value === null || value === "") continue;
      params.set(name, String(value));
    }
    const query = params.toString();
    if (!query) return;

    const action = form.getAttribute("action") || window.location.pathname;
    window.location.replace(`${action}?${query}`);
  };

  const saveForm = (form) => {
    const key = form.dataset.persistKey;
    if (!key) return;
    writePrefs(key, formValues(form));
  };

  for (const form of document.querySelectorAll("form[data-persist-key]")) {
    const key = form.dataset.persistKey;
    const prefs = readPrefs(key);

    for (const field of form.elements) {
      if (!isPersistableField(field)) continue;
      if (Object.prototype.hasOwnProperty.call(prefs, field.name)) {
        setFieldValue(field, prefs[field.name]);
      }
    }

    form.dispatchEvent(new Event("preferences:restored", { bubbles: true }));
    syncUrlWithPrefs(form, prefs);

    form.addEventListener("change", () => saveForm(form));
    form.addEventListener("input", () => saveForm(form));
    form.addEventListener("submit", () => saveForm(form));

    for (const resetLink of form.querySelectorAll("[data-reset-prefs]")) {
      resetLink.addEventListener("click", () => {
        const extraKeys = (resetLink.dataset.resetExtraKeys || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean);
        try {
          for (const keyToRemove of [key, ...extraKeys]) {
            storage.removeItem(keyToRemove);
          }
        } catch (_) {
          // Ignore storage failures.
        }
      });
    }
  }
})();
