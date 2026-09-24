let csrfToken = "";
const statusMessage = document.getElementById("status-message");
const runtimeLabel = document.getElementById("instance-runtime");
let runtimeInstanceId = null;
let instanceStartedAt = null;
let runtimeRequestAt = 0;

function renderRuntime() {
  if (!runtimeInstanceId) {
    runtimeLabel.textContent = "Chưa có instance";
    return;
  }
  if (!instanceStartedAt) return;
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - instanceStartedAt));
  const hours = Math.floor(elapsed / 3600);
  const minutes = Math.floor((elapsed % 3600) / 60);
  const seconds = elapsed % 60;
  runtimeLabel.textContent = `${hours} giờ ${String(minutes).padStart(2, "0")} phút ${String(seconds).padStart(2, "0")} giây`;
}

async function loadInstanceRuntime(instanceId) {
  runtimeRequestAt = Date.now();
  try {
    const response = await fetch("/api/admin/instance-runtime");
    if (response.status === 401) return location.assign("/");
    if (!response.ok) throw new Error("Không thể lấy thời gian từ Vast.");
    const runtime = await response.json();
    if (runtimeInstanceId !== instanceId) return;
    instanceStartedAt = runtime.instance_id === instanceId && Number.isFinite(Number(runtime.started_at))
      && runtime.started_at != null ? Number(runtime.started_at) : null;
    runtimeLabel.textContent = instanceStartedAt ? "" : "Vast chưa cung cấp thời gian bắt đầu";
    if (instanceStartedAt) renderRuntime();
  } catch (_error) {
    if (runtimeInstanceId === instanceId) runtimeLabel.textContent = "Chưa lấy được thời gian từ Vast";
  }
}

let lastKnownStep = 0;
function renderProgress(phase, hasInstance) {
  const steps = ["Thuê máy", "Khởi động", "Kết nối SSH", "Nạp model", "Sẵn sàng"];
  const stepByPhase = {
    creating: 1, create_unknown: 1, provisioning: 2, connecting: 3,
    loading_model: 4, ready: 5, recovering: 3, offline: 3,
  };
  const destroying = ["destroying", "destroy_unknown", "destroy_error"].includes(phase);
  let step = stepByPhase[phase] || 0;
  if (phase === "error") step = Math.min(lastKnownStep || (hasInstance ? 3 : 1), 4);
  if (destroying) step = 5;
  if (phase === "idle") lastKnownStep = 0;
  else if (phase in stepByPhase) lastKnownStep = step;

  let label = step ? `Bước ${step}/5 · ${steps[step - 1]}` : "Chưa deploy";
  if (phase === "ready") label = "Sẵn sàng";
  if (phase === "create_unknown") label = "Bước 1/5 · Đối soát thuê máy";
  if (["recovering", "offline"].includes(phase)) label = "Bước 3/5 · Kết nối lại";
  if (phase === "error") label = "Deploy gặp lỗi";
  if (phase === "destroying") label = "Đang destroy instance";
  if (phase === "destroy_unknown") label = "Đang đối soát destroy";
  if (phase === "destroy_error") label = "Destroy gặp lỗi";

  const track = document.getElementById("progress-track");
  const fill = document.getElementById("progress-fill");
  document.getElementById("progress-step").textContent = label;
  document.getElementById("progress-count").textContent = `${step}/5 bước`;
  track.setAttribute("aria-valuenow", String(step));
  track.setAttribute("aria-valuetext", label);
  fill.style.width = `${step * 20}%`;
  fill.classList.toggle("running", step > 0 && step < 5 && !["error", "offline"].includes(phase));
  fill.classList.toggle("ready", phase === "ready");
  fill.classList.toggle("failed", ["error", "destroy_error"].includes(phase));
}

setInterval(renderRuntime, 1000);

async function loadDashboard() {
  try {
    const sessionResponse = await fetch("/api/admin/session");
    if (sessionResponse.status === 401) {
      location.assign("/");
      return;
    }
    if (!sessionResponse.ok) throw new Error("Không thể kiểm tra phiên đăng nhập.");
    csrfToken = (await sessionResponse.json()).csrf_token;

    const stateResponse = await fetch("/api/admin/state");
    if (stateResponse.status === 401) {
      location.assign("/");
      return;
    }
    if (!stateResponse.ok) throw new Error("Không thể tải trạng thái deployment.");
    const state = await stateResponse.json();
    renderDeployment(state);
    await loadSettings();
    await loadModels();
  } catch (error) {
    statusMessage.textContent = error.message || "Không thể tải dashboard.";
  }
}

