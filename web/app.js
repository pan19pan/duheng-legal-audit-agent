const state = {
  meta: null,
  sample: null,
  busy: false,
};

const PRESUME_LABELS = {
  prior_fine_same_act: "是否已就同一行为罚款",
  duplicate_record: "是否重复录入",
  aggravating_factors: "从重情节",
  mitigating_factors: "从轻情节",
  subjective_fault_excused: "主观过错排除",
  minor_violation: "情节是否轻微",
  correction_order_issued: "是否责令改正",
  law_version_current: "引用条款是否现行有效",
};

const LAYER_SHORT = { 1: "形式合法性", 2: "事实认定", 3: "法律适用", 4: "裁量适当" };

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function sevLabel(r) {
  if (r.kind === "mitigation") return "裁量";
  if (r.kind === "info") return "流程";
  return r.severity === "block" ? "阻断" : "提示";
}

function pillClass(r) {
  if (r.kind === "mitigation") return "mitigation";
  if (r.kind === "info") return "info";
  return r.severity === "block" ? "block" : "warn";
}

function renderBasis(basis) {
  if (!basis || !basis.length) return "";
  return `<div class="basis"><span class="basis-label">依据</span>${basis.map(b => {
    const cite = b.article ? `${b.law} ${b.article}` : b.law;
    const conf = b.confidence === "high" ? "high" : "medium";
    const title = b.note ? esc(b.note) : "";
    return `<span class="cite" title="${title}"><i class="conf ${conf}"></i>${esc(cite)}`
      + (conf === "medium" ? `<span class="note">待核条文号</span>` : "") + `</span>`;
  }).join("")}</div>`;
}

function renderFinding(r) {
  return `<article class="finding">
    <div class="finding-head">
      <span class="pill ${pillClass(r)}">${sevLabel(r)}</span>
      <span class="layer-tag">L${r.layer} ${esc(r.layer_name)}</span>
      <h4>${esc(r.title)}</h4>
      <span style="margin-left:auto;font-size:10.5px;color:var(--muted);font-family:ui-monospace,monospace">${esc(r.id)}</span>
    </div>
    <p class="why">${esc(r.finding)}</p>
    ${r.suggestion ? `<p class="how"><b>处置建议</b>　${esc(r.suggestion)}</p>` : ""}
    ${renderBasis(r.basis)}
  </article>`;
}

