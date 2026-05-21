/**
 * popup.js — UI logic for AI Usage Monitor Credential Exporter
 *
 * Reads cookies via chrome.cookies API and captured headers from
 * chrome.storage.local (written by background.js), then bundles
 * everything into a single credentials.json download.
 */

// ─── Platform definitions ─────────────────────────────────────────────────────

const PLATFORMS = [
  {
    id: "claude",
    name: "Claude",
    cookieDomain: "claude.ai",
    cookieFile: "claude.ai_cookies.txt",
    headerFile: "claude-header.txt",
    guideUrl: "https://claude.ai/settings/usage",
    guideText: "在 Chrome 中打开 Claude 用量页，触发 usage API",
  },
  {
    id: "trae",
    name: "Trae",
    cookieDomain: "www.trae.ai",
    cookieFile: "www.trae.ai_cookies.txt",
    headerFile: "trae-header.txt",
    guideUrl: "https://www.trae.ai",
    guideText: "访问 trae.ai，打开任意页面触发 API 请求",
  },
  {
    id: "minimax",
    name: "MiniMax",
    cookieDomain: "platform.minimaxi.com",
    cookieDomains: ["platform.minimaxi.com", "www.minimaxi.com"],
    cookieFile: "platform.minimaxi.com_cookies.txt",
    headerFile: "minimax-header.txt",
    guideUrl: "https://platform.minimaxi.com",
    guideText: "访问 MiniMax 平台首页触发 API 请求",
  },
  {
    id: "kimi",
    name: "Kimi",
    cookieDomain: "www.kimi.com",
    cookieFile: "www.kimi.com_cookies.txt",
    guideUrl: "https://www.kimi.com",
    guideText: "访问 kimi.com 并登录",
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    cookieDomain: "platform.deepseek.com",
    cookieFile: "platform.deepseek.com_cookies.txt",
    headerFile: "deepseek-amount.txt",
    guideUrl: "https://platform.deepseek.com/usage",
    guideText: "访问 DeepSeek 用量页面触发 API 请求",
  },
  {
    id: "codex",
    name: "Codex",
    localApp: true,   // Reads from ~/Library/Application Support/Codex/Cache — no cookies needed
    guideUrl: "https://github.com/openai/codex",
    guideText: "安装并打开 Codex App 即可，无需 Cookie",
  },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function getCookies(domain) {
  return new Promise((resolve) => {
    chrome.cookies.getAll({ domain }, resolve);
  });
}

async function getPlatformCookies(p) {
  const domains = p.cookieDomains || [p.cookieDomain];
  const cookieGroups = await Promise.all(domains.map(getCookies));
  const seen = new Set();
  return cookieGroups.flat().filter((cookie) => {
    const key = `${cookie.domain}\n${cookie.path}\n${cookie.name}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function cookiesToNetscape(cookies) {
  const lines = [
    "# Netscape HTTP Cookie File",
    "# https://curl.haxx.se/rfc/cookie_spec.html",
    "# This is a generated file! Do not edit.",
    "",
  ];
  for (const c of cookies) {
    const domain = c.hostOnly ? c.domain : `.${c.domain.replace(/^\./, "")}`;
    const subdomains = !c.hostOnly ? "TRUE" : "FALSE";
    const secure = c.secure ? "TRUE" : "FALSE";
    const expiry = c.session ? "0" : Math.round(c.expirationDate || 0).toString();
    lines.push(`${domain}\t${subdomains}\t${c.path}\t${secure}\t${expiry}\t${c.name}\t${c.value}`);
  }
  return lines.join("\n");
}

async function getStorageKeys(keys) {
  return new Promise((resolve) => {
    chrome.storage.local.get(keys, resolve);
  });
}

// ─── Status check ─────────────────────────────────────────────────────────────

async function checkPlatformStatus(p) {
  // Local-app platforms (e.g. Codex) don't use cookies — server reads them directly from disk
  if (p.localApp) {
    return { ...p, status: "ready", statusText: "本地 App（无需配置）", cookies: [], headerCaptured: false, orgIdCaptured: false, deepseekSlots: 0 };
  }

  const cookies = await getPlatformCookies(p);
  const hasCookies = cookies.length > 0;

  let headerCaptured = false;
  let orgIdCaptured = false;

  if (p.headerFile) {
    const stored = await getStorageKeys([`header_${p.headerFile}`]);
    headerCaptured = !!stored[`header_${p.headerFile}`];
  }
  if (p.needsOrgId) {
    const stored = await getStorageKeys(["claude_org_id"]);
    orgIdCaptured = !!stored["claude_org_id"];
  }

  // DeepSeek: check the endpoint-specific slots
  let deepseekSlots = 0;
  if (p.id === "deepseek") {
    const stored = await getStorageKeys([
      "header_deepseek-summary.txt",
      "header_deepseek-cost.txt",
      "header_deepseek-amount.txt",
    ]);
    deepseekSlots = [
      stored["header_deepseek-summary.txt"],
      stored["header_deepseek-cost.txt"],
      stored["header_deepseek-amount.txt"],
    ].filter(Boolean).length;
  }

  // Determine overall status
  let status, statusText;

  const hasCapturedRequest = headerCaptured || deepseekSlots > 0;
  if (!hasCookies && !hasCapturedRequest) {
    status = "missing";
    statusText = "未登录";
  } else if (p.headerFile && !headerCaptured && p.id !== "deepseek") {
    status = "partial";
    statusText = "需要触发 API";
  } else if (p.needsOrgId && !orgIdCaptured) {
    status = "partial";
    statusText = "需要访问页面";
  } else if (p.id === "deepseek" && deepseekSlots === 0) {
    status = "partial";
    statusText = "需要触发 API";
  } else if (p.id === "deepseek" && deepseekSlots < 3) {
    status = "partial";
    statusText = `已捕获 ${deepseekSlots}/3 端点`;
  } else {
    status = "ready";
    statusText = p.headerFile || p.needsOrgId ? "已就绪" : "Cookie 已获取";
  }

  return { ...p, status, statusText, cookies, headerCaptured, orgIdCaptured, deepseekSlots };
}

// ─── Render ───────────────────────────────────────────────────────────────────

function renderPlatforms(statuses) {
  const container = document.getElementById("platforms");
  container.innerHTML = "";

  const needsAction = statuses.filter((s) => s.status !== "ready");

  statuses.forEach((s, idx) => {
    if (idx > 0) {
      const div = document.createElement("div");
      div.className = "divider";
      container.appendChild(div);
    }

    const row = document.createElement("div");
    row.className = "platform-row";
    row.innerHTML = `
      <div class="dot ${s.status}"></div>
      <div class="platform-name">${s.name}</div>
      <div class="platform-status ${s.status === "ready" ? "ready" : s.status === "partial" ? "partial" : "hint"}">
        ${s.statusText}
      </div>
    `;
    container.appendChild(row);
  });

  // Show guide for platforms needing action
  const guideBox = document.getElementById("guideBox");
  if (needsAction.length > 0) {
    const lines = needsAction
      .filter((s) => s.status !== "ready")
      .map((s) => `<a href="${s.guideUrl}" target="_blank"><strong>${s.name}</strong></a>：${s.guideText}`)
      .join("<br>");
    guideBox.innerHTML = lines;
    guideBox.style.display = "block";
  } else {
    guideBox.style.display = "none";
  }

  // Enable export button if at least some platforms have cookies
  const hasSome = statuses.some((s) => s.cookies.length > 0 || s.headerCaptured || s.deepseekSlots > 0);
  document.getElementById("btnExport").disabled = !hasSome;
}

// ─── Export ───────────────────────────────────────────────────────────────────

async function exportCredentials(statuses) {
  const statusMsg = document.getElementById("statusMsg");
  statusMsg.textContent = "正在打包…";
  statusMsg.className = "status-msg";

  try {
    // Collect all storage keys we might need
    const storageKeys = [
      "claude_org_id",
      "header_claude-header.txt",
      "header_trae-header.txt",
      "header_minimax-header.txt",
      "header_qwen-header.txt",
      "header_deepseek-header.txt",
      "header_deepseek-header2.txt",
      "header_deepseek-header3.txt",
      "header_deepseek-summary.txt",
      "header_deepseek-cost.txt",
      "header_deepseek-amount.txt",
    ];
    const stored = await getStorageKeys(storageKeys);

    const credentials = {
      _comment: "Generated by AI Usage Monitor Chrome Extension. Run: bash import-credentials.sh credentials.json",
      claude_org_id: stored["claude_org_id"] || "",
      cookies: {},
      headers: {},
    };

    // Cookies
    for (const p of statuses) {
      if (p.cookies.length > 0) {
        credentials.cookies[p.cookieFile] = cookiesToNetscape(p.cookies);
      }
    }

    // Headers
    const headerMap = {
      "claude-header.txt": "header_claude-header.txt",
      "trae-header.txt": "header_trae-header.txt",
      "minimax-header.txt": "header_minimax-header.txt",
      "qwen-header.txt": "header_qwen-header.txt",
      "deepseek-header.txt": "header_deepseek-header.txt",
      "deepseek-header2.txt": "header_deepseek-header2.txt",
      "deepseek-header3.txt": "header_deepseek-header3.txt",
      "deepseek-summary.txt": "header_deepseek-summary.txt",
      "deepseek-cost.txt": "header_deepseek-cost.txt",
      "deepseek-amount.txt": "header_deepseek-amount.txt",
    };
    for (const [filename, storageKey] of Object.entries(headerMap)) {
      if (stored[storageKey]) {
        credentials.headers[filename] = stored[storageKey];
      }
    }

    const json = JSON.stringify(credentials, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);

    await new Promise((resolve, reject) => {
      chrome.downloads.download(
        {
          url,
          filename: "credentials.json",
          saveAs: false,
          conflictAction: "overwrite",
        },
        (downloadId) => {
          URL.revokeObjectURL(url);
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
          } else {
            resolve(downloadId);
          }
        }
      );
    });

    statusMsg.textContent = "✓ credentials.json 已下载";
    statusMsg.className = "status-msg ok";
  } catch (err) {
    statusMsg.textContent = "导出失败：" + err.message;
    statusMsg.className = "status-msg err";
  }
}

// ─── Init ─────────────────────────────────────────────────────────────────────

let currentStatuses = [];

async function refresh() {
  document.getElementById("statusMsg").textContent = "检查中…";
  document.getElementById("statusMsg").className = "status-msg";

  currentStatuses = await Promise.all(PLATFORMS.map(checkPlatformStatus));
  renderPlatforms(currentStatuses);

  const ready = currentStatuses.filter((s) => s.status === "ready").length;
  const total = PLATFORMS.length;
  document.getElementById("statusMsg").textContent =
    ready === total ? `全部 ${total} 个平台已就绪` : `${ready}/${total} 个平台已就绪`;
  document.getElementById("statusMsg").className =
    ready === total ? "status-msg ok" : "status-msg";
}

document.getElementById("btnExport").addEventListener("click", () => {
  exportCredentials(currentStatuses);
});

document.getElementById("btnRefresh").addEventListener("click", () => {
  refresh();
});

// Auto-refresh on open
refresh();