function renderDeployment(state) {
  const phaseLabels = {
    idle: "Chưa deploy", creating: "Đang thuê máy", create_unknown: "Đang đối soát",
    provisioning: "Đang khởi động", connecting: "Đang kết nối", loading_model: "Đang nạp model",
    ready: "Sẵn sàng", recovering: "Đang khôi phục", offline: "Mất kết nối",
    error: "Lỗi", destroying: "Đang destroy", destroy_unknown: "Đang đối soát",
    destroy_error: "Lỗi destroy",
  };
  document.getElementById("phase").textContent = phaseLabels[state.phase] || state.phase;
  statusMessage.textContent = state.message;
  renderProgress(state.phase, Boolean(state.instance_id));
  if (state.instance_id !== runtimeInstanceId) {
    runtimeInstanceId = state.instance_id;
    instanceStartedAt = null;
    if (runtimeInstanceId) runtimeLabel.textContent = "Đang lấy thời gian từ Vast...";
    renderRuntime();
    if (runtimeInstanceId) loadInstanceRuntime(runtimeInstanceId);
  } else if (runtimeInstanceId && !instanceStartedAt && Date.now() - runtimeRequestAt > 30000) {
    loadInstanceRuntime(runtimeInstanceId);
  }
  document.getElementById("instance-details").textContent = state.instance_id
    ? `Instance #${state.instance_id} · ${state.model_id || ""} · $${Number(state.price_hour || 0).toFixed(3)}/giờ`
    : "";
  const deployError = document.getElementById("deploy-error");
  deployError.textContent = state.error || "";
  deployError.hidden = !state.error;
  document.getElementById("retry-setup").disabled = !state.operation_id ||
    ["ready", "creating", "provisioning", "connecting", "loading_model", "recovering", "destroying", "destroy_unknown", "destroy_error"].includes(state.phase);
  document.getElementById("destroy-instance").disabled = !state.instance_id || state.phase === "destroying";
}

setInterval(async () => {
  if (!csrfToken) return;
  try {
    const response = await fetch("/api/admin/state");
    if (response.status === 401) return location.assign("/");
    if (response.ok) renderDeployment(await response.json());
  } catch (_error) {
    // The current state remains visible while the dashboard connection recovers.
  }
}, 3000);

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    const selected = button.dataset.tab;
    document.querySelectorAll(".tab").forEach((tab) => {
      const active = tab.dataset.tab === selected;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    document.getElementById("panel-deploy").hidden = selected !== "deploy";
    document.getElementById("panel-keys").hidden = selected !== "keys";
    document.getElementById("panel-integration").hidden = selected !== "integration";
    if (selected === "keys" && csrfToken) loadKeys();
    if (selected === "deploy" && csrfToken) loadSettings();
  });
});

function renderIntegration() {
  const baseUrl = `${location.origin}/v1`;
  const slash = String.fromCharCode(92);
  const body = JSON.stringify({
    model: "qwen-3.8",
    messages: [{role: "user", content: "Xin chào"}],
    max_tokens: 128,
  });
  document.getElementById("integration-base-url").textContent = baseUrl;
  document.getElementById("integration-chat-curl").textContent = [
    `curl -X POST '${baseUrl}/chat/completions' ${slash}`,
    `  -H 'Authorization: Bearer YOUR_USER_KEY' ${slash}`,
    `  -H 'Content-Type: application/json' ${slash}`,
    `  -d '${body}'`,
  ].join("\n");
  document.getElementById("integration-models-curl").textContent =
    `curl '${baseUrl}/models' -H 'Authorization: Bearer YOUR_USER_KEY'`;
}

renderIntegration();

