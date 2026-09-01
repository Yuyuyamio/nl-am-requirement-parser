"use strict";

const STAGES = [
  { id: "m1", title: "理解需求", detail: "识别物品、尺寸与制造约束" },
  { id: "m2_plan", title: "规划 3D 模型", detail: "把需求转换为模型生成任务" },
  { id: "m2_submit", title: "提交模型生成", detail: "启动 3D 模型生成服务" },
  { id: "m2_wait", title: "生成 3D 模型", detail: "等待模型生成完成" },
  { id: "m2_artifact", title: "获取模型文件", detail: "接收并核对生成结果" },
  { id: "m2_raw_validation", title: "检查网格结构", detail: "检查封闭性、尺寸与结构约束" },
  { id: "m2_mesh_repair", title: "自动修复网格", detail: "修复可恢复的模型缺陷" },
  { id: "m2_repaired_validation", title: "复检修复结果", detail: "确认修复后的模型可用" },
  { id: "m2_normalize", title: "标准化模型", detail: "统一坐标、比例和模型格式" },
  { id: "m2_normalized_validation", title: "验证标准模型", detail: "执行最终模型硬约束检查" },
  { id: "m2_stl_handoff", title: "生成 STL", detail: "准备切片所需的标准模型" },
  { id: "bambu_slice", title: "自动摆放与切片", detail: "无界面调用 Bambu Studio 自动朝向" },
  { id: "m3_printability", title: "检查打印安全", detail: "检查悬垂、桥接与支撑连续性" },
  { id: "bambu_support_reslice", title: "添加支撑并重切", detail: "必要时自动使用保守支撑方案" },
  { id: "m3_support_printability", title: "复检支撑切片", detail: "确认最终刀路通过安全门" },
  { id: "printer_upload", title: "上传打印文件", detail: "安全传输并校验打印文件" },
  { id: "print_start", title: "启动打印", detail: "向空闲打印机发送一次启动指令" },
];

const GROUPS = [
  { title: "理解你的需求", detail: "提取尺寸、外观与用途", stages: ["m1"] },
  { title: "生成 3D 模型", detail: "规划并生成模型文件", stages: ["m2_plan", "m2_submit", "m2_wait", "m2_artifact"] },
  { title: "修复并验证模型", detail: "检查网格、修复并标准化", stages: ["m2_raw_validation", "m2_mesh_repair", "m2_repaired_validation", "m2_normalize", "m2_normalized_validation"] },
  { title: "准备 STL", detail: "创建稳定的切片输入", stages: ["m2_stl_handoff"] },
  { title: "自动摆放与切片", detail: "无界面完成朝向和刀路生成", stages: ["bambu_slice"] },
  { title: "检查打印安全", detail: "检查刀路，必要时自动添加支撑", stages: ["m3_printability", "bambu_support_reslice", "m3_support_printability"] },
  { title: "上传并开始打印", detail: "校验设备状态后发送打印任务", stages: ["printer_upload", "print_start"] },
];

const STATUS_TEXT = {
  starting: "正在启动",
  created: "准备中",
  running: "进行中",
  paused: "已暂停",
  stopped: "已停止",
  ready_to_print: "切片已就绪",
  print_started: "已开始打印",
  awaiting_clarification: "等待补充信息",
  credentials_required: "需要打印机凭据",
  manual_reconciliation_required: "需要人工核对",
  print_rejected: "打印机拒绝任务",
  failed: "运行失败",
};

