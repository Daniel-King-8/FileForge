/* 任务详情：SSE 进度、日志、取消/重试/删除/分享 */
(function () {
  const task = window.FF_TASK || {};
  const badge = document.getElementById('status-badge');
  const fill = document.getElementById('progress-fill');
  const progressText = document.getElementById('progress-text');
  const errorBox = document.getElementById('error-box');
  const logEl = document.getElementById('task-log');
  let logTimer = null;

  function applyState(status, progress, error) {
    if (badge && status) {
      badge.textContent = task.labels[status] || status;
      badge.className = 'badge ' + (task.classes[status] || 'muted');
      badge.dataset.status = status;
    }
    if (fill && typeof progress === 'number') fill.style.width = Math.max(0, Math.min(100, progress)) + '%';
    if (progressText && typeof progress === 'number') progressText.textContent = progress + '%';
    if (errorBox && error) {
      errorBox.textContent = error;
      errorBox.hidden = false;
    }
  }

  async function loadLog() {
    if (!logEl) return;
    try {
      const response = await fetch('/api/tasks/' + task.id + '/log', { credentials: 'same-origin' });
      if (!response.ok) return;
      const text = await response.text();
      const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 48;
      logEl.textContent = text;
      if (atBottom) logEl.scrollTop = logEl.scrollHeight;
    } catch (error) {
      /* 忽略日志读取失败 */
    }
  }

  loadLog();
  const terminal = ['done', 'failed', 'expired', 'canceled'].includes(task.status);
  if (!terminal) {
    logTimer = setInterval(loadLog, 3500);
    const source = new EventSource('/api/tasks/' + task.id + '/stream');
    source.onmessage = function (event) {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (data.deleted) {
        source.close();
        clearInterval(logTimer);
        FF.toast('任务已被删除', 'info');
        setTimeout(function () { location.href = '/tasks'; }, 800);
        return;
      }
      applyState(data.status, data.progress, data.error_msg);
      if (['done', 'failed', 'expired', 'canceled'].includes(data.status)) {
        source.close();
        clearInterval(logTimer);
        setTimeout(function () { location.reload(); }, 700);
      }
    };
  }

  async function loadArchivePreview() {
    const rows = document.getElementById('archive-rows');
    if (!rows) return;
    const summary = document.getElementById('archive-summary');
    try {
      const data = await FF.api('/api/tasks/' + task.id + '/preview');
      if (summary) {
        summary.textContent = data.entry_count + ' 个文件 · 解压后 ' + FF.formatSize(data.total_size) +
          (data.truncated ? '（仅显示前 800 项）' : '');
      }
      rows.innerHTML = (data.files || []).map(function (item) {
        return '<tr><td class="mono">' + FF.escapeHtml(item.path) + '</td><td>' +
          FF.formatSize(item.size) + '</td></tr>';
      }).join('') || '<tr><td colspan="2" class="muted">压缩包为空</td></tr>';
    } catch (error) {
      if (summary) summary.textContent = '清单加载失败';
    }
  }

  loadArchivePreview();

  const logRefresh = document.getElementById('log-refresh');
  if (logRefresh) logRefresh.addEventListener('click', loadLog);

  const cancelBtn = document.getElementById('cancel-btn');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', async function () {
      if (!window.confirm('确定取消该任务？')) return;
      try {
        await FF.api('/api/tasks/' + task.id + '/cancel', { method: 'POST' });
        FF.toast('任务已取消', 'ok');
        setTimeout(function () { location.reload(); }, 600);
      } catch (error) {
        FF.toast(error.message, 'err');
      }
    });
  }

  const retryBtn = document.getElementById('retry-btn');
  if (retryBtn) {
    retryBtn.addEventListener('click', async function () {
      try {
        await FF.api('/api/tasks/' + task.id + '/retry', { method: 'POST' });
        FF.toast('任务已重新排队', 'ok');
        setTimeout(function () { location.reload(); }, 600);
      } catch (error) {
        FF.toast(error.message, 'err');
      }
    });
  }

  const deleteBtn = document.getElementById('delete-btn');
  if (deleteBtn) {
    deleteBtn.addEventListener('click', async function () {
      if (!window.confirm('确定删除该任务及其文件？此操作不可恢复。')) return;
      try {
        await FF.api('/api/tasks/' + task.id, { method: 'DELETE' });
        FF.toast('任务已删除', 'ok');
        setTimeout(function () { location.href = '/tasks'; }, 500);
      } catch (error) {
        FF.toast(error.message, 'err');
      }
    });
  }

  const inlineConvert = document.getElementById('inline-convert');
  if (inlineConvert) {
    inlineConvert.addEventListener('click', async function () {
      const select = document.getElementById('inline-target');
      if (!select || !select.value) return;
      try {
        await FF.api('/api/tasks/' + task.id + '/convert', {
          method: 'POST',
          body: JSON.stringify({ target_format: select.value, options: {} })
        });
        FF.toast('已提交转换', 'ok');
        setTimeout(function () { location.reload(); }, 500);
      } catch (error) {
        FF.toast(error.message, 'err');
      }
    });
  }

  const modal = document.getElementById('share-modal');
  const shareBtn = document.getElementById('share-btn');
  if (modal && shareBtn) {
    shareBtn.addEventListener('click', function () { modal.hidden = false; });
    modal.querySelectorAll('[data-close]').forEach(function (element) {
      element.addEventListener('click', function () { modal.hidden = true; });
    });
    const createBtn = document.getElementById('share-create');
    createBtn.addEventListener('click', async function () {
      const hours = Number(document.getElementById('share-hours').value || 0);
      const maxDownloads = Number(document.getElementById('share-max').value || 0);
      try {
        const data = await FF.api('/api/tasks/' + task.id + '/share', {
          method: 'POST',
          body: JSON.stringify({ expire_hours: hours, max_downloads: maxDownloads })
        });
        const url = location.origin + data.url;
        document.getElementById('share-url').value = url;
        document.getElementById('share-result').hidden = false;
        FF.toast('分享链接已生成', 'ok');
      } catch (error) {
        FF.toast(error.message, 'err');
      }
    });
    const copyBtn = document.getElementById('share-copy');
    copyBtn.addEventListener('click', function () {
      const value = document.getElementById('share-url').value;
      if (value) FF.copyText(value);
    });
  }
})();