document.getElementById("copy-integration-md").addEventListener("click", async () => {
  const tick = String.fromCharCode(96);
  const fence = tick.repeat(3);
  const baseUrl = document.getElementById("integration-base-url").textContent;
  const chatCurl = document.getElementById("integration-chat-curl").textContent;
  const modelsCurl = document.getElementById("integration-models-curl").textContent;
  const response = document.getElementById("integration-chat-response").textContent.trim();
  const markdown = [
    "# Tích hợp Vast LLM",
    "",
    `- Base URL: ${tick}${baseUrl}${tick}`,
    `- Model: ${tick}qwen-3.8${tick}`,
    "- Dùng API key của user tạo trong tab API Keys làm Bearer token. Thay YOUR_USER_KEY trong ví dụ; không dùng Vast API key.",
    "- Model cần ở trạng thái ready trong tab Deploy. URL 127.0.0.1 chỉ truy cập được từ máy chạy dashboard; từ máy khác cần SSH forwarding hoặc mạng riêng phù hợp.",
    "",
    "## Chat completion",
    "",
    `Endpoint: ${tick}POST ${baseUrl}/chat/completions${tick}`,
    "",
    `${fence}bash`,
    chatCurl,
    fence,
    "",
    "Response ví dụ (rút gọn):",
    "",
    `${fence}json`,
    response,
    fence,
    "",
    "## Danh sách model",
    "",
    `Endpoint: ${tick}GET ${baseUrl}/models${tick}`,
    "",
    `${fence}bash`,
    modelsCurl,
    fence,
    "",
    `Thêm ${tick}"stream": true${tick} vào JSON để nhận phản hồi từng phần. Key thiếu hoặc sai nhận HTTP 401; model chưa sẵn sàng nhận HTTP 503.`,
  ].join("\n");
  const status = document.getElementById("copy-integration-status");
  try {
    await navigator.clipboard.writeText(markdown);
    status.textContent = "Đã sao chép hướng dẫn Markdown vào clipboard.";
    status.classList.remove("error");
  } catch (_error) {
    status.textContent = "Không thể sao chép tự động. Hãy dùng trình duyệt trên localhost hoặc sao chép các ví dụ bên dưới.";
    status.classList.add("error");
  }
  status.hidden = false;
});

const settingsMessage = document.getElementById("settings-message");
const vastCredit = document.getElementById("vast-credit");
const vastCreditMeta = document.getElementById("vast-credit-meta");
const refreshVastCredit = document.getElementById("refresh-vast-credit");
const usdFormatter = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
const creditCacheKey = "vastllm.credit.v1";
let vastConfigured = false;
let creditKeyHint = null;
let cachedCredit = null;
let creditController = null;
let creditRefreshState = "ready";

function renderCreditAge() {
  if (!cachedCredit) return;
  const seconds = Math.max(0, Math.floor((Date.now() - cachedCredit.updatedAt) / 1000));
  const prefix = creditRefreshState === "loading" ? "Đang làm mới · "
    : creditRefreshState === "error" ? "Không làm mới được · " : "";
  vastCreditMeta.textContent = `${prefix}Cập nhật ${seconds} giây trước`;
}

setInterval(renderCreditAge, 1000);

function readCachedCredit(hint) {
  if (!hint) return null;
  try {
    const saved = JSON.parse(localStorage.getItem(creditCacheKey));
    const age = Date.now() - saved?.updatedAt;
    if (saved?.hint === hint && Number.isFinite(saved.amount) &&
        Number.isFinite(saved.updatedAt) && age >= 0 && age < 24 * 60 * 60 * 1000) {
      return saved;
    }
  } catch (_error) {
    // Private browsing may disable storage; live credit still works.
  }
  return null;
}

function renderCachedCredit() {
  if (!cachedCredit) return;
  vastCredit.textContent = usdFormatter.format(cachedCredit.amount);
  renderCreditAge();
}

