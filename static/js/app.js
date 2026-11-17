/* FileForge 公共前端工具 */
window.FF = (function () {
  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function toast(message, type) {
    const root = document.getElementById('toast');
    if (!root) return;
    const item = document.createElement('div');
    item.className = 'toast-item ' + (type || 'info');
    item.textContent = message;
    root.appendChild(item);
    requestAnimationFrame(() => item.classList.add('show'));
    setTimeout(() => {
      item.classList.remove('show');
      setTimeout(() => item.remove(), 250);
    }, 3600);
  }

  async function api(url, options) {
    const opts = Object.assign({ credentials: 'same-origin' }, options || {});
    const headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
    if (opts.body && !(opts.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }
    opts.headers = headers;
    let response;
    try {
      response = await fetch(url, opts);
    } catch (networkError) {
      const error = new Error('网络错误，请检查连接');
      error.status = 0;
      throw error;
    }

    let data = null;
    const contentType = response.headers.get('Content-Type') || '';
    if (contentType.includes('application/json')) {
      try {
        data = await response.json();
      } catch (parseError) {
        data = null;
      }
    }

    if (!response.ok) {
      const error = new Error((data && (data.error || data.message)) || ('请求失败 (' + response.status + ')'));
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function formatSize(bytes) {
    const value = Number(bytes) || 0;
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let index = 0;
    let num = value;
    while (num >= 1024 && index < units.length - 1) {
      num /= 1024;
      index += 1;
    }
    return (index === 0 ? num.toFixed(0) : num.toFixed(2)) + ' ' + units[index];
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制到剪贴板', 'ok');
    } catch (error) {
      toast('复制失败，请手动选择复制', 'err');
    }
  }

  function uuidShort(id) {
    return String(id || '').slice(0, 10);
  }

  return { escapeHtml, toast, api, formatSize, copyText, uuidShort };
})();

document.addEventListener('DOMContentLoaded', function () {
  const logoutBtn = document.getElementById('logout-btn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', async function () {
      try {
        await FF.api('/api/logout', { method: 'POST' });
      } catch (error) {
        /* 忽略登出失败 */
      }
      location.href = '/';
    });
  }
});
