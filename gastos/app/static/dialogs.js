(function () {
  "use strict";

  const dialog = document.getElementById("app-dialog");
  if (!dialog) return;

  const form = document.getElementById("app-dialog-form");
  const title = document.getElementById("app-dialog-title");
  const message = document.getElementById("app-dialog-message");
  const icon = document.getElementById("app-dialog-icon");
  const field = document.getElementById("app-dialog-field");
  const label = document.getElementById("app-dialog-label");
  const input = document.getElementById("app-dialog-input");
  const cancelButton = document.getElementById("app-dialog-cancel");
  const confirmButton = document.getElementById("app-dialog-confirm");

  let active = null;
  let lastFocus = null;

  function normalizeOptions(options, defaults) {
    if (typeof options === "string") return { ...defaults, message: options };
    return { ...defaults, ...(options || {}) };
  }

  function finish(value) {
    if (!active) return;
    const resolve = active.resolve;
    active = null;
    if (dialog.open) dialog.close();
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    resolve(value);
  }

  function open(options) {
    if (active) finish(active.mode === "confirm" ? false : null);

    title.textContent = options.title;
    message.textContent = options.message || "";
    message.hidden = !options.message;
    label.textContent = options.inputLabel || "";
    input.value = options.defaultValue || "";
    input.placeholder = options.placeholder || "";
    field.hidden = options.mode !== "prompt";
    cancelButton.hidden = options.mode === "alert";
    cancelButton.textContent = options.cancelLabel;
    confirmButton.textContent = options.confirmLabel;
    confirmButton.className = "btn app-dialog-confirm " + (options.variant === "danger" ? "btn-danger" : "btn-primary");
    dialog.dataset.variant = options.variant;
    icon.className = "ti " + (options.variant === "danger" ? "ti-alert-triangle" : options.mode === "prompt" ? "ti-pencil" : "ti-message-circle");
    lastFocus = document.activeElement;

    return new Promise(function (resolve) {
      active = { mode: options.mode, resolve };
      dialog.showModal();
      requestAnimationFrame(function () {
        (options.mode === "prompt" ? input : confirmButton).focus();
        if (options.mode === "prompt") input.select();
      });
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!active) return;
    finish(active.mode === "prompt" ? input.value : true);
  });

  cancelButton.addEventListener("click", function () {
    finish(active && active.mode === "confirm" ? false : null);
  });

  input.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || !active || active.mode !== "prompt") return;
    event.preventDefault();
    finish(input.value);
  });

  dialog.addEventListener("cancel", function (event) {
    event.preventDefault();
    finish(active && active.mode === "confirm" ? false : null);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || !dialog.open) return;
    event.preventDefault();
    finish(active && active.mode === "confirm" ? false : null);
  });

  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) finish(active && active.mode === "confirm" ? false : null);
  });

  window.MangotecaDialog = {
    alert: function (options) {
      return open(normalizeOptions(options, {
        mode: "alert", title: "Atención", message: "", confirmLabel: "Entendido",
        cancelLabel: "Cancelar", variant: "default"
      }));
    },
    confirm: function (options) {
      return open(normalizeOptions(options, {
        mode: "confirm", title: "¿Confirmamos?", message: "", confirmLabel: "Confirmar",
        cancelLabel: "Cancelar", variant: "default"
      }));
    },
    prompt: function (options) {
      return open(normalizeOptions(options, {
        mode: "prompt", title: "Completá el dato", message: "", inputLabel: "",
        defaultValue: "", placeholder: "", confirmLabel: "Guardar", cancelLabel: "Cancelar",
        variant: "default"
      }));
    }
  };

  document.addEventListener("submit", async function (event) {
    const targetForm = event.target.closest("form[data-confirm-message]");
    if (!targetForm) return;
    event.preventDefault();
    const confirmed = await window.MangotecaDialog.confirm({
      title: targetForm.dataset.confirmTitle || "¿Confirmamos?",
      message: targetForm.dataset.confirmMessage,
      confirmLabel: targetForm.dataset.confirmLabel || "Confirmar",
      variant: targetForm.dataset.confirmVariant || "default"
    });
    if (confirmed) HTMLFormElement.prototype.submit.call(targetForm);
  });
})();