async function loadVastCredit() {
  if (!vastConfigured) return;
  creditController?.abort();
  const controller = new AbortController();
  creditController = controller;
  refreshVastCredit.disabled = true;
  creditRefreshState = "loading";
  if (cachedCredit) renderCreditAge();
  else vastCredit.textContent = "Đang cập nhật...";
  try {
    const response = await fetch("/api/admin/vast-credit", {signal: controller.signal});
    if (response.status === 401) return location.assign("/");
    if (!response.ok) throw new Error("Không lấy được credit từ Vast");
    const data = await response.json();
    const amount = Number(data.credit_usd);
    if (!Number.isFinite(amount)) throw new Error("Vast trả credit không hợp lệ");
    if (creditController === controller) {
      cachedCredit = {hint: creditKeyHint, amount, updatedAt: Date.now()};
      creditRefreshState = "ready";
      renderCachedCredit();
      try { localStorage.setItem(creditCacheKey, JSON.stringify(cachedCredit)); } catch (_error) {
        // The current page still shows the fresh amount when storage is unavailable.
      }
    }
  } catch (error) {
    if (creditController === controller) {
      creditRefreshState = "error";
      if (cachedCredit) renderCreditAge();
      else vastCredit.textContent = error.message || "Không lấy được credit từ Vast";
    }
  } finally {
    if (creditController === controller) {
      refreshVastCredit.disabled = false;
      creditController = null;
    }
  }
}

refreshVastCredit.addEventListener("click", loadVastCredit);
setInterval(() => {
  if (csrfToken && vastConfigured && !document.getElementById("panel-deploy").hidden) loadVastCredit();
}, 60000);

function showSettingsMessage(message, error = false) {
  settingsMessage.textContent = message;
  settingsMessage.classList.toggle("error", error);
  settingsMessage.hidden = !message;
}

async function loadSettings() {
  try {
    const response = await fetch("/api/admin/settings");
    if (response.status === 401) return location.assign("/");
    if (!response.ok) throw new Error("Không thể tải trạng thái Vast API key.");
    const settings = await response.json();
    const source = settings.vast_api_key_saved
      ? "Đã lưu trong dashboard"
      : settings.vast_api_key_configured ? "Đã cấu hình qua môi trường hoặc Vast CLI" : "Chưa cấu hình";
    document.getElementById("vast-key-status").textContent = settings.vast_api_key_hint
      ? `${source} · ${settings.vast_api_key_hint}` : source;
    vastConfigured = settings.vast_api_key_configured;
    refreshVastCredit.disabled = !vastConfigured;
    if (vastConfigured) {
      const hint = settings.vast_api_key_hint || "";
      if (creditKeyHint !== hint) {
        creditController?.abort();
        creditKeyHint = hint;
        cachedCredit = readCachedCredit(hint);
        creditRefreshState = "ready";
      }
      if (cachedCredit) renderCachedCredit();
      else vastCreditMeta.textContent = "";
      loadVastCredit();
    }
    else {
      creditController?.abort();
      creditController = null;
      creditKeyHint = null;
      cachedCredit = null;
      creditRefreshState = "ready";
      vastCredit.textContent = "Cần cấu hình Vast API key";
      vastCreditMeta.textContent = "";
    }
  } catch (error) {
    showSettingsMessage(error.message || "Không thể tải trạng thái Vast API key.", true);
  }
}

document.getElementById("vast-key-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("vast-api-key");
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  showSettingsMessage("");
  try {
    const response = await fetch("/api/admin/settings/vast-api-key", {
      method: "PUT",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
      body: JSON.stringify({api_key: input.value.trim()}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể lưu Vast API key.");
    input.value = "";
    creditController?.abort();
    creditController = null;
    creditKeyHint = null;
    cachedCredit = null;
    creditRefreshState = "ready";
    try { localStorage.removeItem(creditCacheKey); } catch (_error) { /* Storage may be unavailable. */ }
    await loadSettings();
    showSettingsMessage("Đã lưu Vast API key. Có thể tìm máy và deploy ngay.");
  } catch (error) {
    showSettingsMessage(error.message || "Không thể lưu Vast API key.", true);
  } finally {
    button.disabled = false;
  }
});

const keysError = document.getElementById("keys-error");
function showKeysError(message) {
  keysError.textContent = message;
  keysError.hidden = !message;
}

async function loadKeys() {
  try {
    const response = await fetch("/api/admin/keys");
    if (!response.ok) throw new Error("Không thể tải danh sách API key.");
    const rows = (await response.json()).items;
    const list = document.getElementById("keys-list");
    list.replaceChildren();
    for (const item of rows) {
      const tr = document.createElement("tr");
      for (const value of [
        item.user,
        item.prefix + "…",
        new Date(item.created_at * 1000).toLocaleString(),
        item.revoked_at ? "Đã thu hồi" : "Đang dùng",
      ]) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      }
      const action = document.createElement("td");
      if (!item.revoked_at) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "danger";
        button.textContent = "Thu hồi";
        button.addEventListener("click", () => revokeKey(item.id, item.user));
        action.appendChild(button);
      }
      tr.appendChild(action);
      list.appendChild(tr);
    }
    showKeysError("");
  } catch (error) {
    showKeysError(error.message || "Không thể tải API key.");
  }
}

