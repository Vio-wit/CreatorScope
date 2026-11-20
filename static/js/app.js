(() => {
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    batchSelect: $('#batchSelect'), typeSelect: $('#typeSelect'),
    importButton: $('#importButton'), openBrowserButton: $('#openBrowserButton'),
    captureButton: $('#captureButton'), closeBrowserButton: $('#closeBrowserButton'),
    uploadButton: $('#uploadButton'), uploadInput: $('#uploadInput'),
    creatorSelect: $('#creatorSelect'), browserHint: $('#browserHint'), toastRegion: $('#toastRegion'),
  };
  const charts = {};
  let lastDashboard = null;
  let pendingTask = false;
  let browserIsOpen = false;
  let activeTaskId = null;

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
    const headers = options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
    const response = await fetch(url, { headers, ...options });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
    return payload;
  }

  function initCharts() {
    [
      'typeChart', 'noteChart', 'fanChart', 'categoryChart', 'wordChart',
      'followerDistChart', 'quoteDistChart', 'followerPriceChart', 'readingEngagementChart',
      'fanAggregateChart', 'correlationChart', 'trendChart', 'noteMetricsChart',
    ].forEach((id) => {
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

  function setBarChart(id, items, horizontal = false, valueSuffix = '') {
    const chart = charts[id];
    if (!chart) return;
    const valid = (items || []).filter((item) => item.value !== null && item.value !== undefined);
    if (!valid.length) return setEmptyChart(chart, '当前样本没有可用字段');
    chart.setOption({
      ...baseChart,
      grid: { left: horizontal ? 90 : 38, right: 24, top: 12, bottom: horizontal ? 18 : 37 },
      xAxis: horizontal
        ? { type: 'value', axisLabel: { fontSize: 8, color: '#9aa39d' }, splitLine: { lineStyle: { color: '#f0f2f0' } } }
        : { type: 'category', data: valid.map((item) => item.name), axisLabel: { interval: 0, rotate: 20, fontSize: 8, color: '#89948d' }, axisLine: { lineStyle: { color: '#e8ece8' } }, axisTick: { show: false } },
      yAxis: horizontal
        ? { type: 'category', data: valid.map((item) => item.name), axisLabel: { fontSize: 8, color: '#89948d' }, axisTick: { show: false }, axisLine: { show: false } }
        : { type: 'value', axisLabel: { fontSize: 8, color: '#9aa39d', formatter: `{value}${valueSuffix}` }, splitLine: { lineStyle: { color: '#f0f2f0' } } },
      series: [{ type: 'bar', data: valid.map((item) => item.value), barMaxWidth: 22, itemStyle: { color: '#4a9b75', borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] } }],
    }, true);
  }

  function setScatterChart(id, points, xName, yName) {
    const chart = charts[id];
    if (!chart) return;
    if (!points?.length) return setEmptyChart(chart, '没有具备共同字段的创作者');
    chart.setOption({
      ...baseChart,
      grid: { left: 45, right: 20, top: 15, bottom: 35 },
      tooltip: { trigger: 'item', formatter: (item) => `${escapeHtml(item.data[2])}<br>${escapeHtml(xName)}：${Number(item.data[0]).toLocaleString()}<br>${escapeHtml(yName)}：${Number(item.data[1]).toLocaleString()}` },
      xAxis: { type: 'value', name: xName, nameTextStyle: { fontSize: 8, color: '#929c95' }, axisLabel: { fontSize: 8, color: '#929c95' }, splitLine: { lineStyle: { color: '#f0f2f0' } } },
      yAxis: { type: 'value', name: yName, nameTextStyle: { fontSize: 8, color: '#929c95' }, axisLabel: { fontSize: 8, color: '#929c95' }, splitLine: { lineStyle: { color: '#f0f2f0' } } },
      series: [{ type: 'scatter', symbolSize: 9, itemStyle: { color: '#438f6c', opacity: .78 }, data: points.map((point) => [point.x, point.y, point.name]) }],
    }, true);
  }

  function renderAnalytics(data) {
    const chartsData = data.charts || {};
    $('#analysisScope').textContent = `${data.scope || ''} · ${data.availability?.history || ''}`;
    $('#analysisCount').textContent = `${data.counts?.creators || 0} 位匿名博主 · ${data.counts?.notes || 0} 条笔记`;
    setBarChart('followerDistChart', chartsData.follower_distribution);
    setBarChart('quoteDistChart', chartsData.quote_distribution, true);
    setScatterChart('followerPriceChart', chartsData.follower_price_scatter, '粉丝数', '报价（元）');
    setScatterChart('readingEngagementChart', chartsData.reading_engagement_scatter, '阅读中位数', '互动中位数');
    setBarChart('fanAggregateChart', (chartsData.fan_analysis || []).map((item) => ({ name: `${item.name} (${item.count})`, value: item.value })), true, '%');
    const categories = chartsData.category_distribution || [];
    if (categories.length) {
      charts.categoryChart?.setOption({ ...baseChart, color: ['#438f6c', '#78ad86', '#aec9aa', '#e8c58f', '#b6c6d9'], tooltip: { trigger: 'item', formatter: '{b}<br>{c} 位博主 ({d}%)' }, series: [{ type: 'pie', radius: ['38%', '66%'], center: ['50%', '48%'], label: { fontSize: 8, color: '#77847b' }, labelLine: { length: 7, length2: 5 }, data: categories }] }, true);
    } else setEmptyChart(charts.categoryChart, '当前样本没有内容领域');
    const words = chartsData.word_frequency || [];
    if (words.length) {
      $('#wordStatus').textContent = data.availability?.word_frequency || '已有分词';
      charts.wordChart?.setOption({ ...baseChart, grid: { left: 50, right: 15, top: 10, bottom: 24 }, xAxis: { type: 'category', data: words.map((item) => item.word), axisLabel: { interval: 0, rotate: 25, fontSize: 8, color: '#88938c' }, axisLine: { lineStyle: { color: '#e8ece8' } }, axisTick: { show: false } }, yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f0f2f0' } }, axisLabel: { fontSize: 8, color: '#a0a9a2' } }, series: [{ type: 'bar', data: words.map((item) => item.count), barWidth: 13, itemStyle: { color: '#bf9465', borderRadius: [3, 3, 0, 0] } }] }, true);
      const cloud = $('#wordCloudImage');
      cloud.hidden = false;
      cloud.src = `/api/wordcloud.png?${elements.batchSelect.value ? `batch_id=${encodeURIComponent(elements.batchSelect.value)}&` : ''}v=${Date.now()}`;
      cloud.onerror = () => { cloud.hidden = true; };
    } else {
      $('#wordCloudImage').hidden = true;
      $('#wordCloudImage').removeAttribute('src');
      $('#wordStatus').textContent = data.availability?.word_frequency || '暂无笔记文本';
      setEmptyChart(charts.wordChart, '暂无可用标题/正文/标签');
    }

    const correlation = chartsData.correlation;
    $('#correlationStatus').textContent = data.availability?.correlation || '样本不足';
    if (correlation?.values?.length) {
      const cells = [];
      correlation.values.forEach((row, y) => row.forEach((value, x) => { if (value !== null) cells.push([x, y, value]); }));
      charts.correlationChart?.setOption({
        ...baseChart,
        tooltip: { position: 'top', formatter: (item) => `${correlation.labels[item.data[1]]} × ${correlation.labels[item.data[0]]}<br>${item.data[2]}` },
        grid: { left: 88, right: 16, top: 12, bottom: 26 },
        xAxis: { type: 'category', data: correlation.labels, axisLabel: { fontSize: 8, color: '#89948d', interval: 0, rotate: 25 }, splitArea: { show: true } },
        yAxis: { type: 'category', data: correlation.labels, axisLabel: { fontSize: 8, color: '#89948d' }, splitArea: { show: true } },
        visualMap: { min: -1, max: 1, calculable: false, orient: 'horizontal', left: 'center', bottom: 0, textStyle: { fontSize: 7 }, inRange: { color: ['#d88a79', '#f5f6f3', '#4b9c78'] } },
        series: [{ type: 'heatmap', data: cells, label: { show: true, fontSize: 8, formatter: (item) => Number(item.data[2]).toFixed(2) }, emphasis: { itemStyle: { shadowBlur: 6, shadowColor: '#3334' } } }],
      }, true);
    } else setEmptyChart(charts.correlationChart, '至少需要 3 位共同完整样本');

    const trend = chartsData.batch_trend || [];
    $('#trendStatus').textContent = data.availability?.history || '历史快照不足';
    if (trend.length >= 2) {
      charts.trendChart?.setOption({ ...baseChart, grid: { left: 45, right: 15, top: 15, bottom: 38 }, xAxis: { type: 'category', data: trend.map((item) => item.captured_at), axisLabel: { fontSize: 7, rotate: 20, color: '#89948d' } }, yAxis: { type: 'value', axisLabel: { fontSize: 8, color: '#89948d' }, splitLine: { lineStyle: { color: '#f0f2f0' } } }, series: [{ type: 'line', smooth: true, symbolSize: 6, data: trend.map((item) => item.median_followers), itemStyle: { color: '#438f6c' }, areaStyle: { color: '#83b99b33' } }] }, true);
    } else setEmptyChart(charts.trendChart, '需要不同采集时间的快照');

    $('#followerPriceStatus').textContent = data.availability?.follower_price || '';
    $('#readingStatus').textContent = data.availability?.reading_engagement || '';
    const noteMetrics = chartsData.note_metrics || [];
    const metricKeys = ['曝光量', '阅读量', '点赞量', '收藏量', '评论量', '分享量'];
    const metricItems = metricKeys.map((name) => {
      const values = noteMetrics.map((item) => item[name]).filter((value) => Number.isFinite(value));
      return { name, value: values.length ? Math.round(values.reduce((sum, value) => sum + value, 0) / values.length) : null };
    });
    setBarChart('noteMetricsChart', metricItems);
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
    const labels = { baseline_import: '导入匿名基线', file_import: '导入 JSON / CSV', browser_open: '打开蒲公英浏览器', pgy_visible_capture: '采集当前可见页面', browser_close: '关闭蒲公英浏览器' };
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
    const suffix = elements.batchSelect.value ? `?batch_id=${encodeURIComponent(elements.batchSelect.value)}` : '';
    $('#exportJson').href = `/api/export/json${suffix}`;
    $('#exportCsv').href = `/api/export/csv${suffix}`;
  }

  function formatMetric(value, suffix = '') {
    if (value === null || value === undefined) return '—';
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}${suffix}` : escapeHtml(value);
  }

  async function refreshCreatorOptions() {
    const data = await requestJson('/api/creators');
    const current = elements.creatorSelect.value;
    elements.creatorSelect.innerHTML = '<option value="">新建匿名博主</option>' + (data.creators || []).map((creator) => `<option value="${escapeHtml(creator.creator_id)}">${escapeHtml(creator.pseudonym)}</option>`).join('');
    if ((data.creators || []).some((creator) => creator.creator_id === current)) elements.creatorSelect.value = current;
  }

  async function refreshCreatorLibrary() {
    const params = new URLSearchParams();
    const values = {
      category: $('#categoryFilter').value,
      followers_min: $('#followersMin').value.trim(),
      followers_max: $('#followersMax').value.trim(),
      quote_min: $('#quoteMin').value.trim(),
      quote_max: $('#quoteMax').value.trim(),
    };
    Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
    const data = await requestJson(`/api/creators?${params.toString()}`);
    const currentCategory = $('#categoryFilter').value;
    const categories = data.categories || [];
    $('#categoryFilter').innerHTML = '<option value="">全部领域</option>' + categories.map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join('');
    if (categories.includes(currentCategory)) $('#categoryFilter').value = currentCategory;
    $('#creatorCount').textContent = `${data.count || 0} 位博主`;
    const body = $('#creatorBody');
    if (!data.creators?.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty-cell">当前筛选条件下没有创作者</td></tr>';
      return;
    }
    const typeLabels = { blogger_list_sample: '列表', creator_profile_sample: '详情', creator_content_performance: '表现', fan_analysis: '画像', note_detail: '笔记' };
    body.innerHTML = data.creators.map((creator) => `<tr><td>${escapeHtml(creator.pseudonym)}</td><td>${escapeHtml((creator.categories || []).join('、') || '—')}</td><td>${formatMetric(creator.followers, ' 人')}</td><td>${formatMetric(creator.quote, ' 元')}</td><td>${formatMetric(creator.reading)}</td><td>${formatMetric(creator.engagement)}</td><td>${escapeHtml((creator.record_types || []).map((type) => typeLabels[type] || type).join(' / '))}</td><td>${escapeHtml(creator.last_seen_at || '—')}</td></tr>`).join('');
  }

  async function refreshFieldDictionary() {
    const fields = await requestJson('/api/fields');
    $('#dictionaryCount').textContent = `${fields.length} 个字段`;
    $('#dictionaryBody').innerHTML = fields.map((field) => `<tr><td>${escapeHtml(field.group)}</td><td>${escapeHtml(field.name)}</td><td>${escapeHtml(field.unit)}</td><td>${escapeHtml(field.period)}</td><td><span class="coverage-pill ${field.observed ? '' : 'pending-pill'}">${field.observed ? '真实样本已见' : '待采/待核'}</span></td></tr>`).join('');
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

  async function refreshAnalytics() {
    const params = new URLSearchParams();
    if (elements.batchSelect.value) params.set('batch_id', elements.batchSelect.value);
    const data = await requestJson(`/api/analytics?${params.toString()}`);
    renderAnalytics(data);
  }

  async function refreshDataViews() {
    await refreshDashboard();
    await Promise.all([refreshAnalytics(), refreshCreatorLibrary(), refreshCreatorOptions(), refreshFieldDictionary()]);
  }

  async function refreshTasks() {
    const tasks = await requestJson('/api/tasks');
    renderTasks(tasks);
    pendingTask = tasks.some((task) => ['queued', 'running'].includes(task.status));
    elements.importButton.disabled = pendingTask;
    elements.uploadButton.disabled = pendingTask;
    elements.openBrowserButton.disabled = pendingTask;
    elements.captureButton.disabled = pendingTask || !browserIsOpen;
    elements.closeBrowserButton.disabled = pendingTask || !browserIsOpen;
    if (lastDashboard) lastDashboard.tasks = tasks;
    if (activeTaskId) {
      const finished = tasks.find((task) => task.task_id === activeTaskId && ['completed', 'failed'].includes(task.status));
      if (finished) {
        activeTaskId = null;
        if (finished.status === 'completed') {
          await refreshDataViews();
          toast(finished.message || '任务完成');
        } else {
          toast(finished.message || '任务失败', true);
        }
      }
    }
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

  async function submitTask(endpoint, successMessage, body = null) {
    if (pendingTask) return;
    try {
      const result = await requestJson(endpoint, { method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}) });
      activeTaskId = result.task_id || null;
      toast(successMessage);
      await refreshTasks();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function refreshAll() {
    try {
      await Promise.all([refreshDataViews(), refreshTasks(), refreshBrowserStatus()]);
    } catch (error) {
      toast(`本地服务暂不可用：${error.message}`, true);
    }
  }

  function bindEvents() {
    elements.importButton.addEventListener('click', () => submitTask('/api/tasks/import-baseline', '已提交匿名基线导入任务'));
    elements.uploadButton.addEventListener('click', () => elements.uploadInput.click());
    elements.uploadInput.addEventListener('change', async () => {
      const file = elements.uploadInput.files?.[0];
      if (!file || pendingTask) return;
      const form = new FormData();
      form.append('file', file);
      try {
        const result = await requestJson('/api/tasks/import-file', { method: 'POST', body: form });
        activeTaskId = result.task_id || null;
        toast('已提交文件导入任务');
        await refreshTasks();
      } catch (error) {
        toast(error.message, true);
      } finally {
        elements.uploadInput.value = '';
      }
    });
    elements.openBrowserButton.addEventListener('click', () => submitTask('/api/browser/open', '正在打开独立可见浏览器'));
    elements.captureButton.addEventListener('click', () => submitTask('/api/browser/capture', '已提交当前可见页面采集任务', { creator_id: elements.creatorSelect.value }));
    elements.closeBrowserButton.addEventListener('click', () => submitTask('/api/browser/close', '正在关闭蒲公英浏览器'));
    elements.batchSelect.addEventListener('change', () => { refreshDashboard(); refreshAnalytics(); });
    elements.typeSelect.addEventListener('change', refreshDashboard);
    $('#filterCreators').addEventListener('click', () => refreshCreatorLibrary().catch((error) => toast(error.message, true)));
    $('#categoryFilter').addEventListener('change', () => refreshCreatorLibrary().catch((error) => toast(error.message, true)));
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
