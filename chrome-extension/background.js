/**
 * background.js — service worker for AI Usage Monitor Credential Exporter
 *
 * Intercepts authenticated API requests from supported platforms
 * and stores the request headers (especially auth tokens) in chrome.storage.local.
 * These are later packaged by popup.js into a credentials.json export.
 *
 * NOTE: Kimi only needs cookies, so there is no request header to capture.
 * Codex is handled server-side by reading local app data directly.
 * This background worker captures headers for: Claude, Trae, MiniMax, DeepSeek.
 */

// ─── Platform definitions ─────────────────────────────────────────────────────

const PLATFORMS = {
  claude: {
    name: "Claude",
    cookieDomain: "claude.ai",
    captureUrlPattern: "https://claude.ai/api/organizations/",
    headerFile: "claude-header.txt",
  },
  trae: {
    name: "Trae",
    cookieDomain: "www.trae.ai",
    captureUrl: "https://api-sg-central.trae.ai/trae/api/v1/pay/user_current_entitlement_list",
    headerFile: "trae-header.txt",
    captureHeaders: ["authorization", "x-trae-token", "x-token"],
  },
  minimax: {
    name: "MiniMax",
    cookieDomain: "platform.minimaxi.com",
    captureUrl: "https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains",
    headerFile: "minimax-header.txt",
    captureHeaders: ["authorization", "x-auth-token"],
  },
  kimi: {
    name: "Kimi",
    cookieDomain: "www.kimi.com",
    // Auth comes from the kimi-auth cookie; no separate header needed
  },
  deepseek: {
    name: "DeepSeek",
    cookieDomain: "platform.deepseek.com",
    // Multiple endpoints — captured by exact URL path, not request order
    captureUrlPrefix: "https://platform.deepseek.com/api/",
    captureHeaders: ["authorization", "x-ds-token"],
    deepseekEndpoints: {
      "deepseek-summary.txt": "/api/v0/users/get_user_summary",
      "deepseek-cost.txt":    "/api/v0/usage/cost",
      "deepseek-amount.txt":  "/api/v0/usage/amount",
    },
  },
};

// ─── Request interception ─────────────────────────────────────────────────────

const WATCH_URLS = [
  "https://claude.ai/api/organizations/*/usage",
  "https://api-sg-central.trae.ai/*",
  "https://www.minimaxi.com/v1/api/*",
  "https://platform.deepseek.com/api/*",
];

chrome.webRequest.onSendHeaders.addListener(
  (details) => {
    const { url, method, requestHeaders = [] } = details;

    // Build a header-txt formatted string from the raw request headers
    const toHeaderTxt = (headers) => {
      const urlObj = new URL(url);
      const lines = [
        "Request URL", url,
        "Request Method", method,
        ":authority", urlObj.hostname,
        ":method",    method,
        ":path",      urlObj.pathname + urlObj.search,
        ":scheme",    "https",
      ];
      const SKIP = new Set([
        ":authority", ":method", ":path", ":scheme",
        "accept-encoding", "content-length", "connection",
      ]);
      for (const h of headers) {
        if (!SKIP.has(h.name.toLowerCase())) {
          lines.push(h.name, h.value);
        }
      }
      return lines.join("\n");
    };

    // ── Claude ────────────────────────────────────────────────────────────
    const urlObj = new URL(url);
    if (urlObj.hostname === "claude.ai" && urlObj.pathname.match(/^\/api\/organizations\/[^/]+\/usage$/)) {
      chrome.storage.local.set({ "header_claude-header.txt": toHeaderTxt(requestHeaders) });
      return;
    }

    // ── Trae ──────────────────────────────────────────────────────────────
    if (
      urlObj.hostname === "api-sg-central.trae.ai" &&
      urlObj.pathname === "/trae/api/v1/pay/user_current_entitlement_list"
    ) {
      chrome.storage.local.set({ "header_trae-header.txt": toHeaderTxt(requestHeaders) });
      return;
    }

    // ── MiniMax ───────────────────────────────────────────────────────────
    if (url.includes("minimaxi.com/v1/api")) {
      chrome.storage.local.set({ "header_minimax-header.txt": toHeaderTxt(requestHeaders) });
      return;
    }

    // ── DeepSeek: capture the specific endpoints the parser needs ─────────
    if (url.includes("platform.deepseek.com/api/")) {
      const path = new URL(url).pathname;
      const endpointMap = PLATFORMS.deepseek.deepseekEndpoints;
      const matched = Object.entries(endpointMap).find(([, endpointPath]) => path === endpointPath);
      if (!matched) return;
      const [fileName] = matched;
      chrome.storage.local.set({ [`header_${fileName}`]: toHeaderTxt(requestHeaders) });
      return;
    }
  },
  { urls: WATCH_URLS },
  ["requestHeaders", "extraHeaders"]
);