document.getElementById("key-create").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!csrfToken) return;
  const user = document.getElementById("key-user").value.trim();
  try {
    const response = await fetch("/api/admin/keys", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
      body: JSON.stringify({user}),
    });
    if (!response.ok) throw new Error("Không thể tạo API key.");
    const created = await response.json();
    document.getElementById("new-key-value").textContent = created.key;
    document.getElementById("copy-key-status").hidden = true;
    document.getElementById("new-key").hidden = false;
    document.getElementById("key-user").value = "";
    showKeysError("");
    await loadKeys();
  } catch (error) {
    showKeysError(error.message || "Không thể tạo API key.");
  }
});

document.getElementById("copy-key").addEventListener("click", async () => {
  const key = document.getElementById("new-key-value").textContent;
  if (!key) return;
  const status = document.getElementById("copy-key-status");
  try {
    await navigator.clipboard.writeText(key);
    status.textContent = "Đã sao chép vào clipboard.";
  } catch (_error) {
    status.textContent = "Không thể sao chép tự động. Hãy chọn key và sao chép thủ công.";
  }
  status.hidden = false;
});

async function revokeKey(id, user) {
  if (!confirm(`Thu hồi API key của ${user}?`)) return;
  try {
    const response = await fetch(`/api/admin/keys/${id}`, {
      method: "DELETE", headers: {"X-CSRF-Token": csrfToken},
    });
    if (!response.ok) throw new Error("Không thể thu hồi API key.");
    document.getElementById("new-key").hidden = true;
    document.getElementById("new-key-value").textContent = "";
    document.getElementById("copy-key-status").hidden = true;
    await loadKeys();
  } catch (error) {
    showKeysError(error.message || "Không thể thu hồi API key.");
  }
}

let deployModelId = "";
let currentOffers = [];
let currentOfferChoice = null;

async function loadModels() {
  const response = await fetch("/api/admin/models");
  if (!response.ok) throw new Error("Không thể tải model deploy.");
  const presets = (await response.json()).presets;
  if (presets.length !== 1) throw new Error("Danh sách model deploy không hợp lệ.");
  const preset = presets[0];
  deployModelId = preset.id;
  document.getElementById("deploy-model").textContent = `Qwen3.8-27B uncensored · 256K context · ${preset.id}`;
  document.getElementById("min-vram").value = preset.min_vram_gb;
  document.getElementById("disk-gb").value = preset.disk_gb;
  document.getElementById("search-offers").disabled = false;
}

function deploymentChoice() {
  return {
    model_id: deployModelId,
    min_vram_gb: Number(document.getElementById("min-vram").value),
    disk_gb: Number(document.getElementById("disk-gb").value),
  };
}

function showDeployError(message) {
  const error = document.getElementById("deploy-error");
  error.textContent = message;
  error.hidden = !message;
}

