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
  { id: "bambu_slice", title: "Bambu 自动定向与树状支撑", detail: "后台调用 Bambu Studio 自动摆放并生成树状支撑" },
  { id: "printer_upload", title: "上传打印文件", detail: "安全传输并校验打印文件" },
  { id: "print_start", title: "启动打印", detail: "向空闲打印机发送一次启动指令" },
];

const GROUPS = [
  { title: "理解你的需求", detail: "提取尺寸、外观与用途", stages: ["m1"] },
  { title: "生成 3D 模型", detail: "规划并生成模型文件", stages: ["m2_plan", "m2_submit", "m2_wait", "m2_artifact"] },
  { title: "修复并验证模型", detail: "检查网格、修复并标准化", stages: ["m2_raw_validation", "m2_mesh_repair", "m2_repaired_validation", "m2_normalize", "m2_normalized_validation"] },
  { title: "准备 STL", detail: "创建稳定的切片输入", stages: ["m2_stl_handoff"] },
  { title: "自动定向并生成树状支撑", detail: "由 Bambu Studio 在后台完成自动摆放和树状支撑", stages: ["bambu_slice"] },
  { title: "上传并开始打印", detail: "Bambu 切片完成后直接发送打印任务", stages: ["printer_upload", "print_start"] },
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
  needs_geometry_regeneration: "旧任务待重新切片",
  printability_blocked: "旧任务待重新切片",
};

const EVENT_TEXT = {
  job_created: "任务已创建",
  stage_started: "开始执行",
  stage_completed: "步骤完成",
  stage_skipped: "无需执行，已跳过",
  stage_reused: "复用已完成结果",
  reprint_source_reused: "复用步骤 1 文件",
  preparation_revision_recovery: "复用原模型，应用新版修复",
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
  printProgressCard: $("#printProgressCard"), printProgressCaption: $("#printProgressCaption"),
  printProgressValue: $("#printProgressValue"), printProgressBar: $("#printProgressBar"),
  printStateDot: $("#printStateDot"), printStateText: $("#printStateText"),
  printRemainingText: $("#printRemainingText"), printMonitorAlert: $("#printMonitorAlert"),
  printMonitorAlertMessage: $("#printMonitorAlertMessage"),
  printMonitorAlertLayer: $("#printMonitorAlertLayer"),
  reprintPanel: $("#reprintPanel"), reprintButton: $("#reprintButton"),
  reprintHint: $("#reprintHint"),
  eventLog: $("#eventLog"), eventCount: $("#eventCount"), controlHint: $("#controlHint"),
  pauseButton: $("#pauseButton"), stopButton: $("#stopButton"), retryButton: $("#retryButton"),
  errorCard: $("#errorCard"), errorTitle: $("#errorTitle"), errorMessage: $("#errorMessage"),
  clarificationCard: $("#clarificationCard"), clarificationQuestion: $("#clarificationQuestion"),
  promptInput: $("#promptInput"), composerForm: $("#composerForm"), sendButton: $("#sendButton"),
  voiceButton: $("#voiceButton"), voicePanel: $("#voicePanel"), voiceTitle: $("#voiceTitle"),
  voiceStatus: $("#voiceStatus"), voiceWave: $("#voiceWave"),
  microphoneSelect: $("#microphoneSelect"), retryMicrophone: $("#retryMicrophone"),
  voiceFileAction: $("#voiceFileAction"), voiceFileInput: $("#voiceFileInput"),
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
  deleteDialog: $("#deleteDialog"), deleteDialogText: $("#deleteDialogText"),
  cancelDelete: $("#cancelDelete"), confirmDelete: $("#confirmDelete"),
  printConfirmDialog: $("#printConfirmDialog"), printConfirmTitle: $("#printConfirmTitle"),
  printConfirmMessage: $("#printConfirmMessage"), cancelPrintConfirm: $("#cancelPrintConfirm"),
  confirmPrintDispatch: $("#confirmPrintDispatch"),
  deliveryCard: $("#deliveryCard"), deliveryMessage: $("#deliveryMessage"), deliveryLinks: $("#deliveryLinks"),
  modelPreview: $("#modelPreview"), previewHint: $("#previewHint"), printButton: $("#printButton"),
  previewControls: $("#previewControls"), previewLegend: $("#previewLegend"),
  supportVisibility: $("#supportVisibility"),
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
let printConfirmationResolve = null;
let browserRecordingAvailable = false;
let audioInputDevices = [];
let pendingDeleteConversation = null;

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

function printerFailureText(snapshot = {}) {
  if (snapshot.error_code === "timeout") {
    if (!snapshot.authenticated) {
      const target = snapshot.printer_ip ? `${snapshot.printer_ip}:8883` : "打印机的 8883 端口";
      return `无法连接 ${target}。请在打印机网络页面核对当前 IP，并确认打印机已开机、Wi-Fi 在线且与电脑处于同一局域网。`;
    }
    if (!snapshot.printer_state_observed) {
      return "MQTT 已认证，但没有收到设备状态。请核对 Device ID，并确认打印机已启用局域网访问。";
    }
  }
  return PRINTER_ERROR_TEXT[snapshot.error_code] || snapshot.error || "连接失败";
}

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
    elements.printerCaption.textContent = printerFailureText(snapshot);
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
    const reason = printerFailureText(failure) || error.message;
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
  const original = STAGES.find((stage) => stage.id === String(id).replace("_regeneration_", "_"));
  if (original && String(id).includes("_regeneration_")) return { title: `重新${original.title}`, detail: "首轮未通过，执行一次重新生成与完整复检" };
  return original || { title: id || "准备流程", detail: "正在准备下一步" };
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
    else if (stage.id === snapshot.current_stage && records[stage.id]?.status === "running") {
      const started = Number(records[stage.id].started_unix || snapshot.updated_unix || Date.now() / 1000);
      const elapsed = Math.max(0, Date.now() / 1000 - started);
      const timeScale = ({
        m1: 24, m2_plan: 24, m2_submit: 45, m2_wait: 420, m2_artifact: 30,
        m2_raw_validation: 35, m2_mesh_repair: 75, m2_repaired_validation: 35,
        m2_normalize: 35, m2_normalized_validation: 35, m2_stl_handoff: 24,
        bambu_slice: 180, bambu_support_reslice: 180,
        m3_support_printability: 70, printer_upload: 35, print_start: 24,
      })[stage.id] || 60;
      const activeFraction = .16 + .72 * (1 - Math.exp(-elapsed / timeScale));
      score += Math.min(.88, activeFraction);
    }
  }
  if (["ready_to_print", "print_started"].includes(snapshot.status)) return 100;
  return Math.min(99, (score / STAGES.length) * 100);
}