function render(result) {
  const s = result.stats;
  const layerSteps = result.layers.map(l => {
    const cls = l.fired > 0 ? "hit" : (l.insufficient > 0 ? "warn" : "clear");
    return `<div class="step ${cls}">
      <div class="l">Layer ${l.id}</div>
      <div class="n">${esc(l.name)}</div>
      <div class="c">命中 <b>${l.fired}</b> · 待核 <b>${l.insufficient}</b> / ${l.total}</div>
    </div>`;
  }).join("");

  const riskHtml = result.risks.length
    ? `<div class="section-title">风险清单 <span class="count">${result.risks.length} 项</span></div>${result.risks.map(renderFinding).join("")}`
    : `<div class="section-title">风险清单 <span class="count">未发现风险项</span></div>`;

  const mitigHtml = result.mitigations.length
    ? `<div class="section-title">裁量提示 <span class="count">${result.mitigations.length} 项</span></div>${result.mitigations.map(renderFinding).join("")}`
    : "";

  const infoHtml = result.infos.length
    ? `<div class="section-title">流程提醒 <span class="count">${result.infos.length} 项</span></div>${result.infos.map(renderFinding).join("")}`
    : "";

  const pendingHtml = result.pending.length
    ? `<details class="pending"><summary>另有 ${result.pending.length} 项因材料信息不足未能完成审查，需人工核实</summary>
        <div class="body">${result.pending.map(r => `<div class="item"><b>${esc(r.title)}</b>　${esc(r.id)}　原因：相关事实未在材料中出现</div>`).join("")}</div>
      </details>`
    : "";

  const presumed = (result.trail.presumed || []).map(k => PRESUME_LABELS[k] || k);
  const fineRange = (result.fine_min != null && result.fine_max != null)
    ? `${result.fine_min}–${result.fine_max} 元` : "未能确定";

  document.getElementById("results").innerHTML = `
    <div class="decision ${result.decision}">
      <div class="decision-top">
        <div>
          <div class="decision-label">${esc(result.decision_label)}</div>
          <p class="decision-note">${esc(result.decision_note)}</p>
        </div>
        <div class="stats">
          <div class="stat block"><div class="v">${result.risks.filter(r => r.severity === "block").length}</div><div class="k">阻断项</div></div>
          <div class="stat warn"><div class="v">${result.risks.filter(r => r.severity === "warn").length}</div><div class="k">提示项</div></div>
          <div class="stat"><div class="v">${s.pending}</div><div class="k">待核实</div></div>
          <div class="stat"><div class="v">${s.elapsed_ms}</div><div class="k">毫秒</div></div>
        </div>
      </div>
      <div class="caseline">
        <span>行为定性 <b>${esc(result.behavior_label)}</b></span>
        <span>适用条款 <code>${esc(result.behavior_article || "未匹配")}</code></span>
        <span>法定幅度 <b>${esc(fineRange)}</b></span>
        <span>审查要点 <b>${s.rules_checked}</b> 条，命中 <b>${s.fired}</b> 条</span>
      </div>
    </div>

    <div class="pipeline">${layerSteps}</div>

    <div class="opinion">
      <div class="opinion-head">
        <h3>审查意见（草稿）</h3>
        <div style="display:flex;align-items:center;gap:10px">
          <span class="src">${result.trail.engine_mode === "llm+rules" ? "LLM 起草 · 规则校验" : "模板起草 · 纯规则模式"}</span>
          <button class="mini" id="copyOpinion">复制</button>
        </div>
      </div>
      <p>${esc(result.draft_opinion)}</p>
    </div>

    ${riskHtml}
    ${mitigHtml}
    ${infoHtml}
    ${pendingHtml}

    <div class="trail">
      <div class="row"><span>案件编号 <b>${esc(result.case_id)}</b></span><span>留痕指纹 <b>${esc(result.trail.fingerprint)}</b></span></div>
      <div class="row"><span>规则集 <b>v${esc(result.trail.ruleset)}</b></span><span>地区参数档 <b>${esc(result.trail.profile)}${result.trail.profile_verified ? "" : "（未校准）"}</b></span></div>
      <div class="row"><span>审查基准日 <b>${esc(result.trail.review_date)}</b></span><span>引擎 <b>${esc(result.trail.engine_mode)}</b></span></div>
      <div class="row"><span>命中规则 <b>${esc(result.trail.rule_ids.join("、") || "无")}</b></span></div>
      ${presumed.length ? `<div class="row"><span>案卷否定推定 <b>${esc(presumed.join("、"))}</b>（材料未记载，推定为「无」）</span></div>` : ""}
    </div>
  `;

  const btn = document.getElementById("copyOpinion");
  if (btn) {
    btn.onclick = () => {
      navigator.clipboard.writeText(result.draft_opinion).then(() => {
        btn.textContent = "已复制";
        setTimeout(() => (btn.textContent = "复制"), 1400);
      });
    };
  }
}

async function runAudit() {
  const text = document.getElementById("facts").value.trim();
  if (text.length < 4 || state.busy) return;
  state.busy = true;
  const btn = document.getElementById("run");
  btn.disabled = true;
  btn.textContent = "审查中…";
  document.getElementById("results").innerHTML = `<div class="loading">正在按四层顺序审查…</div>`;

  try {
    const res = await fetch("/api/audit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        facts_text: text,
        case_id: document.getElementById("caseId").value.trim() || null,
        review_date: document.getElementById("reviewDate").value.trim() || null,
        use_llm: document.getElementById("useLlm").checked,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "审查失败");
    render(data);
  } catch (err) {
    document.getElementById("results").innerHTML =
      `<div class="empty"><h3>审查未完成</h3><p>${esc(err.message)}</p></div>`;
  } finally {
    state.busy = false;
    btn.disabled = false;
    btn.textContent = "开始审查";
  }
}

async function loadSample(id) {
  const res = await fetch("/api/sample/" + encodeURIComponent(id));
  const c = await res.json();
  document.getElementById("facts").value = c.facts_text;
  document.getElementById("caseId").value = c.case_id;
  document.querySelectorAll(".chip").forEach(el => {
    el.classList.toggle("active", el.dataset.id === id);
  });
  runAudit();
}

