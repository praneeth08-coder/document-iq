(function () {
  "use strict";

  var ALLOWED = ["pdf", "doc", "docx", "txt"];
  var MAX_BYTES = 10 * 1024 * 1024;

  function fmtSize(bytes) {
    if (bytes >= 1048576) { return (bytes / 1048576).toFixed(1) + " MB"; }
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function extOf(name) {
    var i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i + 1).toLowerCase();
  }

  /* ---------- Upload forms and drop zones ---------- */
  function setupUpload(form) {
    var errorBox = form.querySelector(".dz-error");
    var button = form.querySelector("button[type=submit]");
    var zones = Array.prototype.slice.call(form.querySelectorAll(".dropzone"));

    function showError(message) {
      if (!errorBox) { return; }
      errorBox.textContent = message;
      errorBox.hidden = false;
      errorBox.style.animation = "none";
      void errorBox.offsetWidth;
      errorBox.style.animation = "";
    }
    function clearError() {
      if (errorBox) { errorBox.hidden = true; errorBox.textContent = ""; }
    }

    function check(file) {
      if (ALLOWED.indexOf(extOf(file.name)) === -1) {
        return "Unsupported file type. Please choose a PDF, DOC, DOCX or TXT file.";
      }
      if (file.size > MAX_BYTES) { return "That file is larger than 10 MB. Choose a smaller file."; }
      if (file.size === 0) { return "That file is empty."; }
      return "";
    }

    function show(zone, input) {
      var label = zone.querySelector(".dz-file");
      var file = input.files && input.files[0];
      if (!file) {
        zone.classList.remove("has-file");
        if (label) { label.hidden = true; label.textContent = ""; }
        return;
      }
      var problem = check(file);
      if (problem) {
        input.value = "";
        zone.classList.remove("has-file");
        if (label) { label.hidden = true; }
        showError(problem);
        return;
      }
      clearError();
      zone.classList.add("has-file");
      if (label) { label.textContent = file.name + " (" + fmtSize(file.size) + ")"; label.hidden = false; }
    }

    zones.forEach(function (zone) {
      var input = zone.querySelector("input[type=file]");
      input.addEventListener("change", function () { show(zone, input); });
      ["dragenter", "dragover"].forEach(function (ev) {
        zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("drag"); });
      });
      ["dragleave", "dragend"].forEach(function (ev) {
        zone.addEventListener(ev, function () { zone.classList.remove("drag"); });
      });
      zone.addEventListener("drop", function (e) {
        e.preventDefault();
        zone.classList.remove("drag");
        var files = e.dataTransfer && e.dataTransfer.files;
        if (files && files.length) {
          try { input.files = files; } catch (err) { return; }
          show(zone, input);
        }
      });
    });

    form.addEventListener("submit", function (e) {
      var missing = zones.some(function (z) {
        var input = z.querySelector("input[type=file]");
        return !input.files || !input.files.length;
      });
      if (missing) {
        e.preventDefault();
        showError(zones.length > 1 ? "Choose both files first." : "Choose a file first.");
        return;
      }
      if (button) {
        button.classList.add("loading");
        button.setAttribute("aria-busy", "true");
        // Disable after the submit event finishes so the form still posts.
        setTimeout(function () { button.disabled = true; }, 0);
      }
    });

    window.addEventListener("pageshow", function () {
      if (button) { button.disabled = false; button.classList.remove("loading"); button.removeAttribute("aria-busy"); }
    });
  }

  /* ---------- Result page filters ---------- */
  function setupToolbar(toolbar) {
    var list = document.querySelector("[data-list]");
    if (!list) { return; }
    var cards = Array.prototype.slice.call(list.querySelectorAll(".clause"));
    var countEl = document.querySelector("[data-count]");
    var emptyEl = document.querySelector("[data-empty]");
    var levelBtns = Array.prototype.slice.call(toolbar.querySelectorAll("[data-level]"));
    var catSel = toolbar.querySelector("[data-category]");
    var sortSel = toolbar.querySelector("[data-sort]");
    var search = toolbar.querySelector("[data-search]");
    var sure = toolbar.querySelector("[data-sure]");
    var level = "all";

    function apply() {
      var cat = catSel.value;
      var q = search.value.trim().toLowerCase();
      var shown = 0;
      cards.forEach(function (card) {
        var cats = card.dataset.cats.split(/\s+/);
        var ok = (level === "all" || card.dataset.level === level) &&
                 (cat === "all" || cats.indexOf(cat) !== -1) &&
                 (!sure.checked || +card.dataset.conf >= 55) &&
                 (!q || card.textContent.toLowerCase().indexOf(q) !== -1);
        card.hidden = !ok;
        if (ok) { shown += 1; }
      });
      countEl.textContent = "Showing " + shown + " of " + cards.length + " clauses";
      emptyEl.hidden = shown !== 0;
    }

    function sort() {
      var byRank = sortSel.value === "rank";
      var sorted = cards.slice().sort(function (a, b) {
        var ao = +a.dataset.order, bo = +b.dataset.order;
        if (byRank) {
          var diff = (+b.dataset.rank) - (+a.dataset.rank);
          if (diff) { return diff; }
        }
        return ao - bo;
      });
      sorted.forEach(function (card) { list.appendChild(card); });
    }

    levelBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        level = btn.dataset.level;
        levelBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b === btn)); });
        apply();
      });
    });
    catSel.addEventListener("change", apply);
    sortSel.addEventListener("change", sort);
    search.addEventListener("input", apply);
    sure.addEventListener("change", apply);
  }

  /* ---------- Confirm destructive actions ---------- */
  function setupConfirm(form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) { e.preventDefault(); }
    });
  }

  /* ---------- Theme toggle setup ---------- */
  function setupThemeToggle() {
    var toggle = document.getElementById("themeToggle");
    if (!toggle) { return; }
    toggle.addEventListener("click", function () {
      var isDark = document.documentElement.classList.toggle("dark");
      localStorage.setItem("docanalyzer_theme", isDark ? "dark" : "light");
    });
  }

  document.querySelectorAll("form[data-upload]").forEach(setupUpload);
  document.querySelectorAll("[data-toolbar]").forEach(setupToolbar);
  document.querySelectorAll("form[data-confirm]").forEach(setupConfirm);
  setupThemeToggle();
})();