const EVENT_TEXT = {
  job_created: "任务已创建",
  stage_started: "开始执行",
  stage_completed: "步骤完成",
  stage_skipped: "无需执行，已跳过",
  stage_reused: "复用已完成结果",
  provider_status: "模型服务状态更新",
  stage_failed: "步骤执行失败",
  stage_outcome_unknown: "执行结果需要人工核对",
  credentials_required: "等待打印机访问凭据",
  job_paused: "任务已在安全点暂停",
  job_resumed: "任务继续运行",
  job_finished: "任务流程结束",
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  welcome: $("#welcome"), conversation: $("#conversation"), history: $("#history"),
  newJobButton: $("#newJobButton"),
  taskTitle: $("#taskTitle"), requestText: $("#requestText"), statusDot: $("#statusDot"),
  statusTitle: $("#statusTitle"), stageSummary: $("#stageSummary"), elapsed: $("#elapsed"),
  progressValue: $("#progressValue"), progressBar: $("#progressBar"), timeline: $("#timeline"),
  eventLog: $("#eventLog"), eventCount: $("#eventCount"), controlHint: $("#controlHint"),
  pauseButton: $("#pauseButton"), stopButton: $("#stopButton"), retryButton: $("#retryButton"),
  errorCard: $("#errorCard"), errorTitle: $("#errorTitle"), errorMessage: $("#errorMessage"),
  clarificationCard: $("#clarificationCard"), clarificationQuestion: $("#clarificationQuestion"),
  promptInput: $("#promptInput"), composerForm: $("#composerForm"), sendButton: $("#sendButton"),
  voiceButton: $("#voiceButton"), voicePanel: $("#voicePanel"), voiceStatus: $("#voiceStatus"),
  autoPrintToggle: $("#autoPrintToggle"), detailsButton: $("#detailsButton"),
  detailsDialog: $("#detailsDialog"), detailsJobId: $("#detailsJobId"), detailsList: $("#detailsList"),
  stopDialog: $("#stopDialog"), toast: $("#toast"), sidebar: $("#sidebar"),
  mobileScrim: $("#mobileScrim"), printerStatus: $("#printerStatus"), printerCaption: $("#printerCaption"),
  printerConnectButton: $("#printerConnectButton"), printerDialog: $("#printerDialog"),
  printerConnectionForm: $("#printerConnectionForm"), printerIpInput: $("#printerIpInput"),
  printerDeviceInput: $("#printerDeviceInput"), printerAccessInput: $("#printerAccessInput"),
  printerConnectResult: $("#printerConnectResult"), submitPrinterConnect: $("#submitPrinterConnect"),
  printerSuccessDialog: $("#printerSuccessDialog"), printerSuccessIp: $("#printerSuccessIp"),
  printerSuccessDevice: $("#printerSuccessDevice"), printerSuccessTransport: $("#printerSuccessTransport"),
};

let selectedJobId = null;
let currentSnapshot = null;
let pollTimer = null;
let toastTimer = null;
let historyTick = 0;
let mediaRecorder = null;
let mediaStream = null;
let audioChunks = [];
let recordingTimer = null;
let voiceState = "idle";
let speechAvailable = false;
let browserRecordingAvailable = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let value = {};
  try { value = await response.json(); } catch (_) { value = {}; }
  if (!response.ok) {
    const error = new Error(value.error || `请求失败 (${response.status})`);
    error.payload = value;
    throw error;
  }
  return value;
}

const PRINTER_ERROR_TEXT = {
  invalid_ip: "打印机 IP 无效，请输入局域网 IPv4 地址。",
  invalid_device_id: "Device ID / Serial 格式无效。",
  network_unreachable: "无法到达打印机所在网络。",
  port_unreachable: "打印机的 MQTT/TLS 端口 8883 无法访问。",
  authentication_failed: "Access Code 验证失败，请在打印机上核对后重试。",
  mqtt_tls_failed: "打印机 MQTT/TLS 连接失败。",
  timeout: "连接超时，或没有收到真实打印机状态。",
};

function setPrinterStatus(snapshot = {}) {
  const connected = Boolean(snapshot.connected && snapshot.authenticated && snapshot.printer_state_observed);
  const failed = Boolean(snapshot.error_code);
  elements.printerStatus.className = `printer-status ${connected ? "" : failed ? "error" : "unknown"}`.trim();
  const dot = document.createElement("span");
  const label = document.createTextNode(connected ? " 已连接并验证" : failed ? " 连接失败" : " 尚未检测打印机");
  elements.printerStatus.replaceChildren(dot, label);
  if (connected) {
    elements.printerCaption.textContent = `${snapshot.printer_ip} · ${snapshot.device_id || "已自动发现"}`;
    elements.printerConnectButton.textContent = "重新验证连接";
  } else if (failed) {
    elements.printerCaption.textContent = PRINTER_ERROR_TEXT[snapshot.error_code] || snapshot.error || "连接失败";
    elements.printerConnectButton.textContent = "重新连接";
  } else {
    elements.printerCaption.textContent = "请先验证本地 X1C 连接";
    elements.printerConnectButton.textContent = "连接打印机";
  }
}

