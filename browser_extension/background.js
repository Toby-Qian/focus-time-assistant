/* MV3: event updates + short heartbeat while alive + alarms to wake the worker. */
let sending = false;
const sentIcons = new Set();  // hosts whose icon was already offered this session
let defaultIcon = null;       // the browser's generic globe, so it is never sent as a site icon

function base64(buffer) {
  let text = ""; const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i++) text += String.fromCharCode(bytes[i]);
  return btoa(text);
}
async function cachedIcon(pageUrl) {
  // MV3 "favicon" permission: served from the browser's local favicon cache, no network request.
  const url = chrome.runtime.getURL(`/_favicon/?pageUrl=${encodeURIComponent(pageUrl)}&size=32`);
  const response = await fetch(url);
  return response.ok ? base64(await response.arrayBuffer()) : "";
}
async function sendIcon(token, host, pageUrl) {
  if (!host || sentIcons.has(host)) return;
  sentIcons.add(host);
  try {
    defaultIcon ??= await cachedIcon("https://example.invalid/");
    const png = await cachedIcon(pageUrl);
    if (!png || png === defaultIcon || png.length > 40000) return;
    await fetch("http://127.0.0.1:17863/api/icon", {
      method: "POST", headers: {"Content-Type": "application/json", "X-Focus-Token": token},
      body: JSON.stringify({host, png}), signal: AbortSignal.timeout(2500)
    });
  } catch (_) { /* icons are optional */ }
}
function browserName() {
  const ua = navigator.userAgent;
  if (ua.includes("Edg/")) return "msedge.exe";
  if (ua.includes("OPR/")) return "opera.exe";
  return "chrome.exe";
}
async function report() {
  if (sending) return;
  sending = true;
  try {
    const {token, browserOverride} = await chrome.storage.local.get(["token", "browserOverride"]);
    if (!token) return;
    const win = await chrome.windows.getLastFocused();
    const [tab] = await chrome.tabs.query({active: true, windowId: win.id});
    let host = "";
    if (tab?.url) {
      const url = new URL(tab.url);
      if (["http:", "https:"].includes(url.protocol)) host = url.hostname.toLowerCase().replace(/\.$/, "");
    }
    const result = await fetch("http://127.0.0.1:17863/api/browser", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Focus-Token": token},
      body: JSON.stringify({browser: browserOverride || browserName(), host,
        title: (tab?.title || "").slice(0, 2048), focused: Boolean(win.focused)}),
      signal: AbortSignal.timeout(2500)
    });
    if (!result.ok) throw new Error("pairing");
    await chrome.action.setBadgeText({text: ""});
    if (host && tab.status === "complete") sendIcon(token, host, tab.url);
  } catch (_) {
    await chrome.action.setBadgeBackgroundColor({color: "#cd674a"});
    await chrome.action.setBadgeText({text: "!"});
  } finally {
    sending = false;
  }
}
chrome.tabs.onActivated.addListener(report);
chrome.tabs.onUpdated.addListener((_id, change) => {
  if (change.url || change.title || change.status === "complete") report();
});
chrome.tabs.onRemoved.addListener(report);
chrome.windows.onFocusChanged.addListener(report);
chrome.storage.onChanged.addListener(report);
chrome.alarms.onAlarm.addListener(report);
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("heartbeat", {periodInMinutes: 0.5});
  chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("heartbeat", {periodInMinutes: 0.5});
  report();
});
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.alarms.create("heartbeat", {periodInMinutes: 0.5});
setInterval(report, 2000);
report();
