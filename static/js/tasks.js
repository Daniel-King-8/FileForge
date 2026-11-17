/* 任务列表：批量操作与自动刷新 */
(function () {
  const bar = document.getElementById('batch-bar');
  const countEl = document.getElementById('selected-count');
  const checkAll = document.getElementById('check-all');

  function rowChecks() {
    return Array.from(document.querySelectorAll('.row-check'));
  }

  function selectedIds() {
    return rowChecks().filter(function (box) { return box.checked; }).map(function (box) { return box.value; });
  }

  function refreshBar() {
    if (!bar) return;
    const ids = selectedIds();
    bar.hidden = ids.length === 0;
    countEl.textContent = '已选 ' + ids.length + ' 项';
    if (checkAll) {
      checkAll.checked = ids.length > 0 && ids.length === rowChecks().length;
    }
  }

  if (checkAll) {
    checkAll.addEventListener('change', function () {
      rowChecks().forEach(function (box) { box.checked = checkAll.checked; });
      refreshBar();
    });
  }
  document.addEventListener('change', function (event) {
    if (event.target.classList && event.target.classList.contains('row-check')) refreshBar();
  });

  document.addEventListener('click', async function (event) {
    const deleteButton = event.target.closest('[data-delete]');
    if (deleteButton) {
      if (!window.confirm('确定删除该任务及其文件？此操作不可恢复。')) return;
      try {
        await FF.api('/api/tasks/' + deleteButton.getAttribute('data-delete'), { method: 'DELETE' });
        FF.toast('任务已删除', 'ok');
        const row = deleteButton.closest('tr');
        if (row) row.remove();
        refreshBar();
      } catch (error) {
        FF.toast(error.message, 'err');
      }
      return;
    }

    const batchButton = event.target.closest('[data-batch]');
    if (!batchButton) return;
    const ids = selectedIds();
    if (!ids.length) {
      FF.toast('请先选择任务', 'err');
      return;
    }
    const action = batchButton.getAttribute('data-batch');
    if (action === 'delete') {
      if (!window.confirm('确定删除选中的 ' + ids.length + ' 个任务？')) return;
      try {
        const result = await FF.api('/api/tasks/batch/delete', {
          method: 'POST',
          body: JSON.stringify({ ids: ids })
        });
        FF.toast('已删除 ' + result.deleted + ' 个任务', 'ok');
        location.reload();
      } catch (error) {
        FF.toast(error.message, 'err');
      }
    } else if (action === 'download') {
      try {
        const result = await FF.api('/api/tasks/batch/download', {
          method: 'POST',
          body: JSON.stringify({ ids: ids })
        });
        location.href = result.url;
      } catch (error) {
        FF.toast(error.message, 'err');
      }
    }
  });

  const running = document.querySelectorAll('tr[data-status="running"]').length;
  if (running > 0) {
    setTimeout(function () {
      if (selectedIds().length === 0 && !document.hidden) location.reload();
    }, 12000);
  }
})();
