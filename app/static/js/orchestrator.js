/* Project Orchestrator dashboard. All dynamic text is inserted with textContent (model/worker output is untrusted). */
(function () {
  "use strict";
  var root = document.getElementById("orch-root");
  if (!root) return;
  var PID = root.dataset.project, CSRF = root.dataset.csrf;
  var API = "/api/v1/projects/" + encodeURIComponent(PID) + "/orchestration";
  var lastEventId = 0, events = [], timer = null, busy = false, planEditorOpen = false, lastStatus = null;

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "class") n.className = attrs[k];
      else if (k === "text") n.textContent = attrs[k];
      else if (k.indexOf("on") === 0) n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function $(id) { return document.getElementById(id); }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  async function api(method, path, body) {
    var r = await fetch(API + path, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: body ? JSON.stringify(body) : undefined
    });
    var j;
    try { j = await r.json(); } catch (e) { j = { ok: false, error: "Unexpected response (HTTP " + r.status + ")" }; }
    if (!j.ok) throw new Error(j.error || ("HTTP " + r.status));
    return j.result;
  }

  function flash(msg, type) {
    var box = $("orch-flash");
    clear(box);
    if (!msg) return;
    box.appendChild(el("div", { "class": "alert alert-" + (type || "info"), text: msg }));
    if (type !== "error") setTimeout(function () { if (box.firstChild && box.firstChild.textContent === msg) clear(box); }, 6000);
  }

  async function act(label, btn, fn, okMsg) {
    if (busy) return;
    busy = true;
    var old = btn && btn.textContent;
    if (btn) { btn.disabled = true; btn.textContent = label + "..."; }
    try {
      var res = await fn();
      if (okMsg) flash(okMsg, "success");
      return res;
    } catch (e) {
      flash(e.message, "error");
    } finally {
      busy = false;
      if (btn) { btn.disabled = false; btn.textContent = old; }
      refresh();
    }
  }

  var STATE_CLASS = {
    COMPLETED: "badge-online", RUNNING: "badge-primary", EVALUATING: "badge-primary", READY: "badge-role",
    AWAITING_PLAN_APPROVAL: "badge-warning", AWAITING_CLARIFICATION: "badge-warning", BLOCKED: "badge-error",
    FAILED: "badge-error", CANCELLED: "badge-offline", PAUSED: "badge-warning", DRAFT: "badge-offline",
    PLANNING: "badge-role", ANALYZING: "badge-role", REPLANNING: "badge-role",
    accepted: "badge-online", dispatched: "badge-primary", pending: "badge-offline", failed: "badge-error",
    blocked: "badge-error", cancelled: "badge-offline"
  };
  function badge(s) { return el("span", { "class": "badge " + (STATE_CLASS[s] || "badge-offline"), text: String(s).replace(/_/g, " ") }); }
  function ago(ts) {
    if (!ts) return "n/a";
    var s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
    if (s < 60) return Math.floor(s) + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    return Math.floor(s / 3600) + "h ago";
  }

  // ---- controls --------------------------------------------------------------------------------------------
  function ctl(label, cls, handler, id) {
    var b = el("button", { "class": "btn " + cls + " btn-sm", type: "button", id: "ctl-" + id, text: label });
    b.addEventListener("click", function () { handler(b); });
    return b;
  }

  function renderControls(st) {
    var box = $("orch-controls"); clear(box);
    var s = st.run.state;
    var mode = el("select", { "class": "form-control", id: "plan-mode", style: "width:auto;display:inline-block" }, [
      el("option", { value: "auto", text: "Auto (template if recognised, else model)" }),
      el("option", { value: "template", text: "Template only" }),
      el("option", { value: "model", text: "Local model" })]);
    if (s === "DRAFT" || s === "AWAITING_PLAN_APPROVAL" || s === "FAILED") {
      box.appendChild(mode);
      box.appendChild(ctl(s === "DRAFT" ? "Generate Plan" : "Regenerate Plan", "btn-primary", function (b) {
        act("Planning", b, function () { return api("POST", "/plan", { mode: mode.value }); }, "Planning started.");
      }, "generate"));
    }
    if (s === "AWAITING_PLAN_APPROVAL") {
      box.appendChild(ctl("Approve Plan", "btn-primary", function (b) {
        var why = prompt("Approval note (optional):", "") ;
        if (why === null) return;
        var rev = window.__orchRevision;
        var revId = rev ? rev.id : null;
        var digest = rev ? rev.plan_digest : null;
        act("Approving", b, function () { return api("POST", "/approve", { rationale: why, revision_id: revId, expected_digest: digest }); }, "Plan approved. Review it, then Start Execution.");
      }, "approve"));
      box.appendChild(ctl("Reject Plan", "btn-secondary", function (b) {
        var why = prompt("Reason for rejecting:", ""); if (why === null) return;
        act("Rejecting", b, function () { return api("POST", "/reject", { reason: why }); }, "Plan rejected.");
      }, "reject"));
    }
    if (s === "AWAITING_PLAN_APPROVAL" || s === "READY" || s === "BLOCKED") {
      box.appendChild(ctl("Edit Plan", "btn-secondary", function () { planEditorOpen = true; openEditor(); }, "edit"));
    }
    if (s === "READY") {
      box.appendChild(ctl("Start Execution", "btn-primary", function (b) {
        act("Starting", b, function () { return api("POST", "/start"); }, "Execution started.");
      }, "start"));
    }
    if (s === "RUNNING" && st.run.autonomy_level === 0) {
      box.appendChild(ctl("Run Next Step", "btn-primary", function (b) {
        act("Stepping", b, function () { return api("POST", "/step"); }, "Dispatched one step.");
      }, "step"));
    }
    if (s === "RUNNING" || s === "READY" || s === "EVALUATING") {
      box.appendChild(ctl("Pause", "btn-secondary", function (b) { act("Pausing", b, function () { return api("POST", "/pause"); }, "Paused."); }, "pause"));
    }
    if (s === "PAUSED") {
      box.appendChild(ctl("Resume", "btn-primary", function (b) { act("Resuming", b, function () { return api("POST", "/resume"); }, "Resumed."); }, "resume"));
    }
    if (s === "RUNNING" || s === "BLOCKED" || s === "EVALUATING" || s === "FAILED") {
      box.appendChild(ctl("Request Replan", "btn-secondary", function (b) {
        var why = prompt("Why replan?", ""); if (why === null) return;
        act("Replanning", b, function () { return api("POST", "/replan", { reason: why }); }, "Replanning started; the new plan needs your approval.");
      }, "replan"));
    }
    if (["COMPLETED", "CANCELLED"].indexOf(s) < 0) {
      box.appendChild(ctl("Cancel", "btn-danger", function (b) {
        if (!confirm("Cancel this project run? In-flight tasks will be cancelled.")) return;
        act("Cancelling", b, function () { return api("POST", "/cancel", { reason: "cancelled from dashboard" }); }, "Cancelled.");
      }, "cancel"));
    }
    var lvl = el("select", { "class": "form-control", id: "autonomy-level", style: "width:auto;display:inline-block;margin-left:auto" },
      [0, 1, 2, 3].map(function (n) {
        var names = ["0 Manual", "1 Assisted", "2 Supervised", "3 Delegated"];
        var o = el("option", { value: n, text: "Autonomy " + names[n] }); if (n === st.run.autonomy_level) o.selected = true; return o;
      }));
    lvl.disabled = ["COMPLETED", "CANCELLED"].indexOf(s) >= 0;
    lvl.addEventListener("change", function () {
      act("Saving", null, function () { return api("POST", "/autonomy", { level: parseInt(lvl.value, 10) }); }, "Autonomy level updated.");
    });
    box.appendChild(lvl);
  }

  // ---- overview -----------------------------------------------------------------------------------------
  function renderOverview(st) {
    var o = $("orch-overview"); clear(o);
    var p = st.progress;
    o.appendChild(el("div", { "class": "orch-row" }, [
      el("div", {}, [el("div", { "class": "orch-label", text: "State" }), badge(st.run.state)]),
      el("div", {}, [el("div", { "class": "orch-label", text: "Progress" }), el("strong", { text: p.accepted + " / " + p.total + " tasks accepted (" + p.percent + "%)" })]),
      el("div", {}, [el("div", { "class": "orch-label", text: "Active now" }), el("strong", { text: String(p.active) })]),
      el("div", {}, [el("div", { "class": "orch-label", text: "Remaining" }), el("strong", { text: String(p.remaining) })]),
      el("div", {}, [el("div", { "class": "orch-label", text: "Autonomy" }), el("strong", { text: "Level " + st.run.autonomy_level })]),
      el("div", {}, [el("div", { "class": "orch-label", text: "Last activity" }), el("strong", { text: ago(st.last_activity) })])
    ]));
    var bar = el("div", { "class": "orch-bar" }, [el("div", { "class": "orch-bar-fill", style: "width:" + p.percent + "%" })]);
    o.appendChild(bar);
    o.appendChild(el("p", { "class": "orch-objective", text: (st.intake && st.intake.objective) || "" }));
    st.blockers.forEach(function (b) { o.appendChild(el("div", { "class": "alert alert-error", text: b })); });
    if (st.run.state === "PLANNING" || st.run.state === "REPLANNING") {
      o.appendChild(el("div", { "class": "alert alert-info", text: "Planning in progress. A local model on a small node can take a couple of minutes; this page updates automatically." }));
    }
    var stg = (st.stages || []).filter(function (s) { return s.status === "active" || s.status === "planned"; })[0];
    if (stg) o.appendChild(el("div", { "class": "orch-label", text: "Current stage: " + stg.name }));
  }

  function renderDecisions(st) {
    var box = $("orch-decisions"); clear(box);
    if (!st.decisions_required.length) { box.appendChild(el("p", { "class": "orch-muted", text: "Nothing needs your decision right now." })); return; }
    st.decisions_required.forEach(function (d) {
      var row = el("div", { "class": "orch-decision" }, [
        el("div", {}, [badge(d.risk === "high" ? "blocked" : d.risk === "medium" ? "pending" : "accepted"), " ",
          el("strong", { text: d.kind.toUpperCase() + ": " }), el("span", { text: d.requested_action })])]);
      if (d.kind === "clarification") {
        var ta = el("textarea", { "class": "form-control", rows: "2", placeholder: "Your answer / a more detailed objective" });
        row.appendChild(ta);
        row.appendChild(ctl("Submit answer", "btn-primary", function (b) {
          act("Submitting", b, function () { return api("POST", "/clarify", { answers: { objective: ta.value } }); }, "Thanks, re-planning.");
        }, "clarify-" + d.id));
      } else {
        row.appendChild(el("div", { "class": "orch-actions" }, [
          ctl("Approve", "btn-primary", function (b) { act("Approving", b, function () { return api("POST", "/approvals/" + d.id, { approve: true, rationale: "approved from dashboard" }); }); }, "ok-" + d.id),
          ctl("Deny", "btn-secondary", function (b) {
            var why = prompt("Reason for denying:", ""); if (why === null) return;
            act("Denying", b, function () { return api("POST", "/approvals/" + d.id, { approve: false, rationale: why }); });
          }, "no-" + d.id)]));
      }
      box.appendChild(row);
    });
  }

  // ---- plan / graph ---------------------------------------------------------------------------------------
  function renderGraph(tasks) {
    var box = $("orch-graph"); clear(box);
    if (!tasks.length) { box.appendChild(el("p", { "class": "orch-muted", text: "No tasks yet." })); return; }
    var byKey = {}; tasks.forEach(function (t) { byKey[t.key] = t; });
    var level = {};
    function lv(k, seen) {
      if (level[k] != null) return level[k];
      if (seen[k]) return 0;
      seen[k] = 1;
      var d = (byKey[k].depends_on || []).filter(function (x) { return byKey[x]; });
      level[k] = d.length ? 1 + Math.max.apply(null, d.map(function (x) { return lv(x, seen); })) : 0;
      return level[k];
    }
    tasks.forEach(function (t) { lv(t.key, {}); });
    var cols = {}; tasks.forEach(function (t) { (cols[level[t.key]] = cols[level[t.key]] || []).push(t); });
    var W = 170, H = 46, GX = 50, GY = 16, pos = {}, maxRows = 0, nCols = Object.keys(cols).length;
    Object.keys(cols).forEach(function (c) {
      cols[c].forEach(function (t, i) { pos[t.key] = { x: c * (W + GX) + 10, y: i * (H + GY) + 10 }; });
      maxRows = Math.max(maxRows, cols[c].length);
    });
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", nCols * (W + GX)); svg.setAttribute("height", maxRows * (H + GY) + 20);
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Task dependency graph");
    var COLORS = { accepted: "#10b981", dispatched: "#0f766e", pending: "#94a3b8", failed: "#ef4444", blocked: "#ef4444", cancelled: "#cbd5e1" };
    tasks.forEach(function (t) {
      (t.depends_on || []).forEach(function (d) {
        if (!pos[d]) return;
        var ln = document.createElementNS(NS, "line");
        ln.setAttribute("x1", pos[d].x + W); ln.setAttribute("y1", pos[d].y + H / 2);
        ln.setAttribute("x2", pos[t.key].x); ln.setAttribute("y2", pos[t.key].y + H / 2);
        ln.setAttribute("stroke", "#94a3b8"); ln.setAttribute("stroke-width", "1.5");
        svg.appendChild(ln);
      });
    });
    tasks.forEach(function (t) {
      var g = document.createElementNS(NS, "g");
      var r = document.createElementNS(NS, "rect");
      r.setAttribute("x", pos[t.key].x); r.setAttribute("y", pos[t.key].y); r.setAttribute("width", W); r.setAttribute("height", H);
      r.setAttribute("rx", 8); r.setAttribute("fill", "#fff"); r.setAttribute("stroke", COLORS[t.state] || "#94a3b8"); r.setAttribute("stroke-width", "2");
      var t1 = document.createElementNS(NS, "text");
      t1.setAttribute("x", pos[t.key].x + 8); t1.setAttribute("y", pos[t.key].y + 19); t1.setAttribute("font-size", "11"); t1.setAttribute("font-weight", "600");
      t1.textContent = (t.title || t.key).slice(0, 26);
      var t2 = document.createElementNS(NS, "text");
      t2.setAttribute("x", pos[t.key].x + 8); t2.setAttribute("y", pos[t.key].y + 36); t2.setAttribute("font-size", "10"); t2.setAttribute("fill", "#64748b");
      t2.textContent = t.state + (t.node ? " @ " + t.node : "");
      g.appendChild(r); g.appendChild(t1); g.appendChild(t2); svg.appendChild(g);
    });
    box.appendChild(svg);
  }

  function renderTasks(st) {
    var box = $("orch-tasks"); clear(box);
    if (!st.tasks.length) { box.appendChild(el("p", { "class": "orch-muted", text: "No approved plan yet." })); return; }
    var head = el("tr", {}, ["Task", "Role", "Action", "State", "Node / model", "Attempts", "Depends on", ""].map(function (h) { return el("th", { text: h }); }));
    var tb = el("tbody");
    st.tasks.forEach(function (t) {
      var info = t.node ? t.node + (t.model ? " / " + t.model : "") : "unassigned";
      var tr = el("tr", {}, [
        el("td", {}, [el("strong", { text: t.title }), t.last_error ? el("div", { "class": "orch-err", text: t.last_error }) : null]),
        el("td", { text: t.agent_role }), el("td", { text: t.action_type }), el("td", {}, [badge(t.state), t.failure_class ? el("div", { "class": "orch-muted", text: t.failure_class }) : null]),
        el("td", { text: info }), el("td", { text: t.attempts + "/" + t.max_attempts }),
        el("td", { text: (t.depends_on || []).join(", ") || "none" }), el("td")]);
      if (t.state === "failed" || t.state === "blocked") {
        tr.lastChild.appendChild(ctl("Retry", "btn-secondary", function (b) {
          act("Retrying", b, function () { return api("POST", "/tasks/" + t.id + "/retry"); }, "Task queued for retry.");
        }, "retry-" + t.key));
      }
      tb.appendChild(tr);
    });
    box.appendChild(el("table", { "class": "data-table" }, [el("thead", {}, [head]), tb]));
  }

  function renderLive(st) {
    var box = $("orch-live"); clear(box);
    var act_ = st.tasks.filter(function (t) { return t.state === "dispatched"; });
    if (!act_.length) box.appendChild(el("p", { "class": "orch-muted", text: "No task is executing right now." }));
    act_.forEach(function (t) {
      box.appendChild(el("div", { "class": "orch-live-item" }, [
        el("strong", { text: t.title }), el("span", { text: " on " + (t.node || "?") + (t.model ? " using " + t.model : "") }),
        el("span", { "class": "orch-muted", text: " started " + ago(t.dispatched_at) + ", attempt " + t.attempts + "/" + t.max_attempts })]));
    });
    var wait = st.tasks.filter(function (t) { return t.state === "pending" && t.last_error && t.last_error.indexOf("waiting:") === 0; });
    wait.forEach(function (t) { box.appendChild(el("div", { "class": "alert alert-warning", text: t.title + " - " + t.last_error })); });
    var b = st.budget;
    box.appendChild(el("div", { "class": "orch-muted", text: "Budget: tasks " + b.used_tasks + "/" + b.max_tasks + ", inference " + b.used_inference_requests + "/" + b.max_inference_requests + ", tokens " + b.used_tokens + "/" + b.max_tokens + ", retries " + b.used_retries + "/" + b.max_retries_total }));
  }

  async function renderPlanDetails() {
    var box = $("orch-plan"); 
    try {
      var p = await api("GET", "/plan");
      clear(box);
      if (!p.current) { box.appendChild(el("p", { "class": "orch-muted", text: "No plan yet. Use Generate Plan." })); if (p.planning_error) box.appendChild(el("div", { "class": "alert alert-error", text: "Last planning attempt failed: " + p.planning_error })); return; }
      var c = p.current, plan = c.plan, v = c.validation || {};
      box.appendChild(el("div", { "class": "orch-row" }, [
        el("div", {}, [el("div", { "class": "orch-label", text: "Revision" }), el("strong", { text: "#" + c.revision_number + " (" + c.status + ")" })]),
        el("div", {}, [el("div", { "class": "orch-label", text: "Source" }), el("strong", { text: c.source + (c.source === "model" ? " (AI-generated)" : c.source === "template" ? " (deterministic template)" : "") })]),
        el("div", {}, [el("div", { "class": "orch-label", text: "Validation" }), badge(v.valid ? "accepted" : "failed")]),
        el("div", {}, [el("div", { "class": "orch-label", text: "Estimated" }), el("strong", { text: ((v.estimate || {}).task_count || 0) + " tasks, " + ((v.estimate || {}).inference_requests || 0) + " inference" })])]));
      (v.errors || []).forEach(function (e) { box.appendChild(el("div", { "class": "alert alert-error", text: e })); });
      (v.capability_issues || []).forEach(function (e) { box.appendChild(el("div", { "class": "alert alert-warning", text: e })); });
      (v.warnings || []).forEach(function (e) { box.appendChild(el("div", { "class": "alert alert-info", text: e })); });
      var ov = plan.overview || {};
      box.appendChild(el("h4", { text: "Overview" }));
      box.appendChild(el("p", { text: ov.scope || "" }));
      [["Assumptions", ov.assumptions], ["Constraints", ov.constraints]].forEach(function (pair) {
        if (pair[1] && pair[1].length) box.appendChild(el("div", {}, [el("strong", { text: pair[0] + ": " }), el("span", { text: pair[1].join(" | ") })]));
      });
      box.appendChild(el("h4", { text: "Paths and stages" }));
      (plan.paths || []).forEach(function (pa, i) {
        box.appendChild(el("div", {}, [el("strong", { text: (i === 0 ? "Executable path: " : "Alternative: ") + pa.name }), el("div", { "class": "orch-muted", text: pa.rationale }),
          el("ol", {}, (pa.stages || []).map(function (s) { return el("li", { text: s.name + " - " + s.objective }); }))]));
      });
      box.appendChild(el("h4", { text: "Deliverables" }));
      box.appendChild(el("ul", {}, (plan.deliverables || []).map(function (d) { return el("li", {}, [el("strong", { text: d.name }), el("span", { text: " - " + d.acceptance_criteria })]); })));
      box.appendChild(el("h4", { text: "Risks and approval gates" }));
      box.appendChild(el("ul", {}, (plan.risks || []).map(function (r) { return el("li", { text: r.description + " -> " + r.mitigation }); })
        .concat((plan.approval_gates || []).map(function (g) { return el("li", { text: "Approval: " + g.action + " (" + g.reason + ")" }); }))));
      if ((v.high_risk_tasks || []).length) box.appendChild(el("div", { "class": "alert alert-warning", text: "Tasks requiring explicit approval: " + v.high_risk_tasks.join(", ") }));
      if (p.revisions.length > 1) box.appendChild(el("div", { "class": "orch-muted", text: "Revisions: " + p.revisions.map(function (r) { return "#" + r.revision_number + " " + r.status; }).join(", ") }));
      var plantasks = (plan.tasks || []);
      var tb = el("tbody");
      plantasks.forEach(function (t) {
        tb.appendChild(el("tr", {}, [el("td", { text: t.title }), el("td", { text: t.action.type + (t.action.script_id ? ":" + t.action.script_id : "") }),
          el("td", { text: (t.allowed_nodes || []).join(", ") || "any compatible" }), el("td", { text: (t.preferred_models || []).join(", ") || "-" }),
          el("td", { text: t.risk + (t.approval === "required" ? ", approval" : "") }), el("td", { text: (t.depends_on || []).join(", ") || "none" })]));
      });
      box.appendChild(el("h4", { text: "Planned tasks (assignment, models, permissions)" }));
      box.appendChild(el("table", { "class": "data-table" }, [el("thead", {}, [el("tr", {}, ["Task", "Action", "Allowed nodes", "Models", "Risk", "Depends on"].map(function (h) { return el("th", { text: h }); }))]), tb]));
      if (c.graph && !lastStatus.tasks.length) {
        var gt = c.graph.nodes.map(function (n) { return { key: n.key, title: n.title, state: "pending", node: (n.allowed_nodes || [])[0], depends_on: c.graph.edges.filter(function (e) { return e.to === n.key; }).map(function (e) { return e.from; }) }; });
        renderGraph(gt);
      }
      window.__orchPlan = plan;
      window.__orchRevision = c;
    } catch (e) { clear(box); box.appendChild(el("div", { "class": "alert alert-error", text: e.message })); }
  }

  function openEditor() {
    var ed = $("orch-editor"); clear(ed);
    var ta = el("textarea", { "class": "form-control orch-json", id: "plan-json", rows: "18", spellcheck: "false" });
    ta.value = JSON.stringify(window.__orchPlan || {}, null, 2);
    ed.appendChild(el("div", { "class": "alert alert-info", text: "Edit tasks, dependencies, priorities, models and allowed nodes as JSON. Saving creates a new immutable revision, validated before it is accepted. Actions are restricted to the allow-list; free-form commands are rejected." }));
    ed.appendChild(ta);
    ed.appendChild(el("div", { "class": "orch-actions" }, [
      ctl("Save as new revision", "btn-primary", function (b) {
        var parsed; try { parsed = JSON.parse(ta.value); } catch (e) { flash("Invalid JSON: " + e.message, "error"); return; }
        act("Saving", b, function () { return api("PUT", "/plan", { plan: parsed }); }, "New plan revision saved. Review and approve it.").then(function () { clear(ed); planEditorOpen = false; });
      }, "save-plan"),
      ctl("Close", "btn-secondary", function () { clear(ed); planEditorOpen = false; }, "close-editor")]));
    ed.scrollIntoView({ behavior: "smooth" });
  }

  // ---- artifacts / events ------------------------------------------------------------------------------------
  async function renderArtifacts() {
    var box = $("orch-artifacts");
    try {
      var arts = await api("GET", "/artifacts");
      clear(box);
      if (!arts.length) { box.appendChild(el("p", { "class": "orch-muted", text: "No artifacts yet." })); return; }
      var tb = el("tbody");
      arts.forEach(function (a) {
        tb.appendChild(el("tr", {}, [
          el("td", {}, [el("a", { href: API + "/artifacts/" + encodeURIComponent(a.id) + "/download", text: a.filename })]),
          el("td", { text: a.plan_key || "-" }), el("td", { text: a.worker_id || "-" }),
          el("td", {}, [badge(a.provenance === "model_output" ? "pending" : "accepted"), " " + a.provenance.replace(/_/g, " ")]),
          el("td", {}, [badge(a.validation_state === "valid" ? "accepted" : a.validation_state === "invalid" ? "failed" : "pending"), " " + a.validation_state]),
          el("td", { text: (a.size_bytes / 1024).toFixed(1) + " KiB" }), el("td", { "class": "orch-mono", text: a.sha256.slice(0, 12) })]));
      });
      box.appendChild(el("table", { "class": "data-table" }, [el("thead", {}, [el("tr", {}, ["File", "Task", "Worker", "Provenance", "Validation", "Size", "SHA-256"].map(function (h) { return el("th", { text: h }); }))]), tb]));
    } catch (e) { clear(box); box.appendChild(el("div", { "class": "alert alert-error", text: e.message })); }
  }

  async function pollEvents() {
    try {
      var ev = await api("GET", "/events?after_id=" + lastEventId + "&limit=200");
      ev.forEach(function (e) { events.push(e); lastEventId = Math.max(lastEventId, e.id); });
      events = events.slice(-300);
      var box = $("orch-history"); clear(box);
      events.slice().reverse().slice(0, 120).forEach(function (e) {
        box.appendChild(el("div", { "class": "orch-event" }, [
          el("span", { "class": "orch-muted", text: new Date(e.ts).toLocaleTimeString() + "  " }),
          el("strong", { text: e.event_type.replace(/_/g, " ") }),
          el("span", { text: e.new_state ? "  " + (e.previous_state || "") + " -> " + e.new_state : "" }),
          el("span", { "class": "orch-muted", text: "  [" + e.actor + "] " + (e.reason || "") })]));
      });
    } catch (e) { /* run may not exist yet */ }
  }

  function renderModels(st) {
    var box = $("orch-models"); clear(box);
    if (!st.models.length) { box.appendChild(el("p", { "class": "orch-muted", text: "No inference models are registered by workers." })); return; }
    st.models.forEach(function (m) {
      box.appendChild(el("div", {}, [badge(m.ready ? "accepted" : "failed"), " ", el("strong", { text: m.model }), el("span", { text: " on " + m.node + " (" + m.health + ")" }),
        el("span", { "class": "orch-muted", text: m.requests ? "  " + m.successes + "/" + m.requests + " ok" + (m.avg_latency_s ? ", avg " + m.avg_latency_s.toFixed(1) + "s" : "") + (m.tokens_per_second ? ", " + m.tokens_per_second.toFixed(1) + " tok/s" : "") : "  no usage yet" })]));
    });
  }

  // ---- main refresh -------------------------------------------------------------------------------------------
  async function refresh() {
    try {
      var st = await api("GET", "/status");
      lastStatus = st;
      $("orch-state-pill").replaceChildren(badge(st.run.state));
      renderOverview(st); renderControls(st); renderDecisions(st); renderTasks(st); renderLive(st); renderModels(st);
      if (st.tasks.length) renderGraph(st.tasks);
      if (!planEditorOpen) await renderPlanDetails();
      await renderArtifacts(); await pollEvents();
      $("orch-updated").textContent = "Updated " + new Date().toLocaleTimeString();
    } catch (e) {
      if (/no orchestration run/i.test(e.message)) { showInit(); return; }
      $("orch-updated").textContent = "Update failed: " + e.message;
    }
  }

  function showInit() {
    var box = $("orch-overview"); clear(box);
    box.appendChild(el("div", { "class": "alert alert-info", text: "This project does not have an orchestration run yet. Enable orchestration to let AI plan and execute it." }));
    box.appendChild(ctl("Enable orchestration", "btn-primary", function (b) {
      act("Enabling", b, function () { return api("POST", "/init", { autonomy_level: 1 }); }, "Orchestration enabled. Generate a plan next.");
    }, "init"));
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(async function () { if (!document.hidden) await refresh(); schedule(); }, 2500);
  }
  refresh().then(schedule);
})();