function openPrinterConnection() {
  elements.printerAccessInput.value = "";
  elements.printerConnectResult.textContent = "";
  elements.printerConnectResult.className = "printer-connect-result hidden";
  showDialog(elements.printerDialog);
  setTimeout(() => elements.printerAccessInput.focus(), 0);
}

async function connectPrinter(event) {
  event.preventDefault();
  let accessCode = elements.printerAccessInput.value;
  if (!accessCode) {
    elements.printerAccessInput.focus();
    return;
  }
  elements.submitPrinterConnect.disabled = true;
  elements.submitPrinterConnect.textContent = "正在验证…";
  elements.printerStatus.className = "printer-status connecting";
  elements.printerStatus.replaceChildren(
    document.createElement("span"),
    document.createTextNode(" 正在连接"),
  );
  elements.printerCaption.textContent = "正在进行 MQTT/TLS 认证并读取状态";
  try {
    const result = await api("/api/printer/connect", {
      method: "POST",
      body: JSON.stringify({
        ip: elements.printerIpInput.value.trim(),
        device_id: elements.printerDeviceInput.value.trim(),
        access_code: accessCode,
      }),
    });
    setPrinterStatus(result);
    elements.printerConnectResult.className = "printer-connect-result";
    elements.printerConnectResult.textContent = `连接成功 · ${result.transport} · ${Number(result.elapsed_seconds || 0).toFixed(3)}s · 已收到真实状态`;
    elements.printerAccessInput.value = "";
    elements.printerDialog.close();
    elements.printerSuccessIp.textContent = result.printer_ip || "—";
    elements.printerSuccessDevice.textContent = result.device_id || "已自动发现";
    elements.printerSuccessTransport.textContent = `${result.transport} · ${Number(result.elapsed_seconds || 0).toFixed(3)}s`;
    showDialog(elements.printerSuccessDialog);
  } catch (error) {
    const failure = error.payload || { error: error.message };
    setPrinterStatus(failure);
    elements.printerConnectResult.className = "printer-connect-result error";
    const reason = PRINTER_ERROR_TEXT[failure.error_code] || failure.error || error.message;
    elements.printerConnectResult.textContent = failure.error_code ? `${failure.error_code} · ${reason}` : reason;
  } finally {
    accessCode = "";
    elements.printerAccessInput.value = "";
    elements.submitPrinterConnect.disabled = false;
    elements.submitPrinterConnect.textContent = "验证并连接";
  }
}

function titleFromRequest(text) {
  const cleaned = String(text || "").replace(/[。！!？?，,]/g, " ").trim();
  return cleaned.length > 18 ? `${cleaned.slice(0, 18)}…` : (cleaned || "未命名任务");
}

function stageMeta(id) {
  return STAGES.find((stage) => stage.id === id) || { title: id || "准备流程", detail: "正在准备下一步" };
}

function stageRecord(snapshot, id) {
  return snapshot.stages && snapshot.stages[id] ? snapshot.stages[id] : null;
}

function isFinishedRecord(record) {
  return record && ["completed", "skipped"].includes(record.status);
}

function groupState(group, snapshot) {
  const records = group.stages.map((id) => stageRecord(snapshot, id));
  if (records.some((record) => record && ["failed", "outcome_unknown"].includes(record.status))) return "failed";
  if (group.stages.includes(snapshot.current_stage)) {
    return snapshot.status === "paused" ? "paused" : "active";
  }
  if (records.length && records.every(isFinishedRecord)) return "done";
  return "pending";
}