async function searchOffers() {
  const button = document.getElementById("search-offers");
  button.disabled = true;
  currentOffers = [];
  currentOfferChoice = null;
  document.getElementById("offers-toolbar").hidden = true;
  document.getElementById("offers").replaceChildren();
  const choice = deploymentChoice();
  const params = new URLSearchParams(choice);
  try {
    const response = await fetch(`/api/admin/offers?${params}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không thể tìm offer.");
    currentOffers = result.offers;
    currentOfferChoice = choice;
    renderOffers();
    showDeployError("");
  } catch (error) {
    showDeployError(error.message || "Không thể tìm máy.");
  } finally {
    button.disabled = false;
  }
}

function renderOffers() {
  const list = document.getElementById("offers");
  const toolbar = document.getElementById("offers-toolbar");
  list.replaceChildren();
  toolbar.hidden = currentOffers.length === 0;
  if (!currentOffers.length) {
    list.textContent = "Không có máy phù hợp.";
    return;
  }
  document.getElementById("offers-count").textContent = `${currentOffers.length} máy phù hợp`;
  const sort = document.getElementById("offers-sort").value;
  const metric = (offer) => {
    if (sort === "price") return Number(offer.price_hour);
    return sort === "speed" ? offer.estimated_tps : offer.tokens_per_dollar;
  };
  const offers = [...currentOffers].sort((a, b) => {
    const aMetric = metric(a);
    const bMetric = metric(b);
    if (aMetric == null) return bMetric == null ? Number(a.price_hour) - Number(b.price_hour) : 1;
    if (bMetric == null) return -1;
    const difference = sort === "price" ? aMetric - bMetric : bMetric - aMetric;
    return difference || Number(a.price_hour) - Number(b.price_hour);
  });
  const scroll = document.createElement("div");
  scroll.className = "offer-table-scroll";
  const table = document.createElement("table");
  table.className = "offer-table";
  const header = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const label of ["GPU / offer", "VRAM", "BW GPU", "Tok/s ≈", "$/giờ", "Tok/$ ≈", "Tin cậy", "Vị trí", ""]) {
    const th = document.createElement("th");
    th.textContent = label;
    headerRow.appendChild(th);
  }
  header.appendChild(headerRow);
  const body = document.createElement("tbody");
  for (const offer of offers) {
    const row = document.createElement("tr");
    for (const value of [
      `${offer.gpu_name} · #${offer.id}`,
      `${Number(offer.gpu_ram_gb).toFixed(1)} GB`,
      offer.gpu_mem_bw == null ? "—" : `${Math.round(Number(offer.gpu_mem_bw))} GB/s`,
      offer.estimated_tps == null ? "—" : Number(offer.estimated_tps).toFixed(1),
      `$${Number(offer.price_hour).toFixed(3)}`,
      offer.tokens_per_dollar == null ? "—" : Math.round(Number(offer.tokens_per_dollar)).toLocaleString("vi-VN"),
      `${(Number(offer.reliability) * 100).toFixed(1)}%`,
      offer.location || "—",
    ]) {
      const td = document.createElement("td");
      td.textContent = value;
      row.appendChild(td);
    }
    const action = document.createElement("td");
    const deploy = document.createElement("button");
    deploy.type = "button";
    deploy.textContent = "Thuê và deploy";
    deploy.addEventListener("click", () => deployOffer(offer, currentOfferChoice));
    action.appendChild(deploy);
    row.appendChild(action);
    body.appendChild(row);
  }
  table.append(header, body);
  scroll.appendChild(table);
  list.appendChild(scroll);
}

document.getElementById("offers-sort").addEventListener("change", renderOffers);

async function deployOffer(offer, choice) {
  if (!confirm(`Thuê ${offer.gpu_name} với giá $${Number(offer.price_hour).toFixed(3)}/giờ và deploy ${choice.model_id}?`)) return;
  try {
    const response = await fetch("/api/admin/deploy", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
      body: JSON.stringify({offer_id: offer.id, ...choice}),
    });
    const state = await response.json();
    if (!response.ok) throw new Error(state.detail || "Không thể deploy.");
    renderDeployment(state);
    currentOffers = [];
    currentOfferChoice = null;
    document.getElementById("offers-toolbar").hidden = true;
    document.getElementById("offers").replaceChildren();
  } catch (error) {
    showDeployError(error.message || "Không thể deploy.");
  }
}

document.getElementById("search-offers").addEventListener("click", searchOffers);

document.getElementById("retry-setup").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/admin/retry", {
      method: "POST", headers: {"X-CSRF-Token": csrfToken},
    });
    const state = await response.json();
    if (!response.ok) throw new Error(state.detail || "Không thể thiết lập lại.");
    renderDeployment(state);
  } catch (error) {
    showDeployError(error.message || "Không thể thiết lập lại.");
  }
});

document.getElementById("destroy-instance").addEventListener("click", async () => {
  if (!confirm("Destroy instance này trên Vast? Chi phí chỉ dừng khi Vast xác nhận destroy.")) return;
  try {
    const response = await fetch("/api/admin/destroy", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
      body: JSON.stringify({confirm: true}),
    });
    const state = await response.json();
    if (!response.ok) throw new Error(state.detail || "Không thể destroy instance.");
    renderDeployment(state);
  } catch (error) {
    showDeployError(error.message || "Không thể destroy instance.");
  }
});

loadDashboard();
