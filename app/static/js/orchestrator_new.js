(function () {
  "use strict";
  var root = document.getElementById("orch-new");
  if (!root) return;
  var CSRF = root.dataset.csrf;
  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: "Unexpected response (HTTP " + r.status + ")" }; }); });
  }
  function v(id) { return document.getElementById(id).value.trim(); }
  function show(msg, type) {
    var box = document.getElementById("new-flash");
    while (box.firstChild) box.removeChild(box.firstChild);
    var d = document.createElement("div"); d.className = "alert alert-" + type; d.textContent = msg; box.appendChild(d);
  }
  document.getElementById("new-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var btn = document.getElementById("btn-generate");
    btn.disabled = true; btn.textContent = "Creating project...";
    var body = { name: v("f-name"), objective: v("f-objective"), deliverables: v("f-deliverables"), success_criteria: v("f-success"),
      constraints: v("f-constraints"), resources: v("f-resources"), deadline: v("f-deadline"), priority: v("f-priority"),
      autonomy_level: parseInt(v("f-autonomy"), 10) };
    post("/api/v1/orchestration/projects", body).then(function (j) {
      if (!j.ok) throw new Error(j.error);
      btn.textContent = "Generating plan...";
      return post("/api/v1/projects/" + j.result.project_id + "/orchestration/plan", { mode: v("f-mode") }).then(function (p) {
        if (!p.ok) { show("Project created, but planning could not start: " + p.error, "error"); }
        window.location.href = "/projects/" + j.result.project_id + "/orchestrator";
      });
    }).catch(function (e) {
      show(e.message, "error"); btn.disabled = false; btn.textContent = "Generate Project Plan";
    });
  });
})();