function calculateProgress(snapshot) {
  const records = snapshot.stages || {};
  let score = 0;
  for (const stage of STAGES) {
    if (isFinishedRecord(records[stage.id])) score += 1;
    else if (stage.id === snapshot.current_stage && records[stage.id]?.status === "running") score += 0.35;
  }
  if (["ready_to_print", "print_started"].includes(snapshot.status)) return 100;
  return Math.min(99, Math.round((score / STAGES.length) * 100));
}

function nearestFive(value) {
  return Math.max(0, Math.min(100, Math.round(value / 5) * 5));
}

function statusPresentation(snapshot) {
  const control = snapshot.control || {};
  const current = stageMeta(snapshot.current_stage);
  if (control.status === "stop_requested") return ["正在安全停止", "当前不可中断的调用结束后，不会再进入下一步。"];
  if (snapshot.status === "paused" || control.status === "pause_requested") return ["任务已暂停", `停在「${current.title}」附近，已完成的结果会保留。`];
  if (control.status === "dispatching") return ["正在启动打印", "启动指令正在发送，这个短暂区间不能暂停或停止。"];
  if (snapshot.status === "ready_to_print") return ["切片已经准备好", "模型已通过所有安全检查，打印文件可以随时发送。"];
  if (snapshot.status === "print_started") return ["打印任务已发送", "打印机已经接收任务，请留意首层打印状态。"];
  if (snapshot.status === "stopped") return ["任务已完全停止", "没有继续执行后续软件步骤；已完成的安全结果仍然保留。"];
  if (snapshot.status === "awaiting_clarification") return ["还需要一点信息", "补充完整需求后，可以重新开始自动制作。"];
  if (["failed", "credentials_required", "manual_reconciliation_required", "print_rejected"].includes(snapshot.status)) {
    return [STATUS_TEXT[snapshot.status] || "任务遇到问题", "流程已停在安全位置，错误详情如下。"];
  }
  return [`正在${current.title}`, current.detail];
}

function renderTimeline(snapshot) {
  const fragment = document.createDocumentFragment();
  GROUPS.forEach((group) => {
    const state = groupState(group, snapshot);
    const item = document.createElement("li");
    if (state !== "pending") item.classList.add(state);
    const marker = document.createElement("span");
    marker.textContent = state === "done" ? "✓" : state === "failed" ? "!" : state === "paused" ? "Ⅱ" : "";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = group.title;
    const detail = document.createElement("small");
    if (["active", "paused", "failed"].includes(state) && group.stages.includes(snapshot.current_stage)) {
      detail.textContent = stageMeta(snapshot.current_stage).detail;
    } else {
      detail.textContent = group.detail;
    }
    copy.append(title, detail);
    const time = document.createElement("time");
    time.textContent = { done: "完成", active: "进行中", paused: "已暂停", failed: "出错", pending: "等待" }[state];
    item.append(marker, copy, time);
    fragment.append(item);
  });
  elements.timeline.replaceChildren(fragment);
}

function renderEvents(events) {
  const list = Array.isArray(events) ? events.slice(-80).reverse() : [];
  elements.eventCount.textContent = `${list.length} 条`;
  const fragment = document.createDocumentFragment();
  list.forEach((event) => {
    const row = document.createElement("div");
    row.className = "event-item";
    const stamp = document.createElement("time");
    stamp.textContent = new Date(Number(event.created_unix || 0) * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const text = document.createElement("span");
    const stage = event.stage ? stageMeta(event.stage).title : "整个任务";
    const provider = event.details && event.details.provider_status ? ` · ${event.details.provider_status}` : "";
    text.textContent = `${stage}：${EVENT_TEXT[event.event] || event.event}${provider}`;
    row.append(stamp, text);
    fragment.append(row);
  });
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "event-item";
    empty.textContent = "等待第一条运行记录…";
    fragment.append(empty);
  }
  elements.eventLog.replaceChildren(fragment);
}

