(() => {
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    batchSelect: $('#batchSelect'), typeSelect: $('#typeSelect'),
    importButton: $('#importButton'), openBrowserButton: $('#openBrowserButton'),
    captureButton: $('#captureButton'), closeBrowserButton: $('#closeBrowserButton'),
    browserHint: $('#browserHint'), toastRegion: $('#toastRegion'),
  };
  const charts = {};
  let lastDashboard = null;
  let pendingTask = false;
  let browserIsOpen = false;

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);

  function toast(message, isError = false) {
    const node = document.createElement('div');
    node.className = `toast${isError ? ' error' : ''}`;
    node.textContent = message;
    elements.toastRegion.append(node);
    window.setTimeout(() => node.remove(), 4200);
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
    return payload;
  }

  function initCharts() {
    ['typeChart', 'noteChart', 'fanChart', 'categoryChart', 'wordChart'].forEach((id) => {
      const element = document.getElementById(id);
      if (element && window.echarts) charts[id] = echarts.init(element);
    });
    window.addEventListener('resize', () => Object.values(charts).forEach((chart) => chart.resize()));
  }

  const baseChart = {
    animationDuration: 500,
    textStyle: { fontFamily: 'Microsoft YaHei, Noto Sans SC, sans-serif', color: '#7e8981' },
    tooltip: { trigger: 'item', confine: true },
  };

  function setEmptyChart(chart, message) {
    if (!chart) return;
    chart.clear();
    chart.setOption({
      ...baseChart,
      graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: message, fill: '#a5ada7', fontSize: 11 } }],
    });
  }

  function renderCharts(data) {
    if (!window.echarts) return;
    const types = data.type_summary || [];
    if (types.length) {
      charts.typeChart?.setOption({
        ...baseChart,
        color: ['#197b5b', '#59a984', '#9dc8ae', '#e7b979', '#8ba8cf'],
        tooltip: { trigger: 'item', formatter: '{b}<br>{c} 条 ({d}%)' },
        legend: { bottom: 0, left: 'center', itemWidth: 7, itemHeight: 7, textStyle: { color: '#88938c', fontSize: 8 } },
        series: [{ type: 'pie', radius: ['43%', '68%'], center: ['50%', '43%'], avoidLabelOverlap: true, itemStyle: { borderColor: '#fff', borderWidth: 3 }, label: { show: false }, data: types.map((item) => ({ name: item.label, value: item.count })) }],
      }, true);
    } else setEmptyChart(charts.typeChart, '当前批次暂无记录');

    const noteMetrics = data.chart_data?.note_metrics || [];
    if (noteMetrics.length) {
      charts.noteChart?.setOption({ ...baseChart, grid: { left: 45, right: 15, top: 12, bottom: 27 }, xAxis: { type: 'category', data: noteMetrics.map((item) => item.name), axisLine: { lineStyle: { color: '#e8ece8' } }, axisTick: { show: false }, axisLabel: { color: '#929c95', fontSize: 8 } }, yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f0f2f0' } }, axisLabel: { color: '#a0a9a2', fontSize: 8 } }, series: [{ type: 'bar', data: noteMetrics.map((item) => item.value), barWidth: 21, itemStyle: { color: '#4a9b75', borderRadius: [4, 4, 0, 0] } }] }, true);
    } else setEmptyChart(charts.noteChart, '当前批次没有笔记指标');

    const fans = data.chart_data?.fan_analysis || [];
    if (fans.length) {
      charts.fanChart?.setOption({ ...baseChart, grid: { left: 90, right: 24, top: 8, bottom: 13 }, xAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%', fontSize: 8, color: '#a0a9a2' }, splitLine: { lineStyle: { color: '#f0f2f0' } } }, yAxis: { type: 'category', data: fans.map((item) => item.name), axisLabel: { fontSize: 8, color: '#858f88' }, axisTick: { show: false }, axisLine: { show: false } }, series: [{ type: 'bar', data: fans.map((item) => item.value), barWidth: 10, itemStyle: { color: '#8bb79b', borderRadius: 5 }, label: { show: true, position: 'right', formatter: '{c}%', fontSize: 8, color: '#728078' } }] }, true);
    } else setEmptyChart(charts.fanChart, '当前批次没有粉丝比例字段');

    const categories = data.chart_data?.categories || [];
    if (categories.length) {
      charts.categoryChart?.setOption({ ...baseChart, color: ['#438f6c', '#78ad86', '#aec9aa', '#e8c58f', '#b6c6d9'], tooltip: { trigger: 'item' }, series: [{ type: 'pie', radius: ['38%', '66%'], center: ['50%', '48%'], label: { fontSize: 8, color: '#77847b' }, labelLine: { length: 7, length2: 5 }, data: categories }] }, true);
    } else setEmptyChart(charts.categoryChart, '暂无可统计的内容类目');

    const words = data.chart_data?.word_frequency || [];
    $('#wordStatus').textContent = data.availability?.word_frequency || '等待可用标题或正文';
    if (words.length) {
      charts.wordChart?.setOption({ ...baseChart, grid: { left: 50, right: 15, top: 10, bottom: 24 }, xAxis: { type: 'category', data: words.map((item) => item.word), axisLabel: { interval: 0, rotate: 25, fontSize: 8, color: '#88938c' }, axisLine: { lineStyle: { color: '#e8ece8' } }, axisTick: { show: false } }, yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f0f2f0' } }, axisLabel: { fontSize: 8, color: '#a0a9a2' } }, series: [{ type: 'bar', data: words.map((item) => item.count), barWidth: 13, itemStyle: { color: '#bf9465', borderRadius: [3, 3, 0, 0] } }] }, true);
    } else setEmptyChart(charts.wordChart, '暂无可用标题/正文');
  }

  function renderCoverage(data) {
    const rows = data.field_coverage || [];
    $('#tableCount').textContent = `${data.counts?.records || 0} 条记录`;
    if (!rows.length) {
      $('#coverageBody').innerHTML = '<tr><td colspan="5" class="empty-cell">导入基线样本后显示字段覆盖</td></tr>';
      return;
    }
    const recordByType = new Map((data.records || []).map((record) => [record.type, record]));
    $('#coverageBody').innerHTML = rows.map((row) => {
      const record = recordByType.get(row.type);
      return `<tr><td>${escapeHtml(row.label)}</td><td>${row.field_count}</td><td>${row.present_count} / ${row.field_count}</td><td><span class="coverage-pill">${row.coverage_pct}%</span></td><td>${escapeHtml(record?.captured_at || '—')}</td></tr>`;
    }).join('');
  }

  function renderRecordDetails(records) {
    const container = $('#recordDetails');
    if (!records?.length) {
      container.innerHTML = '<div class="empty-cell">当前批次暂无样本字段</div>';
      return;
    }
    container.innerHTML = records.map((record) => {
      const fields = Object.entries(record.fields || {});
      const fieldHtml = fields.map(([key, value]) => {
        const missing = value === null || value === '' || value === '-' || value === '—';
        const display = value === null || value === '' ? '未提供' : typeof value === 'object' ? JSON.stringify(value) : value;
        return `<dt>${escapeHtml(key)}</dt><dd class="${missing ? 'missing' : ''}">${escapeHtml(display)}</dd>`;
      }).join('');
      return `<article class="record-item"><h3>${escapeHtml(record.label)} <span>· ${fields.length} 个字段</span></h3><dl>${fieldHtml}</dl></article>`;
    }).join('');
  }

  function renderTasks(tasks) {
    const container = $('#taskList');
    if (!tasks?.length) {
      container.innerHTML = '<div class="empty-cell">暂无任务</div>';
      return;
    }
    const labels = { baseline_import: '导入匿名基线', browser_open: '打开蒲公英浏览器', pgy_visible_capture: '采集当前可见页面', browser_close: '关闭蒲公英浏览器' };
    container.innerHTML = tasks.slice(0, 8).map((task) => {
      const status = task.status || 'queued';
      const statusLabel = { queued: '排队中', running: `处理中 ${task.progress || 0}%`, completed: '已完成', failed: '失败' }[status] || status;
      return `<div class="task-row"><div class="task-name">${escapeHtml(labels[task.task_type] || task.task_type)}</div><div class="task-message">${escapeHtml(task.message || '')} · ${escapeHtml(task.created_at || '')}</div><span class="task-state ${escapeHtml(status)}">${escapeHtml(statusLabel)}</span></div>`;
    }).join('');
  }

  function updateSelectOptions(data) {
    const currentBatch = elements.batchSelect.value;
    const batches = data.batches || [];
    elements.batchSelect.innerHTML = batches.length
      ? batches.map((batch) => `<option value="${escapeHtml(batch.batch_id)}">${escapeHtml(batch.source)} · ${escapeHtml(batch.captured_at)}</option>`).join('')
      : '<option value="">暂无批次</option>';
    if (batches.some((batch) => batch.batch_id === currentBatch)) elements.batchSelect.value = currentBatch;
    else if (data.selected_batch_id) elements.batchSelect.value = data.selected_batch_id;

    const currentType = elements.typeSelect.value;
    const types = data.record_types || [{ value: '', label: '全部数据' }];
    elements.typeSelect.innerHTML = types.map((type) => `<option value="${escapeHtml(type.value)}">${escapeHtml(type.label)}</option>`).join('');
    if (types.some((type) => type.value === currentType)) elements.typeSelect.value = currentType;
  }

  function renderDashboard(data) {
    lastDashboard = data;
    updateSelectOptions(data);
    $('#recordCount').textContent = data.counts?.records ?? '0';
    $('#batchCount').textContent = `历史批次 ${data.counts?.batches ?? 0}`;
    $('#coverageRate').textContent = `${data.counts?.quality_pct ?? 0}%`;
    $('#fieldCount').textContent = `${data.counts?.present_fields ?? 0} / ${data.counts?.fields ?? 0} 字段有值 · ${data.counts?.parsed_numeric_fields ?? 0} 个数值转换`;
    $('#followersValue').textContent = data.kpis?.followers_display || '暂无数据';
    $('#readingValue').textContent = data.kpis?.reading_median_display || '暂无数据';
    renderCharts(data);
    renderCoverage(data);
    renderRecordDetails(data.records || []);
    renderTasks(data.tasks || []);
  }

  async function refreshDashboard() {
    const params = new URLSearchParams();
    if (elements.batchSelect.value) params.set('batch_id', elements.batchSelect.value);
    if (elements.typeSelect.value) params.set('record_type', elements.typeSelect.value);
    const data = await requestJson(`/api/dashboard?${params.toString()}`);
    renderDashboard(data);
  }

  async function refreshTasks() {
    const tasks = await requestJson('/api/tasks');
    renderTasks(tasks);
    pendingTask = tasks.some((task) => ['queued', 'running'].includes(task.status));
    elements.importButton.disabled = pendingTask;
    elements.openBrowserButton.disabled = pendingTask;
    elements.captureButton.disabled = pendingTask || !browserIsOpen;
    elements.closeBrowserButton.disabled = pendingTask || !browserIsOpen;
    if (lastDashboard) lastDashboard.tasks = tasks;
  }

  async function refreshBrowserStatus() {
    const status = await requestJson('/api/browser');
    browserIsOpen = Boolean(status.open && status.domain === 'pgy.xiaohongshu.com');
    elements.captureButton.disabled = pendingTask || !browserIsOpen;
    elements.closeBrowserButton.disabled = pendingTask || !status.open;
    if (status.open && status.domain === 'pgy.xiaohongshu.com') {
      elements.openBrowserButton.disabled = pendingTask;
      elements.browserHint.innerHTML = `<span class="status-dot"></span><span>独立浏览器已打开 · ${escapeHtml(status.page_kind || '蒲公英页面')}。确认页面状态后可读取当前页有限字段。</span>`;
    } else if (status.open) {
      elements.openBrowserButton.disabled = pendingTask;
      elements.browserHint.innerHTML = '<span class="status-dot muted"></span><span>浏览器当前不在蒲公英域名；采集按钮保持关闭，请切回蒲公英页面。</span>';
    } else {
      elements.openBrowserButton.disabled = pendingTask;
      elements.browserHint.innerHTML = '<span class="status-dot muted"></span><span>新建隔离浏览器配置；请在窗口内正常登录并手动打开要验证的页面。应用只读取蒲公英域名下当前可见页面的少量字段。</span>';
    }
  }

  async function submitTask(endpoint, successMessage) {
    if (pendingTask) return;
    try {
      await requestJson(endpoint, { method: 'POST' });
      toast(successMessage);
      await refreshTasks();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function refreshAll() {
    try {
      await Promise.all([refreshDashboard(), refreshTasks(), refreshBrowserStatus()]);
    } catch (error) {
      toast(`本地服务暂不可用：${error.message}`, true);
    }
  }

  function bindEvents() {
    elements.importButton.addEventListener('click', () => submitTask('/api/tasks/import-baseline', '已提交匿名基线导入任务'));
    elements.openBrowserButton.addEventListener('click', () => submitTask('/api/browser/open', '正在打开独立可见浏览器'));
    elements.captureButton.addEventListener('click', () => submitTask('/api/browser/capture', '已提交当前可见页面采集任务'));
    elements.closeBrowserButton.addEventListener('click', () => submitTask('/api/browser/close', '正在关闭蒲公英浏览器'));
    elements.batchSelect.addEventListener('change', refreshDashboard);
    elements.typeSelect.addEventListener('change', refreshDashboard);
    const today = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }).format(new Date());
    $('#todayLabel').textContent = today;
  }

  document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    bindEvents();
    refreshAll();
    window.setInterval(async () => {
      try { await Promise.all([refreshTasks(), refreshBrowserStatus()]); }
      catch (_) { /* Keep the dashboard usable during a local restart. */ }
    }, 2000);
  });
})();