const progressMotion = {
  jobId: null,
  displayed: 0,
  target: 0,
  frame: null,
  lastTime: 0,
  active: false,
};

function paintProgress(value) {
  const safe = Math.max(0, Math.min(100, value));
  elements.progressBar.style.width = `${safe.toFixed(3)}%`;
  const label = safe >= 99.999 ? "100" : progressMotion.active ? safe.toFixed(1) : String(Math.floor(safe));
  elements.progressValue.textContent = `${label}%`;
  elements.progressBar.parentElement.setAttribute("role", "progressbar");
  elements.progressBar.parentElement.setAttribute("aria-valuemin", "0");
  elements.progressBar.parentElement.setAttribute("aria-valuemax", "100");
  elements.progressBar.parentElement.setAttribute("aria-valuenow", String(label));
}

function animateProgress(time) {
  const motion = progressMotion;
  const elapsed = motion.lastTime ? Math.min(80, time - motion.lastTime) : 16;
  motion.lastTime = time;
  const distance = motion.target - motion.displayed;
  if (Math.abs(distance) <= .008) {
    motion.displayed = motion.target;
    motion.frame = null;
    motion.lastTime = 0;
    paintProgress(motion.displayed);
    return;
  }
  motion.displayed += distance * (1 - Math.exp(-elapsed / 520));
  paintProgress(motion.displayed);
  motion.frame = requestAnimationFrame(animateProgress);
}

function setProgressTarget(jobId, value, active) {
  const motion = progressMotion;
  const target = Math.max(0, Math.min(100, Number(value) || 0));
  motion.active = Boolean(active);
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (motion.jobId !== jobId || reducedMotion) {
    if (motion.frame) cancelAnimationFrame(motion.frame);
    motion.jobId = jobId;
    motion.target = target;
    motion.displayed = target;
    motion.frame = null;
    motion.lastTime = 0;
    paintProgress(target);
    return;
  }
  motion.target = Math.max(motion.target, target);
  if (!motion.frame) motion.frame = requestAnimationFrame(animateProgress);
}

function statusPresentation(snapshot) {
  const control = snapshot.control || {};
  const current = stageMeta(snapshot.current_stage);
  if (control.status === "stop_requested") return ["正在安全停止", "当前不可中断的调用结束后，不会再进入下一步。"];
  if (snapshot.status === "paused" || control.status === "pause_requested") return ["任务已暂停", `停在「${current.title}」附近，已完成的结果会保留。`];
  if (control.status === "dispatching") return ["正在启动打印", "启动指令正在发送，这个短暂区间不能暂停或停止。"];
  if (snapshot.status === "ready_to_print") return snapshot.delivery?.available
    ? ["Bambu 切片已就绪", "Bambu Studio 已完成树状支撑切片，没有执行额外可打印性检查。"]
    : ["等待 Bambu 切片文件", snapshot.delivery?.message || "Bambu Studio 尚未完成切片。"];
  if (snapshot.status === "needs_geometry_regeneration") return snapshot.preparation_recovery_available
    ? ["旧任务可以直接重切", "可以复用现有模型交给新版 Bambu 流程重新切片，不再执行旧版检查。"]
    : ["这是旧版任务状态", "请新建一次任务；新版在 Bambu Studio 切片成功后会直接放行。"];
  if (snapshot.status === "printability_blocked") return ["这是旧版任务状态", "新版已取消切片后检查；请重新运行任务以直接采用 Bambu Studio 的切片结果。"];
  if (snapshot.status === "print_started") {
    const monitor = snapshot.print_monitor || {};
    const percent = Number.isFinite(Number(monitor.percent)) ? `${Number(monitor.percent).toFixed(1).replace(".0", "")}%` : "";
    if (monitor.status === "error") return ["打印需要处理", monitor.alert?.message || "打印机报告异常，请立即检查设备。"];
    if (monitor.status === "completed") return ["实体打印已完成", "打印监控已收到正常结束状态。"];
    if (monitor.status === "paused") return ["实体打印已暂停", `${percent ? `当前完成 ${percent}。` : ""}请在打印机或官方设备控制中处理。`];
    if (monitor.status === "printing") return ["正在实体打印", `${percent ? `当前完成 ${percent}。` : ""}逐层状态正在后台监控。`];
    if (monitor.status === "unavailable") return ["打印任务已发送", "暂时没有收到新的打印机状态，请检查本地连接。"];
    return ["正在连接打印监控", "打印任务已经发送，正在等待打印机进度状态。"];
  }
  if (snapshot.status === "stopped") return ["任务已完全停止", "没有继续执行后续软件步骤；已完成的安全结果仍然保留。"];
  if (snapshot.status === "awaiting_clarification") return ["还需要一点信息", "补充完整需求后，可以重新开始自动制作。"];
  if (["failed", "credentials_required", "manual_reconciliation_required", "print_rejected"].includes(snapshot.status)) {
    return [STATUS_TEXT[snapshot.status] || "任务遇到问题", "流程已停在安全位置，错误详情如下。"];
  }
  return [`正在${current.title}`, current.detail];
}