function renderControls(snapshot) {
  const control = snapshot.control || {};
  const canResume = Boolean(control.can_resume);
  elements.pauseButton.disabled = !(control.can_pause || canResume);
  elements.pauseButton.innerHTML = canResume ? "▶&nbsp;&nbsp;继续" : "Ⅱ&nbsp;&nbsp;暂停";
  elements.stopButton.disabled = !control.can_stop;
  const canRetry = snapshot.terminal && !["print_started", "manual_reconciliation_required"].includes(snapshot.status);
  elements.retryButton.classList.toggle("hidden", !canRetry);
  if (control.status === "dispatching") elements.controlHint.textContent = "打印启动指令正在发送，不能安全撤回";
  else if (control.status === "stop_requested") elements.controlHint.textContent = "停止请求已收到，正在等待安全检查点";
  else if (canResume || snapshot.status === "paused") elements.controlHint.textContent = "任务暂停期间不会进入新的制造步骤";
  else if (snapshot.status === "print_started") elements.controlHint.textContent = "后续急停请使用打印机实体按钮或官方设备控制";
  else elements.controlHint.textContent = "暂停会在当前安全步骤结束后生效";
}

function renderError(snapshot) {
  const error = snapshot.last_error;
  const show = Boolean(error) || ["failed", "credentials_required", "manual_reconciliation_required", "print_rejected"].includes(snapshot.status);
  elements.errorCard.classList.toggle("hidden", !show);
  if (!show) return;
  elements.errorTitle.textContent = snapshot.status === "credentials_required" ? "需要配置打印机访问码" : "任务遇到问题";
  elements.errorMessage.textContent = error?.message || STATUS_TEXT[snapshot.status] || "未知错误";
}

function renderSnapshot(snapshot) {
  currentSnapshot = snapshot;
  selectedJobId = snapshot.job_id;
  elements.welcome.classList.add("hidden");
  elements.conversation.classList.remove("hidden");
  elements.taskTitle.textContent = titleFromRequest(snapshot.request_text);
  elements.requestText.textContent = snapshot.request_text;
  elements.detailsButton.disabled = false;
  elements.autoPrintToggle.checked = Boolean(snapshot.start_print_requested);
  elements.autoPrintToggle.disabled = Boolean(snapshot.control?.worker_alive);
  const [title, summary] = statusPresentation(snapshot);
  elements.statusTitle.textContent = title;
  elements.stageSummary.textContent = summary;
  elements.statusDot.classList.toggle("hidden", Boolean(snapshot.terminal) || ["stopped", "failed"].includes(snapshot.status));
  const progress = calculateProgress(snapshot);
  elements.progressValue.textContent = `${progress}%`;
  elements.progressBar.className = progress ? `progress-${nearestFive(progress)}` : "";
  renderTimeline(snapshot);
  renderEvents(snapshot.events);
  renderControls(snapshot);
  renderError(snapshot);
  const question = snapshot.clarification_question;
  elements.clarificationCard.classList.toggle("hidden", !question);
  if (question) elements.clarificationQuestion.textContent = question;
  updateElapsed();
}

function updateElapsed() {
  if (!currentSnapshot) return;
  const start = Number(currentSnapshot.created_unix || Date.now() / 1000);
  const end = currentSnapshot.terminal ? Number(currentSnapshot.updated_unix || Date.now() / 1000) : Date.now() / 1000;
  const seconds = Math.max(0, Math.round(end - start));
  if (seconds < 8) elements.elapsed.textContent = "刚刚开始";
  else if (seconds < 60) elements.elapsed.textContent = `已用时 ${seconds}秒`;
  else elements.elapsed.textContent = `已用时 ${Math.floor(seconds / 60)}分 ${seconds % 60}秒`;
}

function historyClass(job) {
  if (["failed", "credentials_required", "manual_reconciliation_required", "print_rejected"].includes(job.status)) return "error";
  if (job.status === "paused" || job.control?.status === "pause_requested") return "paused";
  if (job.terminal) return "complete";
  return "";
}

