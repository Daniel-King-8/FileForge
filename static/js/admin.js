/* 管理后台 */
(function () {
  const state = { tab: 'overview', userPage: 1, userQuery: '', taskPage: 1 };

  function fmtTime(iso) {
    if (!iso) return '-';
    const date = new Date(iso);
    if (isNaN(date.getTime())) return iso;
    return date.toLocaleString('zh-CN', { hour12: false });
  }

  function statusBadge(status) {
    const classes = {
      pending: 'warn', running: 'info', done: 'ok',
      failed: 'err', expired: 'muted', canceled: 'muted'
    };
    const labels = {
      pending: '等待转换', running: '转换中', done: '已完成',
      failed: '失败', expired: '已过期', canceled: '已取消'
    };
    return '<span class="badge ' + (classes[status] || 'muted') + '">' + (labels[status] || status) + '</span>';
  }

  function rowCount(tbody, columns, message) {
    if (tbody) tbody.innerHTML = '<tr><td colspan="' + columns + '" class="muted">' + message + '</td></tr>';
  }

  const panels = {
    overview: document.getElementById('tab-overview'),
    users: document.getElementById('tab-users'),
    alerts: document.getElementById('tab-alerts'),
    tasks: document.getElementById('tab-tasks'),
    audit: document.getElementById('tab-audit')
  };
  const tabs = Array.from(document.querySelectorAll('#admin-tabs .tab'));

  function activate(name) {
    state.tab = name;
    tabs.forEach(function (tab) {
      tab.classList.toggle('active', tab.getAttribute('data-tab') === name);
    });
    Object.keys(panels).forEach(function (key) {
      panels[key].hidden = key !== name;
    });
    load(name);
  }

  function load(name) {
    if (name === 'overview') return loadStats();
    if (name === 'users') return loadUsers();
    if (name === 'alerts') return loadAlerts();
    if (name === 'tasks') return loadAdminTasks();
    if (name === 'audit') return loadAudit();
    return Promise.resolve();
  }

  async function loadStats() {
    let data;
    try {
      data = await FF.api('/api/admin/stats');
    } catch (error) {
      FF.toast(error.message, 'err');
      return;
    }

    document.getElementById('stat-disk').textContent = data.disk_percent + '%';
    document.getElementById('stat-pending').textContent = data.queue.pending;
    document.getElementById('stat-running').textContent = data.queue.running;
    document.getElementById('stat-alerts').textContent = data.unacked_alerts;
    document.getElementById('limits-text').textContent =
      '全局并发 ' + data.limits.max_concurrent + ' · 告警 ' + data.limits.disk_warn_pct +
      '% / 拒绝 ' + data.limits.disk_stop_pct + '%';

    const alertCount = document.getElementById('alert-count');
    if (alertCount) {
      alertCount.textContent = data.unacked_alerts;
      alertCount.hidden = data.unacked_alerts === 0;
    }

    const volumeRows = document.getElementById('volume-rows');
    if (volumeRows) {
      volumeRows.innerHTML = data.volumes.map(function (volume) {
        const percent = Math.min(Number(volume.percent) || 0, 100);
        const tone = percent >= data.limits.disk_stop_pct ? 'err'
          : (percent >= data.limits.disk_warn_pct ? 'warn' : 'ok');
        return '<tr>' +
          '<td><strong>' + FF.escapeHtml(volume.name) + '</strong></td>' +
          '<td class="mono muted">' + FF.escapeHtml(volume.path) + '</td>' +
          '<td>' + FF.formatSize(volume.content) + '</td>' +
          '<td><div class="usage-cell"><div class="mini-bar"><span class="' + tone + '" style="width:' + percent + '%"></span></div><span>' + volume.percent + '%</span></div></td>' +
          '</tr>';
      }).join('');
    }

    const containerRows = document.getElementById('container-rows');
    if (containerRows) {
      if (!data.containers.length) {
        rowCount(containerRows, 4, '暂无数据（worker 启动后每 20 秒采集一次）');
      } else {
        containerRows.innerHTML = data.containers.map(function (item) {
          return '<tr><td class="mono">' + FF.escapeHtml(item.name) + '</td>' +
            '<td>' + FF.escapeHtml(item.cpu_pct || '-') + '</td>' +
            '<td>' + FF.escapeHtml(item.mem_usage || '-') + '</td>' +
            '<td>' + FF.escapeHtml(item.mem_pct || '-') + '</td></tr>';
        }).join('');
      }
    }

    const workerRows = document.getElementById('worker-rows');
    if (workerRows) {
      if (!data.workers.length) {
        rowCount(workerRows, 4, '暂无 worker');
      } else {
        workerRows.innerHTML = data.workers.map(function (worker) {
          const online = worker.status === 'online';
          return '<tr><td class="mono">' + FF.escapeHtml(worker.id) + '</td>' +
            '<td><span class="badge ' + (online ? 'ok' : 'muted') + '">' + (online ? '在线' : '离线') + '</span></td>' +
            '<td>' + (worker.current_task ? '<a class="task-link" href="/tasks/' + FF.escapeHtml(worker.current_task) + '">' + FF.escapeHtml(worker.current_task.slice(0, 10)) + '</a>' : '<span class="muted">空闲</span>') + '</td>' +
            '<td class="muted nowrap">' + fmtTime(worker.last_heartbeat) + '</td></tr>';
        }).join('');
      }
    }

    const counts = document.getElementById('task-counts');
    if (counts) {
      const labels = { pending: '等待', running: '运行中', done: '完成', failed: '失败', expired: '过期', canceled: '取消' };
      counts.innerHTML = Object.keys(labels).map(function (key) {
        return '<div class="stat"><span class="stat-label">' + labels[key] + '</span>' +
          '<span class="stat-value">' + (data.tasks[key] || 0) + '</span></div>';
      }).join('');
    }
  }

  async function loadUsers() {
    let data;
    try {
      data = await FF.api('/api/admin/users?page=' + state.userPage + '&per=20&q=' + encodeURIComponent(state.userQuery));
    } catch (error) {
      FF.toast(error.message, 'err');
      return;
    }
    const tbody = document.getElementById('user-rows');
    if (!data.items.length) {
      rowCount(tbody, 7, '没有用户');
    } else {
      tbody.innerHTML = data.items.map(function (user) {
        const actions = user.role === 'admin'
          ? '<span class="muted">管理员</span>'
          : '<button class="btn btn-ghost btn-xs" type="button" data-user="' + user.id + '" data-act="' +
              (user.status === 'active' ? 'ban' : 'unban') + '">' + (user.status === 'active' ? '封禁' : '解封') + '</button> ' +
            '<button class="btn btn-ghost btn-xs" type="button" data-user="' + user.id + '" data-act="reset-password">重置密码</button>';
        return '<tr>' +
          '<td><strong>' + FF.escapeHtml(user.username) + '</strong><div class="sub mono">#' + user.id + '</div></td>' +
          '<td class="muted">' + FF.escapeHtml(user.email) + '</td>' +
          '<td>' + (user.role === 'admin' ? '管理员' : '用户') + '</td>' +
          '<td><span class="badge ' + (user.status === 'active' ? 'ok' : 'err') + '">' + (user.status === 'active' ? '正常' : '已封禁') + '</span></td>' +
          '<td>' + FF.formatSize(user.used_bytes) + ' / ' + FF.formatSize(user.quota_bytes) + '</td>' +
          '<td>' + user.task_count + '</td>' +
          '<td class="actions">' + actions + '</td>' +
          '</tr>';
      }).join('');
    }
    const pagination = document.getElementById('user-pagination');
    if (pagination) {
      pagination.innerHTML = data.pages > 1
        ? '<button class="page-btn" type="button" data-upage="' + (data.page - 1) + '" ' + (data.page <= 1 ? 'disabled' : '') + '>← 上一页</button>' +
          '<span class="page-info">第 ' + data.page + ' / ' + data.pages + ' 页</span>' +
          '<button class="page-btn" type="button" data-upage="' + (data.page + 1) + '" ' + (data.page >= data.pages ? 'disabled' : '') + '>下一页 →</button>'
        : '';
    }
  }

  async function loadAlerts() {
    const onlyUnacked = document.getElementById('only-unacked');
    const query = onlyUnacked && onlyUnacked.checked ? '?unacked=1' : '';
    let data;
    try {
      data = await FF.api('/api/admin/alerts' + query);
    } catch (error) {
      FF.toast(error.message, 'err');
      return;
    }
    const tbody = document.getElementById('alert-rows');
    if (!data.items.length) {
      rowCount(tbody, 5, '暂无告警');
      return;
    }
    const classes = { info: 'info', warning: 'warn', critical: 'err' };
    tbody.innerHTML = data.items.map(function (alert) {
      return '<tr>' +
        '<td><span class="badge ' + (classes[alert.level] || 'muted') + '">' + FF.escapeHtml(alert.level) + '</span></td>' +
        '<td class="muted">' + FF.escapeHtml(alert.source) + '</td>' +
        '<td>' + FF.escapeHtml(alert.message) + '</td>' +
        '<td class="muted nowrap">' + fmtTime(alert.created_at) + '</td>' +
        '<td class="actions">' + (alert.acked
          ? '<span class="muted">已确认</span>'
          : '<button class="btn btn-ghost btn-xs" type="button" data-ack="' + alert.id + '">确认</button>') + '</td>' +
        '</tr>';
    }).join('');
  }

  async function loadAdminTasks() {
    const status = document.getElementById('task-status').value;
    const query = (document.getElementById('task-search').value || '').trim();
    let data;
    try {
      data = await FF.api('/api/admin/tasks?page=' + state.taskPage + '&per=20&status=' +
        encodeURIComponent(status) + '&q=' + encodeURIComponent(query));
    } catch (error) {
      FF.toast(error.message, 'err');
      return;
    }
    const tbody = document.getElementById('admin-task-rows');
    if (!data.items.length) {
      rowCount(tbody, 6, '没有任务');
    } else {
      tbody.innerHTML = data.items.map(function (task) {
        return '<tr>' +
          '<td><a class="task-link" href="/tasks/' + FF.escapeHtml(task.id) + '">' + FF.escapeHtml(task.src_name) + '</a>' +
            '<div class="sub mono">#' + FF.escapeHtml(task.id.slice(0, 10)) + '</div></td>' +
          '<td>#' + task.user_id + '</td>' +
          '<td class="nowrap">' + FF.escapeHtml(task.category) + '</td>' +
          '<td>' + statusBadge(task.status) + '</td>' +
          '<td class="nowrap">' + FF.formatSize(task.src_size) + '</td>' +
          '<td class="muted nowrap">' + fmtTime(task.created_at) + '</td>' +
          '</tr>';
      }).join('');
    }
    const pagination = document.getElementById('task-pagination');
    if (pagination) {
      pagination.innerHTML = data.pages > 1
        ? '<button class="page-btn" type="button" data-tpage="' + (data.page - 1) + '" ' + (data.page <= 1 ? 'disabled' : '') + '>← 上一页</button>' +
          '<span class="page-info">第 ' + data.page + ' / ' + data.pages + ' 页</span>' +
          '<button class="page-btn" type="button" data-tpage="' + (data.page + 1) + '" ' + (data.page >= data.pages ? 'disabled' : '') + '>下一页 →</button>'
        : '';
    }
  }

  async function loadAudit() {
    let data;
    try {
      data = await FF.api('/api/admin/audit');
    } catch (error) {
      FF.toast(error.message, 'err');
      return;
    }
    const tbody = document.getElementById('audit-rows');
    if (!data.items.length) {
      rowCount(tbody, 6, '暂无审计记录');
      return;
    }
    tbody.innerHTML = data.items.map(function (item) {
      return '<tr>' +
        '<td class="muted nowrap">' + fmtTime(item.created_at) + '</td>' +
        '<td>' + (item.user_id || '-') + '</td>' +
        '<td class="mono">' + FF.escapeHtml(item.action) + '</td>' +
        '<td class="truncate" title="' + FF.escapeHtml(item.target) + '">' + FF.escapeHtml(item.target) + '</td>' +
        '<td class="mono muted">' + FF.escapeHtml(item.ip) + '</td>' +
        '<td class="muted truncate" title="' + FF.escapeHtml(item.ua) + '">' + FF.escapeHtml(item.ua) + '</td>' +
        '</tr>';
    }).join('');
  }

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      activate(tab.getAttribute('data-tab'));
    });
  });

  document.getElementById('refresh-btn').addEventListener('click', function () {
    load(state.tab);
    FF.toast('已刷新', 'info');
  });

  document.getElementById('cleanup-btn').addEventListener('click', async function () {
    if (!window.confirm('立即执行一次过期文件/任务清理？')) return;
    try {
      const data = await FF.api('/api/admin/cleanup', { method: 'POST' });
      FF.toast('清理完成：过期 ' + data.summary.expired + '，孤儿文件 ' + data.summary.orphans, 'ok');
      loadStats();
    } catch (error) {
      FF.toast(error.message, 'err');
    }
  });

  document.getElementById('user-search-btn').addEventListener('click', function () {
    state.userQuery = document.getElementById('user-search').value.trim();
    state.userPage = 1;
    loadUsers();
  });
  document.getElementById('user-search').addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      document.getElementById('user-search-btn').click();
    }
  });

  document.getElementById('task-search-btn').addEventListener('click', function () {
    state.taskPage = 1;
    loadAdminTasks();
  });
  document.getElementById('task-status').addEventListener('change', function () {
    state.taskPage = 1;
    loadAdminTasks();
  });

  document.getElementById('only-unacked').addEventListener('change', loadAlerts);
  document.getElementById('alert-test').addEventListener('click', async function () {
    try {
      await FF.api('/api/admin/alerts/test', { method: 'POST' });
      FF.toast('测试告警已写入', 'ok');
      loadAlerts();
    } catch (error) {
      FF.toast(error.message, 'err');
    }
  });

  document.addEventListener('click', async function (event) {
    const userButton = event.target.closest('[data-user]');
    if (userButton) {
      const id = userButton.getAttribute('data-user');
      const action = userButton.getAttribute('data-act');
      if (action === 'ban' && !window.confirm('确定封禁该用户？')) return;
      try {
        const data = await FF.api('/api/admin/users/' + id + '/' + action, { method: 'POST' });
        if (action === 'reset-password') {
          FF.toast('新密码：' + data.password + '（请立即转交用户）', 'ok');
        } else {
          FF.toast('操作成功', 'ok');
        }
        loadUsers();
      } catch (error) {
        FF.toast(error.message, 'err');
      }
      return;
    }

    const ack = event.target.closest('[data-ack]');
    if (ack) {
      try {
        await FF.api('/api/admin/alerts/' + ack.getAttribute('data-ack') + '/ack', { method: 'POST' });
        loadAlerts();
      } catch (error) {
        FF.toast(error.message, 'err');
      }
      return;
    }

    const userPage = event.target.closest('[data-upage]');
    if (userPage) {
      state.userPage = Number(userPage.getAttribute('data-upage'));
      loadUsers();
      return;
    }
    const taskPage = event.target.closest('[data-tpage]');
    if (taskPage) {
      state.taskPage = Number(taskPage.getAttribute('data-tpage'));
      loadAdminTasks();
    }
  });

  activate('overview');
})();