function renderTimeline(snapshot) {
  const fragment = document.createDocumentFragment();
  const groups = [...GROUPS];
  groups.forEach((group) => {
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
    const skipped = group.stages.every((id) => stageRecord(snapshot, id)?.status === "skipped");
    time.textContent = skipped ? "未执行" : { done: "完成", active: "进行中", paused: "已暂停", failed: "出错", pending: "等待" }[state];
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

const PRINT_MONITOR_TEXT = {
  waiting: ["等待打印", "生成完成后自动开始监控"],
  connecting: ["正在连接监控", "等待打印机返回实时状态"],
  printing: ["正在打印", "逐层状态已折叠为总体进度"],
  paused: ["打印已暂停", "请在打印机或官方设备控制中处理"],
  completed: ["打印已完成", "监控已收到正常结束状态"],
  error: ["打印异常", "请立即检查打印机"],
  unavailable: ["监控暂时不可用", "打印任务可能仍在设备上运行"],
};

function formatRemainingMinutes(value) {
  const minutes = Number(value);
  if (!Number.isFinite(minutes) || minutes < 0) return "";
  if (minutes < 1) return "预计不到 1 分钟";
  if (minutes < 60) return `预计剩余 ${Math.round(minutes)} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  return `预计剩余 ${hours} 小时${rest ? ` ${rest} 分钟` : ""}`;
}

function renderReprint(snapshot) {
  const reprint = snapshot.reprint || {};
  const visible = Boolean(reprint.visible);
  const available = visible && Boolean(reprint.available);
  elements.reprintPanel.classList.toggle("hidden", !visible);
  elements.reprintPanel.classList.toggle("ready", available);
  elements.reprintButton.disabled = !available;
  elements.reprintHint.textContent = reprint.message
    || "原任务结束且打印机安全空闲后开放。";
}

function renderPrintMonitor(snapshot) {
  const shouldShow = Boolean(snapshot.start_print_requested || snapshot.status === "print_started");
  elements.printProgressCard.classList.toggle("hidden", !shouldShow);
  if (!shouldShow) return;

  const monitor = snapshot.print_monitor || {};
  const status = String(monitor.status || "waiting");
  const labels = PRINT_MONITOR_TEXT[status] || PRINT_MONITOR_TEXT.waiting;
  const numericPercent = Number(monitor.percent);
  const percent = Number.isFinite(numericPercent) ? Math.max(0, Math.min(100, numericPercent)) : 0;
  const percentLabel = Number.isInteger(percent) ? String(percent) : percent.toFixed(1);
  elements.printProgressValue.textContent = `${percentLabel}%`;
  elements.printProgressBar.style.width = `${percent}%`;
  elements.printProgressBar.classList.toggle("is-running", status === "printing");
  elements.printProgressBar.parentElement.setAttribute("role", "progressbar");
  elements.printProgressBar.parentElement.setAttribute("aria-valuemin", "0");
  elements.printProgressBar.parentElement.setAttribute("aria-valuemax", "100");
  elements.printProgressBar.parentElement.setAttribute("aria-valuenow", percentLabel);
  elements.printProgressCard.dataset.status = status;
  elements.printStateText.textContent = labels[0];
  elements.printStateDot.className = `print-state-dot ${status}`;
  elements.printProgressCaption.textContent = snapshot.status === "print_started"
    ? "实时读取打印机总体进度"
    : "等待打印任务生成完成";
  elements.printRemainingText.textContent = formatRemainingMinutes(monitor.remaining_minutes)
    || monitor.message
    || labels[1];
  renderReprint(snapshot);

  const alert = monitor.alert;
  const showAlert = status === "error" || Boolean(monitor.has_alert);
  elements.printMonitorAlert.classList.toggle("hidden", !showAlert);
  if (!showAlert) {
    elements.printMonitorAlertMessage.textContent = "";
    elements.printMonitorAlertLayer.textContent = "";
    return;
  }
  const reasons = [];
  if (alert?.print_error != null && ![0, "0", ""].includes(alert.print_error)) reasons.push(`错误码 ${alert.print_error}`);
  if (Array.isArray(alert?.hms) && alert.hms.length) reasons.push(`HMS 告警 ${alert.hms.length} 条`);
  elements.printMonitorAlertMessage.textContent = reasons.length
    ? `${alert?.message || "打印机报告异常"}（${reasons.join(" · ")}）`
    : (alert?.message || "打印机报告异常，请检查设备。");
  const layer = Number(alert?.layer_num);
  const total = Number(alert?.total_layer_num);
  elements.printMonitorAlertLayer.textContent = Number.isFinite(layer)
    ? `异常发生在第 ${layer}${Number.isFinite(total) ? ` / ${total}` : ""} 层`
    : "打印机未返回明确的异常层数";
}

function renderControls(snapshot) {
  const control = snapshot.control || {};
  const canResume = Boolean(control.can_resume);
  elements.pauseButton.disabled = !(control.can_pause || canResume);
  elements.pauseButton.innerHTML = canResume ? "▶&nbsp;&nbsp;继续" : "Ⅱ&nbsp;&nbsp;暂停";
  elements.stopButton.disabled = !control.can_stop;
  const canRetry = snapshot.terminal && (snapshot.preparation_recovery_available || !["ready_to_print", "print_started", "manual_reconciliation_required", "needs_geometry_regeneration", "printability_blocked", "awaiting_clarification"].includes(snapshot.status));
  elements.retryButton.classList.toggle("hidden", !canRetry);
  if (control.status === "dispatching") elements.controlHint.textContent = "打印启动指令正在发送，不能安全撤回";
  else if (control.status === "stop_requested") elements.controlHint.textContent = "停止请求已收到，正在等待安全检查点";
  else if (canResume || snapshot.status === "paused") elements.controlHint.textContent = "任务暂停期间不会进入新的制造步骤";
  else if (snapshot.status === "print_started") elements.controlHint.textContent = "后续急停请使用打印机实体按钮或官方设备控制";
  else if (snapshot.status === "ready_to_print") elements.controlHint.textContent = "尚未开打；请先查看上方成品文件";
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
  elements.autoPrintToggle.disabled = Boolean(snapshot.control?.worker_alive);
  const [title, summary] = statusPresentation(snapshot);
  elements.statusTitle.textContent = title;
  elements.stageSummary.textContent = summary;
  const printMonitorActive = ["printing", "connecting", "waiting"].includes(snapshot.print_monitor?.status);
  elements.statusDot.classList.toggle("hidden", (Boolean(snapshot.terminal) && !printMonitorActive) || ["stopped", "failed"].includes(snapshot.status));
  const progress = calculateProgress(snapshot);
  const progressActive = Boolean(snapshot.control?.worker_alive) && !snapshot.terminal;
  elements.progressBar.classList.toggle("is-running", progressActive);
  setProgressTarget(snapshot.job_id, progress, progressActive);
  renderTimeline(snapshot);
  renderEvents(snapshot.events);
  renderPrintMonitor(snapshot);
  renderControls(snapshot);
  renderError(snapshot);
  renderDelivery(snapshot);
  const question = snapshot.clarification_question;
  elements.clarificationCard.classList.toggle("hidden", !question);
  if (question) elements.clarificationQuestion.textContent = question;
  updateElapsed();
}

function updateElapsed() {
  if (!currentSnapshot) return;
  const start = Number(currentSnapshot.preparation_started_unix || currentSnapshot.created_unix || Date.now() / 1000);
  const end = currentSnapshot.terminal ? Number(currentSnapshot.updated_unix || Date.now() / 1000) : Date.now() / 1000;
  const seconds = Math.max(0, Math.round(end - start));
  const timingLabel = currentSnapshot.preparation_started_unix ? "本次修复用时" : "已用时";
  if (seconds < 8) elements.elapsed.textContent = "刚刚开始";
  else if (seconds < 60) elements.elapsed.textContent = `${timingLabel} ${seconds}秒`;
  else elements.elapsed.textContent = `${timingLabel} ${Math.floor(seconds / 60)}分 ${seconds % 60}秒`;
}

function historyClass(job) {
  if (job.print_monitor?.status === "error" || ["failed", "credentials_required", "manual_reconciliation_required", "print_rejected"].includes(job.status)) return "error";
  if (job.print_monitor?.status === "paused" || job.status === "paused" || job.control?.status === "pause_requested") return "paused";
  if (["printing", "connecting", "waiting"].includes(job.print_monitor?.status)) return "";
  if (job.terminal) return "complete";
  return "";
}

function historyStatusText(job) {
  const monitor = job.print_monitor || {};
  const prefix = job.source_job_id ? "再次打印 · " : "";
  if (job.status !== "print_started") return `${prefix}${STATUS_TEXT[job.status] || "处理中"}`;
  if (monitor.status === "error") return `${prefix}打印异常`;
  if (monitor.status === "completed") return `${prefix}打印完成`;
  if (monitor.status === "paused") return `${prefix}打印已暂停`;
  if (monitor.status === "printing" && Number.isFinite(Number(monitor.percent))) {
    return `${prefix}打印中 ${Number(monitor.percent).toFixed(1).replace(".0", "")}%`;
  }
  return `${prefix}${STATUS_TEXT[job.status]}`;
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
    const row = document.createElement("div");
    row.className = `history-item${job.job_id === selectedJobId ? " active" : ""}${job.pinned ? " pinned" : ""}`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-select";
    button.dataset.jobId = job.job_id;
    button.setAttribute("aria-label", `打开对话：${titleFromRequest(job.request_text)}`);
    const dot = document.createElement("span");
    dot.className = `history-dot ${historyClass(job)}`.trim();
    const copy = document.createElement("span");
    copy.className = "history-copy";
    const strong = document.createElement("strong");
    strong.textContent = titleFromRequest(job.request_text);
    const small = document.createElement("small");
    small.textContent = historyStatusText(job);
    copy.append(strong, small);
    const time = document.createElement("time");
    const age = Math.max(0, Date.now() / 1000 - Number(job.updated_unix || 0));
    time.textContent = age < 90 ? "刚刚" : age < 3600 ? `${Math.floor(age / 60)}分` : age < 86400 ? `${Math.floor(age / 3600)}时` : `${Math.floor(age / 86400)}天`;
    button.append(dot, copy, time);
    const actions = document.createElement("span");
    actions.className = "history-actions";
    const pin = document.createElement("button");
    pin.type = "button";
    pin.dataset.historyAction = job.pinned ? "unpin" : "pin";
    pin.dataset.jobId = job.job_id;
    pin.className = job.pinned ? "pin active" : "pin";
    pin.textContent = job.pinned ? "取消" : "置顶";
    pin.setAttribute("aria-label", `${job.pinned ? "取消置顶" : "置顶"}对话：${titleFromRequest(job.request_text)}`);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.dataset.historyAction = "delete";
    remove.dataset.jobId = job.job_id;
    remove.dataset.jobTitle = titleFromRequest(job.request_text);
    remove.className = "delete";
    remove.textContent = "删除";
    remove.setAttribute("aria-label", `删除对话：${titleFromRequest(job.request_text)}`);
    actions.append(pin, remove);
    row.append(button, actions);
    fragment.append(row);
  });
  elements.history.replaceChildren(fragment);
}

async function updatePinned(jobId, action) {
  try {
    const snapshot = await api(`/api/jobs/${encodeURIComponent(jobId)}/${action}`, {
      method: "POST",
      body: "{}",
    });
    if (selectedJobId === jobId) currentSnapshot = snapshot;
    await loadJobs(false);
    showToast(action === "pin" ? "已置顶" : "已取消置顶", { compact: true, duration: 1500 });
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteConversation(jobId, title) {
  pendingDeleteConversation = { jobId, title };
  elements.deleteDialogText.textContent = `「${title}」会从最近任务中移除，本地任务资料会转存到回收目录。运行中的任务不会被删除。`;
  elements.confirmDelete.disabled = false;
  elements.confirmDelete.textContent = "删除对话";
  showDialog(elements.deleteDialog);
  setTimeout(() => elements.cancelDelete.focus(), 0);
}

async function confirmDeleteConversation() {
  if (!pendingDeleteConversation) return;
  const { jobId } = pendingDeleteConversation;
  elements.confirmDelete.disabled = true;
  elements.confirmDelete.textContent = "正在删除…";
  try {
    await api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
    elements.deleteDialog.close();
    pendingDeleteConversation = null;
    if (selectedJobId === jobId) resetComposer();
    else await loadJobs(false);
    showToast("对话已删除", { compact: true, duration: 1800 });
  } catch (error) {
    elements.deleteDialogText.textContent = error.message;
  } finally {
    elements.confirmDelete.disabled = false;
    elements.confirmDelete.textContent = "删除对话";
  }
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
  if (["recording", "transcribing"].includes(voiceState)) {
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
    if (elements.autoPrintToggle.checked) {
      const approved = await requestPrintConfirmation({
        title: "开启自动打印？",
        message: "模型切片成功后，系统会自动上传文件并启动实体打印机，不再等待第二次确认。",
        confirmLabel: "确认自动打印",
      });
      if (!approved) return;
    }
    const snapshot = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ transcript, start_print: elements.autoPrintToggle.checked }),
    });
    elements.promptInput.value = "";
    resizeComposer();
    setVoiceState("idle");
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

async function reprintStepOneFiles() {
  if (!currentSnapshot?.reprint?.available || !selectedJobId) return;
  const sourceJobId = selectedJobId;
  const approved = await requestPrintConfirmation({
    title: "使用步骤 1 文件再次打印？",
    message: "系统会复用原任务中已校验的模型与 Bambu 切片，新建一条打印记录，并向实体打印机发送一次新的启动指令。",
    confirmLabel: "确认再次打印",
  });
  if (!approved) return;
  elements.reprintButton.disabled = true;
  elements.reprintButton.textContent = "正在创建新任务…";
  try {
    const snapshot = await api(`/api/jobs/${encodeURIComponent(sourceJobId)}/reprint`, {
      method: "POST",
      body: "{}",
    });
    renderSnapshot(snapshot);
    await loadJobs(false);
    showToast("已复用步骤 1 文件并创建新的打印任务", { duration: 2600 });
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.reprintButton.textContent = "再次打印步骤 1 文件";
    if (selectedJobId === sourceJobId && currentSnapshot) renderReprint(currentSnapshot);
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
  elements.autoPrintToggle.checked = false;
  setVoiceState("idle");
  elements.promptInput.focus();
  loadJobs(false);
}

let previewKey = null;
let previewAbort = null;
let previewController = null;
function renderDelivery(snapshot) {
  const delivery = snapshot.delivery || {};
  elements.deliveryCard.classList.toggle("hidden", !snapshot.terminal);
  elements.deliveryMessage.textContent = delivery.message || "等待 Bambu Studio 切片完成。";
  elements.deliveryLinks.replaceChildren();
  elements.printButton.classList.toggle("hidden", !(delivery.available && snapshot.status === "ready_to_print"));
  for (const file of delivery.available ? delivery.files : []) {
    const link = document.createElement("a");
    link.href = file.url;
    link.download = file.name;
    link.textContent = file.name;
    elements.deliveryLinks.append(link);
  }
  const model = delivery.available && delivery.files.find((file) => file.kind === "stl");
  const gcode = delivery.available && delivery.files.find((file) => file.kind === "gcode");
  const key = model && gcode && delivery.preview_url ? `${snapshot.job_id}:${model.sha256}:${gcode.sha256}` : null;
  if (previewKey === key) return;
  previewKey = key;
  previewAbort?.abort();
  previewController?.dispose?.();
  previewController = null;
  elements.modelPreview.classList.add("hidden");
  elements.previewControls.classList.add("hidden");
  elements.previewLegend.classList.add("hidden");
  elements.previewHint.textContent = "";
  if (!model || !gcode || !delivery.preview_url) return;
  previewAbort = new AbortController();
  elements.previewHint.textContent = "正在读取最终模型和真实支撑刀路…";
  Promise.all([
    fetch(model.url, { signal: previewAbort.signal, cache: "no-store" }),
    fetch(delivery.preview_url, { signal: previewAbort.signal, cache: "no-store" }),
  ]).then(async ([modelResponse, supportResponse]) => {
    if (!modelResponse.ok || modelResponse.headers.get("X-Artifact-SHA256") !== model.sha256) throw new Error("最终模型核验失败");
    if (!supportResponse.ok) throw new Error("最终支撑核验失败");
    const [data, support] = await Promise.all([modelResponse.arrayBuffer(), supportResponse.json()]);
    if (support.schema !== "actual_support_toolpaths_v1"
        || support.source?.stl_sha256 !== model.sha256
        || support.source?.gcode_sha256 !== gcode.sha256
        || support.segment_count !== support.paths?.length) throw new Error("模型与支撑来源不一致");
    if (previewKey !== key) return;
    elements.modelPreview.classList.remove("hidden");
    elements.previewControls.classList.remove("hidden");
    elements.previewLegend.classList.remove("hidden");
    elements.supportVisibility.checked = true;
    previewController = showModelPreview(elements.modelPreview, data, support);
    elements.previewHint.textContent = `拖动可旋转。当前叠加最终切片中的 ${support.segment_count.toLocaleString("zh-CN")} 条真实支撑刀路；01 文件是同一份含支撑切片。`;
  }).catch((error) => {
    if (previewKey === key && error.name !== "AbortError") elements.previewHint.textContent = "真实支撑预览未通过核验。请先打开 01 含真实支撑切片，不以仅显示本体的工程代替检查。";
  });
}

elements.supportVisibility.addEventListener("change", () => {
  previewController?.setSupportVisible(elements.supportVisibility.checked);
});
document.querySelectorAll("[data-preview-view]").forEach((button) => {
  button.addEventListener("click", () => previewController?.setView(button.dataset.previewView));
});

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

function requestPrintConfirmation({ title, message, confirmLabel }) {
  if (printConfirmationResolve) finishPrintConfirmation(false);
  elements.printConfirmTitle.textContent = title;
  elements.printConfirmMessage.textContent = message;
  elements.confirmPrintDispatch.textContent = confirmLabel;
  showDialog(elements.printConfirmDialog);
  setTimeout(() => elements.cancelPrintConfirm.focus(), 0);
  return new Promise((resolve) => { printConfirmationResolve = resolve; });
}

function finishPrintConfirmation(approved) {
  const resolve = printConfirmationResolve;
  if (!resolve) return;
  printConfirmationResolve = null;
  elements.printConfirmDialog.close();
  resolve(approved);
}

function renderDetails() {
  if (!currentSnapshot) return;
  elements.detailsJobId.textContent = currentSnapshot.job_id;
  const rows = [
    ["流程状态", STATUS_TEXT[currentSnapshot.status] || currentSnapshot.status],
    ["当前步骤", stageMeta(currentSnapshot.current_stage).title],
    ["打印模式", currentSnapshot.start_print_requested ? "Bambu 切片后直接打印" : "仅生成可打印文件"],
    ["控制状态", currentSnapshot.control?.status || "—"],
    ["创建时间", new Date(Number(currentSnapshot.created_unix || 0) * 1000).toLocaleString("zh-CN")],
  ];
  if (currentSnapshot.source_job_id) rows.splice(1, 0, ["来源任务", currentSnapshot.source_job_id]);
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
    NotAllowedError: "浏览器没有麦克风权限。请允许此网站使用麦克风，然后点“重新检测并录音”。",
    NotFoundError: "Windows 当前没有启用麦克风输入设备。请连接或启用麦克风，再点“重新检测并录音”；也可以直接导入录音文件。",
    NotReadableError: "麦克风存在，但无法读取。请关闭正在占用麦克风的会议或录音程序后重试。",
    OverconstrainedError: "刚才选择的麦克风已不可用，请重新选择设备。",
    SecurityError: "当前页面没有麦克风访问权限，请使用本机地址打开网站。",
  })[code] || "无法开始录音，请检查麦克风后重试；也可以导入已有录音文件。";
}

function microphoneErrorTitle(code) {
  return ({
    NotAllowedError: "需要麦克风权限",
    NotFoundError: "没有可用的麦克风输入",
    NotReadableError: "麦克风暂时不可用",
    OverconstrainedError: "所选麦克风已断开",
    SecurityError: "无法访问麦克风",
  })[code] || "语音输入没有启动";
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

function renderMicrophoneOptions(devices) {
  const previous = elements.microphoneSelect.value;
  audioInputDevices = devices;
  const fragment = document.createDocumentFragment();
  if (!devices.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "未检测到麦克风";
    fragment.append(option);
  } else {
    devices.forEach((device, index) => {
      const option = document.createElement("option");
      option.value = device.deviceId;
      option.textContent = device.label || `麦克风 ${index + 1}`;
      fragment.append(option);
    });
  }
  elements.microphoneSelect.replaceChildren(fragment);
  if (devices.some((device) => device.deviceId === previous)) {
    elements.microphoneSelect.value = previous;
  }
  elements.microphoneSelect.disabled = !devices.length;
  elements.microphoneSelect.classList.toggle("hidden", devices.length === 0);
}

async function refreshMicrophoneDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) {
    renderMicrophoneOptions([]);
    return [];
  }
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices.filter((device) => device.kind === "audioinput");
    renderMicrophoneOptions(inputs);
    return inputs;
  } catch (_) {
    renderMicrophoneOptions([]);
    return [];
  }
}

function setVoiceState(state, message = "", title = "") {
  voiceState = state;
  const busy = ["recording", "transcribing"].includes(state);
  elements.voiceButton.classList.toggle("recording", state === "recording");
  elements.voiceButton.classList.toggle("transcribing", state === "transcribing");
  elements.voiceButton.disabled = state === "transcribing"
    || !speechAvailable;
  elements.sendButton.disabled = busy;
  elements.voicePanel.dataset.state = state;
  elements.voicePanel.classList.toggle("hidden", state === "idle");
  elements.voiceWave.classList.toggle("hidden", !busy);
  elements.retryMicrophone.classList.toggle("hidden", !["error", "unavailable"].includes(state));
  elements.voiceFileAction.classList.toggle("hidden", busy);
  elements.microphoneSelect.classList.toggle("hidden", busy || audioInputDevices.length === 0);
  if (title) elements.voiceTitle.textContent = title;
  if (message) elements.voiceStatus.textContent = message;
  elements.voiceButton.setAttribute(
    "aria-label",
    state === "recording" ? "结束录音并转写" : state === "review" ? "继续语音输入" : "开始语音输入",
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
    const message = "当前浏览器不支持实时录音。你仍可导入录音文件，转成可修改文字。";
    setVoiceState("unavailable", message, "浏览器不支持实时录音");
    showToast(message);
    return;
  }
  try {
    const selectedDeviceId = elements.microphoneSelect.value;
    const audioConstraints = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    if (selectedDeviceId && selectedDeviceId !== "default") {
      audioConstraints.deviceId = { exact: selectedDeviceId };
    }
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: audioConstraints,
    });
    await refreshMicrophoneDevices();
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
      setVoiceState("error", "录音过程出现错误，请重新尝试。", "录音意外中断");
      showToast("录音过程出现错误，请重新尝试。");
    });
    mediaRecorder.addEventListener("stop", handleRecordingStopped, { once: true });
    mediaRecorder.start(250);
    setVoiceState("recording", "正在录音…再次点击麦克风结束并转写", "正在听你说");
    recordingTimer = setTimeout(() => {
      if (voiceState === "recording") {
        stopRecording();
        showToast("单次录音最长 90 秒，已自动开始转写。");
      }
    }, 90000);
  } catch (error) {
    closeMediaStream();
    await refreshMicrophoneDevices();
    const message = microphoneErrorMessage(error.name);
    setVoiceState(
      error.name === "NotFoundError" ? "unavailable" : "error",
      message,
      microphoneErrorTitle(error.name),
    );
    showToast(message);
  }
}

function stopRecording() {
  if (voiceState === "recording" && mediaRecorder?.state !== "inactive") {
    clearTimeout(recordingTimer);
    setVoiceState("transcribing", "正在使用本地 Whisper 转写…首次使用可能需要下载模型", "正在转成文字");
    mediaRecorder.stop();
  }
}

async function handleRecordingStopped() {
  closeMediaStream();
  const mimeType = mediaRecorder?.mimeType || audioChunks[0]?.type || "audio/webm";
  const recording = new Blob(audioChunks, { type: mimeType });
  audioChunks = [];
  mediaRecorder = null;
  await transcribeRecording(recording, mimeType);
}

function recordingContentType(file) {
  const provided = String(file.type || "").split(";", 1)[0].toLowerCase();
  const aliases = {
    "audio/x-m4a": "audio/x-m4a",
    "audio/mp3": "audio/mpeg",
    "audio/x-mpeg": "audio/mpeg",
    "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
  };
  if (aliases[provided]) return aliases[provided];
  if (["audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/aac"].includes(provided)) return provided;
  const suffix = file.name.toLowerCase().split(".").pop();
  return ({
    webm: "audio/webm", ogg: "audio/ogg", mp3: "audio/mpeg",
    m4a: "audio/mp4", mp4: "audio/mp4", wav: "audio/wav", aac: "audio/aac",
  })[suffix] || "application/octet-stream";
}

async function transcribeRecording(recording, contentType) {
  if (recording.size < 128) {
    const message = "录音内容太短，请重新说一次。";
    setVoiceState("error", message, "没有收到有效录音");
    showToast(message);
    return;
  }
  if (recording.size > 20 * 1024 * 1024) {
    const message = "录音文件超过 20 MB，请缩短录音后重试。";
    setVoiceState("error", message, "录音文件过大");
    showToast(message);
    return;
  }
  setVoiceState("transcribing", "正在使用本地 Whisper 转写，完成后文字不会自动发送。", "正在转成文字");
  try {
    const response = await fetch("/api/speech/transcribe?language=zh", {
      method: "POST",
      headers: { "Content-Type": contentType || recording.type || "audio/webm" },
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
    setVoiceState("idle");
    showToast("语音已转成文字。", { duration: 2400 });
  } catch (error) {
    setVoiceState("error", error.message, "语音转写没有完成");
    showToast(error.message);
  }
}

async function handleAudioFileSelected() {
  const file = elements.voiceFileInput.files?.[0];
  if (!file) return;
  try {
    await transcribeRecording(file, recordingContentType(file));
  } finally {
    elements.voiceFileInput.value = "";
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
elements.retryMicrophone.addEventListener("click", startRecording);
elements.voiceFileInput.addEventListener("change", handleAudioFileSelected);
elements.microphoneSelect.addEventListener("change", () => {
  const selected = audioInputDevices.find((device) => device.deviceId === elements.microphoneSelect.value);
  if (selected) showToast(`已选择：${selected.label || "麦克风"}`, { compact: true, duration: 1600 });
});
elements.newJobButton.addEventListener("click", resetComposer);
elements.history.addEventListener("click", (event) => {
  const action = event.target.closest("[data-history-action]");
  if (action) {
    if (action.dataset.historyAction === "delete") deleteConversation(action.dataset.jobId, action.dataset.jobTitle);
    else updatePinned(action.dataset.jobId, action.dataset.historyAction);
    return;
  }
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
elements.cancelDelete.addEventListener("click", () => {
  pendingDeleteConversation = null;
  elements.deleteDialog.close();
});
elements.confirmDelete.addEventListener("click", confirmDeleteConversation);
elements.deleteDialog.addEventListener("close", () => { pendingDeleteConversation = null; });
elements.retryButton.addEventListener("click", () => runAction("retry", { start_print: false }));
elements.reprintButton.addEventListener("click", reprintStepOneFiles);
elements.printButton.addEventListener("click", async () => {
  if (!currentSnapshot?.delivery?.available) return;
  const approved = await requestPrintConfirmation({
    title: "发送到打印机并开始打印？",
    message: "当前 Bambu Studio 切片将上传到实体打印机，并发送一次启动指令。",
    confirmLabel: "确认发送并开打",
  });
  if (!approved) return;
  elements.printButton.disabled = true;
  try { await runAction("retry", { start_print: true }); }
  finally { elements.printButton.disabled = false; }
});
elements.cancelPrintConfirm.addEventListener("click", () => finishPrintConfirmation(false));
elements.confirmPrintDispatch.addEventListener("click", () => finishPrintConfirmation(true));
elements.printConfirmDialog.addEventListener("close", () => {
  if (printConfirmationResolve) {
    const resolve = printConfirmationResolve;
    printConfirmationResolve = null;
    resolve(false);
  }
});
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
    elements.voiceButton.disabled = !speechAvailable;
    elements.voiceButton.title = speechAvailable
      ? `本地 ${health.speech.model} 模型语音转文字`
      : "本地语音组件尚未安装";
    if (speechAvailable && browserRecordingAvailable) {
      const inputs = await refreshMicrophoneDevices();
      if (!inputs.length) {
        elements.voiceButton.title = "当前未检测到麦克风；点击查看恢复方式或导入录音";
      }
    } else if (speechAvailable) {
      elements.voiceButton.title = "当前浏览器不支持实时录音；点击后可以导入录音文件";
    }
    setPrinterStatus(health.printer || {});
  } catch (_) {
    elements.printerCaption.textContent = "本地服务尚未连接";
  }
  await loadJobs(false);
  pollTimer = setInterval(refreshCurrent, 900);
  setInterval(updateElapsed, 1000);
}

navigator.mediaDevices?.addEventListener?.("devicechange", async () => {
  const inputs = await refreshMicrophoneDevices();
  if (inputs.length && voiceState === "unavailable") {
    setVoiceState("idle");
    showToast("已检测到麦克风，可以开始录音。", { duration: 2600 });
  }
});

initialise();