function renderHistory(jobs) {
  const fragment = document.createDocumentFragment();
  if (!jobs.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "还没有打印任务";
    fragment.append(empty);
  }
  jobs.forEach((job) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `history-item${job.job_id === selectedJobId ? " active" : ""}`;
    button.dataset.jobId = job.job_id;
    const dot = document.createElement("span");
    dot.className = `history-dot ${historyClass(job)}`.trim();
    const copy = document.createElement("span");
    copy.className = "history-copy";
    const strong = document.createElement("strong");
    strong.textContent = titleFromRequest(job.request_text);
    const small = document.createElement("small");
    small.textContent = STATUS_TEXT[job.status] || "处理中";
    copy.append(strong, small);
    const time = document.createElement("time");
    const age = Math.max(0, Date.now() / 1000 - Number(job.updated_unix || 0));
    time.textContent = age < 90 ? "刚刚" : age < 3600 ? `${Math.floor(age / 60)}分` : age < 86400 ? `${Math.floor(age / 3600)}时` : `${Math.floor(age / 86400)}天`;
    button.append(dot, copy, time);
    fragment.append(button);
  });
  elements.history.replaceChildren(fragment);
}

async function loadJobs(selectLatest = false) {
  try {
    const data = await api("/api/jobs");
    renderHistory(data.jobs || []);
    if (selectLatest && !selectedJobId && data.jobs?.length) await selectJob(data.jobs[0].job_id);
  } catch (error) {
    showToast(error.message);
  }
}

async function selectJob(jobId) {
  selectedJobId = jobId;
  closeMobileSidebar();
  try {
    const snapshot = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (selectedJobId === jobId) renderSnapshot(snapshot);
    await loadJobs(false);
  } catch (error) {
    showToast(error.message);
  }
}

async function refreshCurrent() {
  if (!selectedJobId) return;
  const requested = selectedJobId;
  try {
    const snapshot = await api(`/api/jobs/${encodeURIComponent(requested)}`);
    if (selectedJobId === requested) renderSnapshot(snapshot);
    historyTick += 1;
    if (historyTick % 5 === 0) await loadJobs(false);
  } catch (error) {
    showToast(error.message);
  }
}

async function createJob() {
  if (voiceState !== "idle") {
    if (voiceState === "recording") stopRecording();
    showToast("请等语音转写完成，再提交打印需求。");
    return;
  }
  const transcript = elements.promptInput.value.trim();
  if (!transcript) {
    showToast("先告诉我你想打印什么。");
    elements.promptInput.focus();
    return;
  }
  elements.sendButton.disabled = true;
  try {
    const snapshot = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ transcript, start_print: elements.autoPrintToggle.checked }),
    });
    elements.promptInput.value = "";
    resizeComposer();
    renderSnapshot(snapshot);
    await loadJobs(false);
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.sendButton.disabled = false;
  }
}