async function init() {
  const res = await fetch("/api/meta");
  state.meta = await res.json();
  const m = state.meta;

  document.getElementById("topmeta").innerHTML =
    `<span class="dot ${m.llm_available ? "dot-ok" : "dot-warn"}"></span>`
    + `<span>规则集 v${esc(m.ruleset)} · ${m.rule_count} 条审查要点 · `
    + `${m.llm_available ? "LLM 增强已启用" : "纯规则模式"}</span>`;

  document.getElementById("samples").innerHTML = m.samples.map(s =>
    `<button class="chip" data-id="${esc(s.case_id)}">${esc(s.title)}<span class="tag">${esc(s.tag)}</span></button>`
  ).join("");

  document.querySelectorAll(".chip").forEach(el => {
    el.onclick = () => loadSample(el.dataset.id);
  });

  const llmBox = document.getElementById("useLlm");
  llmBox.checked = m.llm_available;
  if (!m.llm_available) {
    llmBox.disabled = true;
    document.getElementById("llmWrap").classList.add("disabled");
    document.getElementById("llmWrap").title = "未配置 DUHENG_LLM_KEY，当前为纯规则模式";
  }

  document.getElementById("run").onclick = runAudit;
  document.getElementById("facts").addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") runAudit();
  });
  wireUpload();

  if (new URLSearchParams(location.search).get("demo") === "1") {
    demoMode();
    return;
  }

  if (m.samples.length) {
    const wanted = new URLSearchParams(location.search).get("case");
    const target = m.samples.find(s => s.case_id === wanted) || m.samples[0];
    loadSample(target.case_id);
  }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function demoMode() {
  const q = new URLSearchParams(location.search);
  if (q.get("demo") !== "1") return false;
  const m = state.meta;
  const wanted = ["DEMO-02", "DEMO-03"].filter(id => m.samples.some(s => s.case_id === id));
  const texts = {};
  for (const id of wanted) {
    const r = await fetch("/api/sample/" + id);
    texts[id] = await r.json();
  }
  const facts = document.getElementById("facts");
  const chips = id => document.querySelectorAll(".chip").forEach(el =>
    el.classList.toggle("active", el.dataset.id === id));

  for (let n = 0; n < wanted.length; n++) {
    const c = texts[wanted[n]];
    facts.value = "";
    document.getElementById("caseId").value = c.case_id;
    chips(c.case_id);
    document.getElementById("results").innerHTML =
      `<div class="empty"><h3>把案卷事实粘进来</h3><p>系统按四层顺序审查：形式合法性 → 事实认定 → 法律适用 → 裁量适当</p></div>`;
    const step = n === 0 ? 5 : 9, gap = n === 0 ? 85 : 45;
    for (let i = step; i <= c.facts_text.length; i += step) {
      facts.value = c.facts_text.slice(0, i);
      await sleep(gap);
    }
    facts.value = c.facts_text;
    await sleep(350);
    runAudit();
    await sleep(n === 0 ? 2400 : 2000);
  }
  return true;
}

init();

function wireUpload() {
  const btn = document.getElementById("uploadBtn");
  const fileInput = document.getElementById("caseFile");
  const hint = document.getElementById("uploadHint");
  const facts = document.getElementById("facts");

  btn.onclick = () => fileInput.click();

  fileInput.onchange = async () => {
    const f = fileInput.files[0];
    if (!f) return;
    const name = f.name.toLowerCase();
    hint.textContent = "正在读取「" + f.name + "」……";
    try {
      let text;
      if (name.endsWith(".txt")) {
        text = await f.text();
      } else if (name.endsWith(".docx")) {
        const res = await fetch("/api/extract_docx", {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: f,
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "解析失败");
        text = data.text;
      } else {
        throw new Error("只支持 .txt 和 .docx 文件");
      }
      text = (text || "").trim();
      if (text.length < 4) throw new Error("文件里没有提取到有效文字");
      facts.value = text;
      const n = document.getElementById("caseId");
      if (n && !n.value) n.value = f.name.replace(/\.(txt|docx)$/i, "");
      hint.textContent = "已载入「" + f.name + "」（" + text.length + " 字），可修改后点「开始审查」";
    } catch (err) {
      hint.textContent = "读取失败：" + err.message;
    } finally {
      fileInput.value = "";
    }
  };
}
