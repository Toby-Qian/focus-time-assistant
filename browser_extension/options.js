const tokenField = document.getElementById("token");
const browserField = document.getElementById("browser");
const status = document.getElementById("status");
chrome.storage.local.get(["token", "browserOverride"]).then(data => {
  tokenField.value = data.token || "";
  browserField.value = data.browserOverride || "";
});
document.getElementById("save").addEventListener("click", async () => {
  const token = tokenField.value.trim();
  if (!/^[A-Za-z0-9_-]{40,100}$/.test(token)) {
    status.textContent = "连接码格式有误，请从桌面程序重新复制。";
    return;
  }
  await chrome.storage.local.set({token, browserOverride: browserField.value});
  try {
    const ua = navigator.userAgent;
    const browser = browserField.value || (ua.includes("Edg/") ? "msedge.exe" : ua.includes("OPR/") ? "opera.exe" : "chrome.exe");
    const result = await fetch("http://127.0.0.1:17863/api/browser", {
      method: "POST", headers: {"Content-Type": "application/json", "X-Focus-Token": token},
      body: JSON.stringify({browser, host: "", title: "", focused: false}), signal: AbortSignal.timeout(3000)
    });
    status.textContent = result.ok ? "已连接。请切回你要浏览的网页，桌面程序会显示域名识别。" : "连接码不匹配，请重新复制。";
  } catch (_) {
    status.textContent = "已保存。暂时连接不上，请确认桌面程序正在运行。";
  }
});