async function runAction(action, body = {}) {
  if (!selectedJobId) return;
  try {
    const snapshot = await api(`/api/jobs/${encodeURIComponent(selectedJobId)}/${action}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    renderSnapshot(snapshot);
    await loadJobs(false);
  } catch (error) {
    showToast(error.message);
  }
}

function resetComposer() {
  selectedJobId = null;
  currentSnapshot = null;
  elements.welcome.classList.remove("hidden");
  elements.conversation.classList.add("hidden");
  elements.taskTitle.textContent = "新建打印";
  elements.detailsButton.disabled = true;
  elements.autoPrintToggle.disabled = false;
  elements.promptInput.focus();
  loadJobs(false);
}

function resizeComposer() {
  const visualLines = elements.promptInput.value.split("\n")
    .reduce((total, line) => total + Math.max(1, Math.ceil(line.length / 54)), 0);
  elements.promptInput.rows = Math.max(1, Math.min(6, visualLines));
}

function showToast(message, { compact = false, duration = 3300 } = {}) {
  elements.toast.textContent = message;
  elements.toast.classList.toggle("compact", compact);
  elements.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(
    () => elements.toast.classList.remove("show"),
    duration,
  );
}

function showDialog(dialog) {
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function renderDetails() {
  if (!currentSnapshot) return;
  elements.detailsJobId.textContent = currentSnapshot.job_id;
  const rows = [
    ["流程状态", STATUS_TEXT[currentSnapshot.status] || currentSnapshot.status],
    ["当前步骤", stageMeta(currentSnapshot.current_stage).title],
    ["打印模式", currentSnapshot.start_print_requested ? "通过检查后自动打印" : "仅生成可打印文件"],
    ["控制状态", currentSnapshot.control?.status || "—"],
    ["创建时间", new Date(Number(currentSnapshot.created_unix || 0) * 1000).toLocaleString("zh-CN")],
  ];
  const fragment = document.createDocumentFragment();
  rows.forEach(([key, value]) => {
    const dt = document.createElement("dt"); dt.textContent = key;
    const dd = document.createElement("dd"); dd.textContent = value;
    fragment.append(dt, dd);
  });
  elements.detailsList.replaceChildren(fragment);
  showDialog(elements.detailsDialog);
}

function openMobileSidebar() {
  elements.sidebar.classList.add("open");
  elements.mobileScrim.classList.add("open");
}

function closeMobileSidebar() {
  elements.sidebar.classList.remove("open");
  elements.mobileScrim.classList.remove("open");
}

function microphoneErrorMessage(code) {
  return ({
    NotAllowedError: "没有麦克风权限，请在浏览器设置中允许访问。",
    NotFoundError: "没有检测到可用的麦克风。",
    NotReadableError: "麦克风正被其他程序占用。",
    SecurityError: "当前页面没有权限使用麦克风。",
  })[code] || "无法开始录音，请检查麦克风后重试。";
}

function initSpeechAdapter() {
  const browserSupported = Boolean(
    navigator.mediaDevices?.getUserMedia && window.MediaRecorder
  );
  browserRecordingAvailable = browserSupported;
  window.PrintSpeechAdapter = {
    isSupported: browserSupported,
    start: startRecording,
    stop: stopRecording,
  };
  elements.voiceButton.disabled = true;
}

function preferredAudioType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function setVoiceState(state, message = "") {
  voiceState = state;
  elements.voiceButton.classList.toggle("recording", state === "recording");
  elements.voiceButton.classList.toggle("transcribing", state === "transcribing");
  elements.voiceButton.disabled = state === "transcribing"
    || !speechAvailable
    || !browserRecordingAvailable;
  elements.sendButton.disabled = state !== "idle";
  elements.voicePanel.classList.toggle("hidden", state === "idle");
  if (message) elements.voiceStatus.textContent = message;
  elements.voiceButton.setAttribute(
    "aria-label",
    state === "recording" ? "结束录音并转写" : "开始语音输入",
  );
}

function closeMediaStream() {
  if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
  mediaStream = null;
}

async function startRecording() {
  if (voiceState === "recording") {
    stopRecording();
    return;
  }
  if (voiceState === "transcribing") {
    showToast("本地模型正在转写，请稍候。");
    return;
  }
  if (!speechAvailable) {
    showToast("本地语音组件尚未就绪，请重启应用后再试。");
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showToast("当前浏览器不支持录音，仍可直接在输入框打字。");
    return;
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    audioChunks = [];
    const mimeType = preferredAudioType();
    mediaRecorder = new MediaRecorder(
      mediaStream,
      mimeType ? { mimeType, audioBitsPerSecond: 64000 } : undefined,
    );
    mediaRecorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) audioChunks.push(event.data);
    });
    mediaRecorder.addEventListener("error", () => {
      closeMediaStream();
      setVoiceState("idle");
      showToast("录音过程出现错误，请重新尝试。");
    });
    mediaRecorder.addEventListener("stop", handleRecordingStopped, { once: true });
    mediaRecorder.start(250);
    setVoiceState("recording", "正在录音…再次点击麦克风结束并转写");
    recordingTimer = setTimeout(() => {
      if (voiceState === "recording") {
        stopRecording();
        showToast("单次录音最长 90 秒，已自动开始转写。");
      }
    }, 90000);
  } catch (error) {
    closeMediaStream();
    setVoiceState("idle");
    showToast(microphoneErrorMessage(error.name));
  }
}

function stopRecording() {
  if (voiceState === "recording" && mediaRecorder?.state !== "inactive") {
    clearTimeout(recordingTimer);
    setVoiceState("transcribing", "正在使用本地 Whisper 转写…首次使用可能需要下载模型");
    mediaRecorder.stop();
  }
}

async function handleRecordingStopped() {
  closeMediaStream();
  const mimeType = mediaRecorder?.mimeType || audioChunks[0]?.type || "audio/webm";
  const recording = new Blob(audioChunks, { type: mimeType });
  audioChunks = [];
  mediaRecorder = null;
  if (recording.size < 128) {
    setVoiceState("idle");
    showToast("录音内容太短，请重新说一次。");
    return;
  }
  try {
    const response = await fetch("/api/speech/transcribe?language=zh", {
      method: "POST",
      headers: { "Content-Type": recording.type || "audio/webm" },
      body: recording,
    });
    let result = {};
    try { result = await response.json(); } catch (_) { result = {}; }
    if (!response.ok) throw new Error(result.error || `转写失败 (${response.status})`);
    const existing = elements.promptInput.value.trimEnd();
    const separator = existing && !/[，。！？；：,.!?\s]$/.test(existing) ? "，" : "";
    elements.promptInput.value = `${existing}${separator}${result.text}`;
    resizeComposer();
    elements.promptInput.focus();
    elements.promptInput.setSelectionRange(
      elements.promptInput.value.length,
      elements.promptInput.value.length,
    );
    showToast("语音已转成文字，你可以继续修改后再提交。");
  } catch (error) {
    showToast(error.message);
  } finally {
    setVoiceState("idle");
  }
}

elements.composerForm.addEventListener("submit", (event) => { event.preventDefault(); createJob(); });
elements.promptInput.addEventListener("input", resizeComposer);
elements.promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    createJob();
  }
});
elements.voiceButton.addEventListener("click", startRecording);
elements.newJobButton.addEventListener("click", resetComposer);
elements.history.addEventListener("click", (event) => {
  const item = event.target.closest("[data-job-id]");
  if (item) selectJob(item.dataset.jobId);
});
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => {
  elements.promptInput.value = button.dataset.prompt;
  resizeComposer();
  elements.promptInput.focus();
}));
elements.pauseButton.addEventListener("click", () => runAction(currentSnapshot?.control?.can_resume ? "resume" : "pause"));
elements.stopButton.addEventListener("click", () => showDialog(elements.stopDialog));
$("#cancelStop").addEventListener("click", () => elements.stopDialog.close());
$("#confirmStop").addEventListener("click", () => { elements.stopDialog.close(); runAction("stop"); });
elements.retryButton.addEventListener("click", () => runAction("retry", { start_print: elements.autoPrintToggle.checked }));
elements.detailsButton.addEventListener("click", renderDetails);
$("#closeDetails").addEventListener("click", () => elements.detailsDialog.close());
$("#copyError").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(elements.errorMessage.textContent);
    showToast("✓ 已复制", { compact: true, duration: 1600 });
  }
  catch (_) { showToast("无法访问剪贴板，请手动选择错误文字。"); }
});
$("#mobileMenu").addEventListener("click", openMobileSidebar);
$("#closeSidebar").addEventListener("click", closeMobileSidebar);
elements.mobileScrim.addEventListener("click", closeMobileSidebar);
elements.printerConnectButton.addEventListener("click", openPrinterConnection);
elements.printerConnectionForm.addEventListener("submit", connectPrinter);
$("#closePrinterDialog").addEventListener("click", () => elements.printerDialog.close());
$("#cancelPrinterConnect").addEventListener("click", () => elements.printerDialog.close());
$("#confirmPrinterSuccess").addEventListener("click", () => elements.printerSuccessDialog.close());

async function initialise() {
  initSpeechAdapter();
  try {
    const health = await api("/api/health");
    speechAvailable = Boolean(health.speech?.available);
    elements.voiceButton.disabled = !(speechAvailable && browserRecordingAvailable);
    elements.voiceButton.title = speechAvailable
      ? `本地 ${health.speech.model} 模型语音转文字`
      : "本地语音组件尚未安装";
    setPrinterStatus(health.printer || {});
  } catch (_) {
    elements.printerCaption.textContent = "本地服务尚未连接";
  }
  await loadJobs(true);
  pollTimer = setInterval(refreshCurrent, 900);
  setInterval(updateElapsed, 1000);
}

initialise();
