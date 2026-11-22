// AnnaSetu - small progressive enhancements. Vanilla JS, no dependencies.
document.addEventListener("DOMContentLoaded", () => {
  // (a) Password show/hide. Each toggle names the action it performs.
  document.querySelectorAll(".password-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.target);
      if (!input) return;
      const visible = input.type === "text";
      input.type = visible ? "password" : "text";
      btn.textContent = visible ? "Show" : "Hide";
      btn.setAttribute("aria-label", visible ? "Show password" : "Hide password");
      btn.setAttribute("aria-pressed", String(!visible));
    });
  });

  // (b) Auto-dismiss flash alerts after 6 seconds. Pauses while hovered or focused.
  const DISMISS_MS = 6000;
  const dismiss = (el) => {
    if (window.bootstrap && bootstrap.Alert) {
      bootstrap.Alert.getOrCreateInstance(el).close();
    } else {
      el.remove();
    }
  };

  document.querySelectorAll(".flash-stack .alert").forEach((el) => {
    let timer = null;
    const start = () => {
      clearTimeout(timer);
      timer = setTimeout(() => dismiss(el), DISMISS_MS);
    };
    const pause = () => clearTimeout(timer);

    el.addEventListener("mouseenter", pause);
    el.addEventListener("mouseleave", () => {
      if (!el.contains(document.activeElement)) start();
    });
    el.addEventListener("focusin", pause);
    el.addEventListener("focusout", (e) => {
      if (!el.contains(e.relatedTarget) && !el.matches(":hover")) start();
    });

    start();
  });
});
