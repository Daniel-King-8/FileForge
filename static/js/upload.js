/* 上传 + 转换页交互 */
(function () {
  const page = window.FF_PAGE || {};
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('file-input');
  const fileList = document.getElementById('file-list');
  const targetSelect = document.getElementById('target-select');
  const targetHint = document.getElementById('target-hint');
  const submitBtn = document.getElementById('submit-btn');
  const submitHint = document.getElementById('submit-hint');

  const entries = [];
  let nextId = 1;
  let busy = false;

  const STATUS_TEXT = {
    ready: '待上传',
    uploading: '上传中',
    converting: '提交转换',
    queued: '已入队',
    error: '失败'
  };

  function extOf(name) {
    const lower = String(name || '').toLowerCase();
    if (lower.endsWith('.tar.gz')) return 'tar.gz';
    const index = lower.lastIndexOf('.');
    return index >= 0 ? lower.slice(index + 1) : '';
  }

  function addFiles(files) {
    Array.from(files || []).forEach(function (file) {
      const ext = extOf(file.name);
      if (!(page.exts || []).includes(ext)) {
        FF.toast(file.name + '：不支持 .' + (ext || '?') + ' 格式', 'err');
        return;
      }
      if (file.size > page.maxUpload) {
        FF.toast(file.name + '：超过单文件上限 ' + FF.formatSize(page.maxUpload), 'err');
        return;
      }
      entries.push({
        id: nextId++,
        file: file,
        ext: ext,
        status: 'ready',
        progress: 0,
        taskId: null,
        error: null
      });
    });
    render();
    refreshTargets();
  }

  function render() {
    if (!entries.length) {
      fileList.innerHTML = '';
      submitBtn.disabled = true;
      submitHint.textContent = '';
      return;
    }
    fileList.innerHTML = entries.map(function (entry) {
      const percent = entry.status === 'uploading' ? entry.progress : (entry.status === 'queued' ? 100 : 0);
      const barClass = entry.status === 'error' ? 'err' : (entry.status === 'queued' ? 'ok' : 'info');
      const statusText = entry.status === 'error'
        ? FF.escapeHtml(entry.error || '失败')
        : (entry.status === 'uploading'
            ? '上传中 ' + entry.progress + '%'
            : (STATUS_TEXT[entry.status] || entry.status));
      return '' +
        '<div class="file-row" data-id="' + entry.id + '">' +
          '<div class="file-row-main">' +
            '<span class="file-name" title="' + FF.escapeHtml(entry.file.name) + '">' + FF.escapeHtml(entry.file.name) + '</span>' +
            '<span class="file-size">' + FF.formatSize(entry.file.size) + ' · ' + FF.escapeHtml(entry.ext) + '</span>' +
          '</div>' +
          '<div class="file-row-state ' + (entry.status === 'error' ? 'err' : '') + '">' + statusText + '</div>' +
          '<div class="file-progress"><span class="' + barClass + '" style="width:' + (percent || 0) + '%"></span></div>' +
          (entry.taskId ? '<a class="btn btn-ghost btn-xs" href="/tasks/' + entry.taskId + '">查看</a>' : '') +
          (entry.status === 'uploading' || busy ? '' : '<button class="btn btn-ghost btn-xs" type="button" data-remove="' + entry.id + '">移除</button>') +
        '</div>';
    }).join('');
    updateSubmitState();
  }

  function updateRow(entry) {
    const row = fileList.querySelector('[data-id="' + entry.id + '"]');
    if (!row) {
      render();
      return;
    }
    const state = row.querySelector('.file-row-state');
    const bar = row.querySelector('.file-progress span');
    if (state) {
      state.textContent = entry.status === 'uploading' ? '上传中 ' + entry.progress + '%' : (STATUS_TEXT[entry.status] || '');
      state.classList.toggle('err', entry.status === 'error');
    }
    if (bar) bar.style.width = (entry.status === 'uploading' ? entry.progress : (entry.status === 'queued' ? 100 : 0)) + '%';
  }

  function commonTargets() {
    const valid = entries.filter(function (entry) { return !entry.error; });
    if (!valid.length) return [];
    let result = (page.targetsByExt[valid[0].ext] || []).slice();
    for (let index = 1; index < valid.length; index += 1) {
      const other = page.targetsByExt[valid[index].ext] || [];
      result = result.filter(function (target) { return other.includes(target); });
    }
    return result;
  }

  function refreshTargets() {
    const targets = commonTargets();
    const previous = targetSelect.value;
    targetSelect.innerHTML = '';
    if (!entries.length) {
      targetSelect.innerHTML = '<option value="">请先选择文件</option>';
      targetSelect.disabled = true;
      targetHint.textContent = '';
      updateSubmitState();
      return;
    }
    if (!targets.length) {
      targetSelect.innerHTML = '<option value="">无可选格式</option>';
      targetSelect.disabled = true;
      targetHint.textContent = entries.length ? '所选文件没有共同的目标格式，请分批上传' : '';
    } else {
      targets.forEach(function (target) {
        const option = document.createElement('option');
        option.value = target;
        option.textContent = target;
        targetSelect.appendChild(option);
      });
      if (targets.includes(previous)) targetSelect.value = previous;
      targetSelect.disabled = false;
      targetHint.textContent = '所选文件共同支持 ' + targets.length + ' 种目标格式';
    }
    updateSubmitState();
  }

  function updateSubmitState() {
    const hasReady = entries.some(function (entry) {
      return entry.status === 'ready' || entry.status === 'error';
    });
    submitBtn.disabled = busy || !hasReady || !targetSelect.value;
    if (busy) {
      submitBtn.textContent = '处理中…';
    } else {
      submitBtn.textContent = '开始转换';
    }
  }

  function collectOptions() {
    const options = {};
    if (page.category === 'image') {
      const quality = document.getElementById('opt-quality');
      const width = document.getElementById('opt-width');
      const height = document.getElementById('opt-height');
      if (quality) options.quality = Number(quality.value);
      if (width && width.value) options.width = Number(width.value);
      if (height && height.value) options.height = Number(height.value);
    } else if (page.category === 'media') {
      ['start', 'end', 'resolution'].forEach(function (key) {
        const input = document.getElementById('opt-' + key);
        if (input && input.value.trim()) options[key] = input.value.trim();
      });
    } else if (page.category === 'archive') {
      const level = document.getElementById('opt-level');
      if (level) options.level = Number(level.value);
    }
    return options;
  }

  function uploadOne(entry, target, options) {
    return new Promise(function (resolve) {
      entry.status = 'uploading';
      entry.progress = 0;
      entry.error = null;
      render();

      const form = new FormData();
      form.append('file', entry.file);
      form.append('category', page.category);

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/upload');
      xhr.upload.onprogress = function (event) {
        if (event.lengthComputable) {
          entry.progress = Math.min(99, Math.round((event.loaded / event.total) * 100));
          updateRow(entry);
        }
      };
      xhr.onload = async function () {
        let data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (error) {
          data = null;
        }
        if (xhr.status === 401) {
          location.href = '/login?next=' + encodeURIComponent(location.pathname);
          resolve();
          return;
        }
        if (xhr.status >= 400 || !data) {
          entry.status = 'error';
          entry.error = (data && data.error) || ('上传失败 (' + xhr.status + ')');
          render();
          resolve();
          return;
        }
        entry.taskId = data.task.id;
        entry.progress = 100;
        entry.status = 'converting';
        render();
        try {
          await FF.api('/api/tasks/' + entry.taskId + '/convert', {
            method: 'POST',
            body: JSON.stringify({ target_format: target, options: options })
          });
          entry.status = 'queued';
        } catch (error) {
          entry.status = 'error';
          entry.error = error.message;
        }
        render();
        resolve();
      };
      xhr.onerror = function () {
        entry.status = 'error';
        entry.error = '网络错误，上传失败';
        render();
        resolve();
      };
      xhr.send(form);
    });
  }

  async function submit() {
    if (busy) return;
    const target = targetSelect.value;
    if (!target) {
      FF.toast('请选择目标格式', 'err');
      return;
    }
    const queue = entries.filter(function (entry) {
      return entry.status === 'ready' || entry.status === 'error';
    });
    if (!queue.length) {
      FF.toast('没有待处理的新文件', 'info');
      return;
    }

    busy = true;
    render();
    const options = collectOptions();
    let success = 0;
    for (const entry of queue) {
      await uploadOne(entry, target, options);
      if (entry.status === 'queued') success += 1;
    }
    busy = false;
    render();
    if (success > 0) {
      FF.toast('已提交 ' + success + ' 个转换任务', 'ok');
      submitHint.innerHTML = '任务已进入队列 · <a class="link" href="/tasks">前往任务中心查看进度 →</a>';
    } else {
      FF.toast('提交失败，请查看文件列表中的错误', 'err');
    }
  }

  dropzone.addEventListener('click', function () {
    fileInput.click();
  });
  dropzone.addEventListener('dragover', function (event) {
    event.preventDefault();
    dropzone.classList.add('dragover');
  });
  ['dragleave', 'dragend'].forEach(function (name) {
    dropzone.addEventListener(name, function () {
      dropzone.classList.remove('dragover');
    });
  });
  dropzone.addEventListener('drop', function (event) {
    event.preventDefault();
    dropzone.classList.remove('dragover');
    addFiles(event.dataTransfer.files);
  });
  fileInput.addEventListener('change', function () {
    addFiles(fileInput.files);
    fileInput.value = '';
  });
  window.addEventListener('dragover', function (event) { event.preventDefault(); });
  window.addEventListener('drop', function (event) { event.preventDefault(); });

  fileList.addEventListener('click', function (event) {
    const button = event.target.closest('[data-remove]');
    if (!button || busy) return;
    const id = Number(button.getAttribute('data-remove'));
    const index = entries.findIndex(function (entry) { return entry.id === id; });
    if (index >= 0) {
      entries.splice(index, 1);
      render();
      refreshTargets();
    }
  });

  targetSelect.addEventListener('change', updateSubmitState);
  submitBtn.addEventListener('click', submit);

  const quality = document.getElementById('opt-quality');
  const qualityValue = document.getElementById('quality-val');
  if (quality && qualityValue) {
    quality.addEventListener('input', function () {
      qualityValue.textContent = quality.value;
    });
  }

  refreshTargets();
})();
