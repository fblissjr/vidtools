/* vidtools - Web UI application */

(function() {
  "use strict";

  // --- State ---
  let currentFileId = null;
  let selectedYtFormat = "mp3";
  let ws = null;
  let wsRetryCount = 0;
  const MAX_WS_RETRIES = 10;

  // --- DOM refs ---
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // --- Init ---
  document.addEventListener("DOMContentLoaded", init);

  async function init() {
    try {
      const res = await api("/api/status");
      if (res.needs_setup) {
        showScreen("setup-screen");
      } else if (res.authenticated) {
        showScreen("main-screen");
        onAuthenticated();
      } else {
        showScreen("login-screen");
      }
    } catch (e) {
      showScreen("login-screen");
    }

    bindEvents();
  }

  function bindEvents() {
    // Login
    $("#login-form").addEventListener("submit", handleLogin);
    $("#setup-form").addEventListener("submit", handleSetup);
    $("#logout-btn").addEventListener("click", handleLogout);

    // Tabs
    $$(".tab").forEach(tab => {
      tab.addEventListener("click", () => switchTab(tab.dataset.tab));
    });

    // YouTube
    $$(".format-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        $$(".format-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        selectedYtFormat = btn.dataset.format;
      });
    });
    $("#yt-download-btn").addEventListener("click", handleYtDownload);

    // Upload
    const uploadArea = $("#upload-area");
    uploadArea.addEventListener("click", () => $("#file-input").click());
    uploadArea.addEventListener("dragover", (e) => { e.preventDefault(); uploadArea.classList.add("dragover"); });
    uploadArea.addEventListener("dragleave", () => uploadArea.classList.remove("dragover"));
    uploadArea.addEventListener("drop", handleDrop);
    $("#file-input").addEventListener("change", handleFileSelect);
    $("#clear-upload").addEventListener("click", clearUpload);

    // Operation select
    $("#operation-select").addEventListener("change", handleOpChange);

    // Process
    $("#process-btn").addEventListener("click", handleProcess);

    // Files
    $("#refresh-files-btn").addEventListener("click", loadFiles);
  }

  // --- Screens ---
  function showScreen(id) {
    $$(".screen").forEach(s => s.hidden = true);
    const screen = document.getElementById(id);
    if (screen) screen.hidden = false;
  }

  function onAuthenticated() {
    connectWebSocket();
    loadFiles();
    loadPresets();
  }

  // --- Auth ---
  async function handleLogin(e) {
    e.preventDefault();
    const pw = $("#password-input").value;
    try {
      await api("/api/login", { method: "POST", body: { password: pw } });
      showScreen("main-screen");
      onAuthenticated();
    } catch (err) {
      showError("login-error", err.message || "Login failed");
    }
  }

  async function handleSetup(e) {
    e.preventDefault();
    const pw = $("#setup-password").value;
    const confirm = $("#setup-confirm").value;
    if (pw !== confirm) {
      showError("setup-error", "Passwords do not match");
      return;
    }
    try {
      await api("/api/setup", { method: "POST", body: { password: pw } });
      showScreen("main-screen");
      onAuthenticated();
    } catch (err) {
      showError("setup-error", err.message || "Setup failed");
    }
  }

  async function handleLogout() {
    await api("/api/logout", { method: "POST" });
    if (ws) ws.close();
    showScreen("login-screen");
    $("#password-input").value = "";
  }

  // --- Tabs ---
  function switchTab(name) {
    $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
    $$(".tab-content").forEach(c => c.classList.toggle("active", c.id === "tab-" + name));
    if (name === "files") loadFiles();
  }

  // --- YouTube Download ---
  async function handleYtDownload() {
    const url = $("#yt-url").value.trim();
    if (!url) return;

    const btn = $("#yt-download-btn");
    btn.disabled = true;
    btn.textContent = "Starting...";

    try {
      const job = await api("/api/jobs", {
        method: "POST",
        body: { operation: "ytdlp", url: url, params: { format: selectedYtFormat } }
      });
      addJobToBar(job);
      $("#yt-url").value = "";
    } catch (err) {
      alert("Download failed: " + (err.message || "Unknown error"));
    } finally {
      btn.disabled = false;
      btn.textContent = "Download";
    }
  }

  // --- File Upload ---
  function handleDrop(e) {
    e.preventDefault();
    $("#upload-area").classList.remove("dragover");
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  }

  function handleFileSelect(e) {
    const file = e.target.files[0];
    if (file) uploadFile(file);
  }

  async function uploadFile(file) {
    const formData = new FormData();
    formData.append("file", file);

    const uploadArea = $("#upload-area");
    uploadArea.querySelector("p").textContent = "Uploading...";

    try {
      const res = await fetch("/api/upload", { method: "POST", body: formData });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Upload failed");
      }
      const data = await res.json();
      currentFileId = data.id;

      uploadArea.hidden = true;
      const status = $("#upload-status");
      status.hidden = false;
      $("#upload-filename").textContent = data.filename + " (" + formatSize(data.size) + ")";
      updateProcessBtn();
    } catch (err) {
      uploadArea.querySelector("p").textContent = "Upload failed. Tap to retry.";
      alert("Upload error: " + err.message);
    }
  }

  function clearUpload() {
    currentFileId = null;
    $("#upload-area").hidden = false;
    $("#upload-area").querySelector("p").textContent = "Tap to select a file or drag & drop";
    $("#upload-status").hidden = true;
    $("#file-input").value = "";
    updateProcessBtn();
  }

  // --- Operation params ---
  function handleOpChange() {
    // Hide all param sections
    $$(".op-params").forEach(p => p.hidden = true);

    const op = $("#operation-select").value;
    if (op) {
      const params = $(`#params-${op}`);
      if (params) params.hidden = false;
    }
    if (op === "add-audio") {
      loadAudioFileOptions();
    }
    updateProcessBtn();
  }

  function updateProcessBtn() {
    const op = $("#operation-select").value;
    $("#process-btn").disabled = !(currentFileId && op);
  }

  // --- Process ---
  async function handleProcess() {
    const op = $("#operation-select").value;
    if (!currentFileId || !op) return;

    const params = getOperationParams(op);
    const btn = $("#process-btn");
    btn.disabled = true;
    btn.textContent = "Starting...";

    try {
      const job = await api("/api/jobs", {
        method: "POST",
        body: { operation: op, input_file_id: currentFileId, params: params }
      });
      addJobToBar(job);
    } catch (err) {
      alert("Process failed: " + (err.message || "Unknown error"));
    } finally {
      btn.disabled = false;
      btn.textContent = "Process";
      updateProcessBtn();
    }
  }

  function getOperationParams(op) {
    switch (op) {
      case "resize":
        return { percentage: parseFloat($("#resize-pct").value) / 100 || 0.5 };
      case "convert":
        return { format: $("#convert-format").value };
      case "cut":
        return {
          start: $("#cut-start").value || undefined,
          end: $("#cut-end").value || undefined,
          copy: $("#cut-copy").checked
        };
      case "extract-audio":
        return { format: $("#audio-format").value };
      case "crop":
        return {
          width: parseInt($("#crop-w").value) || 640,
          height: parseInt($("#crop-h").value) || 480,
          x: parseInt($("#crop-x").value) || 0,
          y: parseInt($("#crop-y").value) || 0
        };
      case "rotate":
        return { rotation: parseInt($("#rotate-deg").value) };
      case "add-audio":
        return {
          audio_file_id: $("#audio-file-select").value,
          mode: $("#audio-mix").checked ? "mix" : "replace",
          volume: parseFloat($("#audio-volume").value) || 1.0
        };
      case "preset":
        return { preset_name: $("#preset-select").value };
      default:
        return {};
    }
  }

  // --- Audio file selector for add-audio operation ---
  function populateAudioSelect(files) {
    const select = $("#audio-file-select");
    while (select.children.length > 1) {
      select.removeChild(select.lastChild);
    }
    files.forEach(f => {
      const opt = document.createElement("option");
      opt.value = f.id;
      opt.textContent = f.filename + " (" + formatSize(f.size) + ")";
      select.appendChild(opt);
    });
  }

  async function loadAudioFileOptions() {
    try {
      const data = await api("/api/files");
      populateAudioSelect(data.files || []);
    } catch (e) {
      // silently fail
    }
  }

  // --- Files ---
  async function loadFiles() {
    try {
      const data = await api("/api/files");
      const files = data.files || [];
      renderFiles(files);
      if ($("#operation-select").value === "add-audio") {
        populateAudioSelect(files);
      }
    } catch (e) {
      // silently fail
    }
  }

  function renderFiles(files) {
    const list = $("#file-list");
    // Clear existing children safely
    while (list.firstChild) {
      list.removeChild(list.firstChild);
    }

    if (files.length === 0) {
      const p = document.createElement("p");
      p.className = "empty-state";
      p.textContent = "No files yet";
      list.appendChild(p);
      return;
    }

    files.forEach(f => {
      const item = document.createElement("div");
      item.className = "file-item";
      item.dataset.id = f.id;

      const info = document.createElement("div");
      info.className = "file-info";

      const name = document.createElement("div");
      name.className = "file-name";
      name.textContent = f.filename;

      const meta = document.createElement("div");
      meta.className = "file-meta";
      meta.textContent = formatSize(f.size) + " \u00b7 " + f.source;

      info.appendChild(name);
      info.appendChild(meta);

      const actions = document.createElement("div");
      actions.className = "file-actions";

      const dlLink = document.createElement("a");
      dlLink.href = "/api/files/" + encodeURIComponent(f.id) + "/download";
      dlLink.className = "small-btn";
      dlLink.textContent = "Download";

      const delBtn = document.createElement("button");
      delBtn.className = "small-btn danger-btn";
      delBtn.textContent = "Delete";
      delBtn.addEventListener("click", () => deleteFile(f.id));

      actions.appendChild(dlLink);
      actions.appendChild(delBtn);

      item.appendChild(info);
      item.appendChild(actions);
      list.appendChild(item);
    });
  }

  async function deleteFile(id) {
    if (!confirm("Delete this file?")) return;
    try {
      await api("/api/files/" + encodeURIComponent(id), { method: "DELETE" });
      loadFiles();
    } catch (e) {
      alert("Delete failed");
    }
  }

  // --- Presets ---
  async function loadPresets() {
    try {
      const data = await api("/api/presets");
      const select = $("#preset-select");
      // Clear options safely
      while (select.firstChild) {
        select.removeChild(select.firstChild);
      }
      for (const [presetName, preset] of Object.entries(data.presets || {})) {
        const opt = document.createElement("option");
        opt.value = presetName;
        opt.textContent = presetName + (preset.description ? " - " + preset.description : "");
        select.appendChild(opt);
      }
    } catch (e) {
      // silently fail
    }
  }

  // --- Jobs bar ---
  function addJobToBar(job) {
    const bar = $("#jobs-bar");
    bar.hidden = false;
    updateJobInBar(job);
  }

  function updateJobInBar(job) {
    const list = $("#jobs-list");
    let el = list.querySelector('[data-job-id="' + job.id + '"]');

    if (!el) {
      el = document.createElement("div");
      el.className = "job-item";
      el.dataset.jobId = job.id;
      list.prepend(el);
    }

    // Clear existing content safely
    while (el.firstChild) {
      el.removeChild(el.firstChild);
    }

    const progressClass = job.status === "completed" ? "complete" : job.status === "failed" ? "error" : "";

    // Build job header
    const header = document.createElement("div");
    header.className = "job-header";

    const opSpan = document.createElement("span");
    opSpan.className = "job-op";
    opSpan.textContent = job.operation;

    const statusSpan = document.createElement("span");
    statusSpan.className = "job-status " + job.status;
    statusSpan.textContent = job.status;

    header.appendChild(opSpan);
    header.appendChild(statusSpan);
    el.appendChild(header);

    // Progress bar
    const progressBar = document.createElement("div");
    progressBar.className = "progress-bar";
    const progressFill = document.createElement("div");
    progressFill.className = "progress-fill " + progressClass;
    progressFill.style.width = job.progress + "%";
    progressBar.appendChild(progressFill);
    el.appendChild(progressBar);

    // Message
    const msg = document.createElement("div");
    msg.className = "job-message";
    msg.textContent = job.progress_message || "";
    el.appendChild(msg);

    // Actions based on status
    if (job.status === "completed" && job.output_file) {
      const actionsDiv = document.createElement("div");
      actionsDiv.className = "job-actions";
      const dlLink = document.createElement("a");
      dlLink.href = "/api/files/" + encodeURIComponent(job.output_file) + "/download";
      dlLink.className = "small-btn";
      dlLink.textContent = "Download";
      actionsDiv.appendChild(dlLink);
      el.appendChild(actionsDiv);
    } else if (job.status === "failed") {
      const actionsDiv = document.createElement("div");
      actionsDiv.className = "job-actions";
      const errSpan = document.createElement("span");
      errSpan.className = "error";
      errSpan.style.fontSize = "13px";
      errSpan.textContent = job.error || "Unknown error";
      actionsDiv.appendChild(errSpan);
      el.appendChild(actionsDiv);
    }

    // Show jobs bar
    $("#jobs-bar").hidden = false;

    // Auto-refresh files when a job completes
    if (job.status === "completed" || job.status === "failed") {
      loadFiles();
    }
  }

  // --- WebSocket ---
  function connectWebSocket() {
    if (ws && ws.readyState <= 1) return;

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/ws/progress");

    ws.onopen = () => { wsRetryCount = 0; };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "job_update") {
          updateJobInBar(msg.job);
        }
      } catch (err) {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      if (wsRetryCount < MAX_WS_RETRIES) {
        wsRetryCount++;
        setTimeout(connectWebSocket, Math.min(1000 * wsRetryCount, 10000));
      }
    };

    ws.onerror = () => { /* onclose will handle reconnect */ };
  }

  // --- API helper ---
  async function api(url, opts = {}) {
    const config = { headers: {} };
    if (opts.method) config.method = opts.method;
    if (opts.body) {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(opts.body);
    }

    const res = await fetch(url, config);
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "HTTP " + res.status);
    }
    return data;
  }

  // --- Utilities ---
  function showError(id, msg) {
    const el = document.getElementById(id);
    el.textContent = msg;
    el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 5000);
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
  }
})();
