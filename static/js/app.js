(() => {
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    batchSelect: $('#batchSelect'), typeSelect: $('#typeSelect'),
    importButton: $('#importButton'), openBrowserButton: $('#openBrowserButton'),
    captureButton: $('#captureButton'), closeBrowserButton: $('#closeBrowserButton'),
    uploadButton: $('#uploadButton'), uploadInput: $('#uploadInput'),
    startCollectionButton: $('#startCollectionButton'),
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
      'fanAggregateChart', 'correlationChart', 'trendChart', 'noteMetricsChart', 'sourceShareChart',
      'noteReadRankChart', 'noteInteractionRankChart', 'notePublishChart', 'noteTrendChart',
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
    if (trend.length) {
      const times = [...new Set(trend.flatMap((group) => group.points.map((point) => point.captured_at)))].sort();
      charts.trendChart?.setOption({ ...baseChart, legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 7 } }, grid: { left: 45, right: 15, top: 12, bottom: 48 }, xAxis: { type: 'category', data: times, axisLabel: { fontSize: 7, rotate: 20, color: '#89948d' } }, yAxis: { type: 'value', axisLabel: { fontSize: 8, color: '#89948d' }, splitLine: { lineStyle: { color: '#f0f2f0' } } }, series: trend.map((group) => { const byTime = new Map(group.points.map((point) => [point.captured_at, point.median_followers])); return { name: `${group.source} · ${group.period}`, type: 'line', smooth: true, symbolSize: 6, connectNulls: false, data: times.map((time) => byTime.get(time) ?? null) }; }) }, true);
    } else setEmptyChart(charts.trendChart, '需要同来源、同周期、重复创作者快照');

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

  function renderOverview(data) {
    const counts = data.counts || {};
    $('#overviewCreators').textContent = Number(counts.creators || 0).toLocaleString('zh-CN');
    $('#overviewNotes').textContent = Number(counts.notes || 0).toLocaleString('zh-CN');
    $('#overviewComments').textContent = Number(counts.comments || 0).toLocaleString('zh-CN');
    $('#overviewBatches').textContent = Number(counts.batches || 0).toLocaleString('zh-CN');
    $('#overviewUpdatedAt').textContent = `最近更新 ${data.latest_updated_at || '暂无'}`;
    $('#sourceShareScope').textContent = data.source_scope || '按采集来源统计';
    const sources = (data.sources || []).filter((item) => item.total_count > 0);
    if (!sources.length) return setEmptyChart(charts.sourceShareChart, '暂无来源记录');
    charts.sourceShareChart?.setOption({ ...baseChart, color: ['#438f6c', '#78ad86', '#aec9aa', '#e8c58f', '#b6c6d9', '#d48d7f'], tooltip: { trigger: 'item', formatter: (item) => `${escapeHtml(item.name)}<br>${item.value.toLocaleString()} 行 (${item.percent}%)` }, legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 8 } }, series: [{ type: 'pie', radius: ['38%', '66%'], center: ['50%', '44%'], label: { formatter: '{b} {d}%', fontSize: 8 }, data: sources.map((item) => ({ name: item.source, value: item.total_count })) }] }, true);
  }

  function renderGroupedRanking(chartId, groups, statusId = null) {
    const validGroups = (groups || []).filter((group) => group.items?.length);
    if (!validGroups.length) return setEmptyChart(charts[chartId], '当前筛选没有可用指标');
    const labels = [...new Set(validGroups.flatMap((group) => group.items.slice(0, 7).map((item) => item.label)))];
    charts[chartId]?.setOption({ ...baseChart, color: ['#438f6c', '#d19a64', '#7596bd', '#c8796d', '#8a78aa'], legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 7 } }, grid: { left: 118, right: 18, top: 12, bottom: 35 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, xAxis: { type: 'value', axisLabel: { fontSize: 8, color: '#89948d' }, splitLine: { lineStyle: { color: '#f0f2f0' } } }, yAxis: { type: 'category', data: labels, axisLabel: { fontSize: 7, color: '#89948d', width: 108, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } }, series: validGroups.map((group) => { const values = new Map(group.items.slice(0, 7).map((item) => [item.label, item.value])); return { name: group.source, type: 'bar', barMaxWidth: 12, data: labels.map((label) => values.get(label) ?? null) }; }) }, true);
    if (statusId) $(`#${statusId}`).textContent = validGroups.length > 1 ? `按来源分别排名（${validGroups.length} 组）` : `来源：${validGroups[0].source}`;
  }

  function getNoteFilterParams() {
    const params = new URLSearchParams();
    const values = { creator_id: $('#noteCreatorFilter').value, date_from: $('#noteDateFrom').value, date_to: $('#noteDateTo').value, note_type: $('#noteTypeFilter').value, topic: $('#noteTopicFilter').value };
    Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
    return params;
  }

  function renderNotes(data) {
    $('#noteCountLabel').textContent = `${data.count || 0} 篇笔记`;
    const fill = (selector, rows, valueKey, labelFn, emptyLabel, current) => {
      const node = $(selector);
      node.innerHTML = `<option value="">${emptyLabel}</option>` + rows.map((row) => `<option value="${escapeHtml(row[valueKey])}">${escapeHtml(labelFn(row))}</option>`).join('');
      if (rows.some((row) => String(row[valueKey]) === current)) node.value = current;
    };
    fill('#noteCreatorFilter', data.creators || [], 'creator_id', (row) => row.pseudonym, '全部创作者', $('#noteCreatorFilter').value);
    fill('#noteTypeFilter', (data.note_types || []).map((value) => ({ value })), 'value', (row) => row.value, '全部类型', $('#noteTypeFilter').value);
    fill('#noteTopicFilter', (data.topics || []).map((value) => ({ value })), 'value', (row) => row.value, '全部话题', $('#noteTopicFilter').value);
    const params = getNoteFilterParams();
    if (elements.batchSelect.value) params.set('batch_id', elements.batchSelect.value);
    $('#exportNotes').href = `/api/notes/export.csv?${params.toString()}`;
    const chartsData = data.charts || {};
    renderGroupedRanking('noteReadRankChart', chartsData.top_read, 'noteReadStatus');
    renderGroupedRanking('noteInteractionRankChart', chartsData.top_interactions);
    const publish = chartsData.publish_distribution || [];
    if (publish.length) charts.notePublishChart?.setOption({ ...baseChart, grid: { left: 38, right: 12, top: 12, bottom: 30 }, xAxis: { type: 'category', data: publish.map((item) => item.month), axisLabel: { fontSize: 8, color: '#89948d' } }, yAxis: { type: 'value', axisLabel: { fontSize: 8, color: '#89948d' }, splitLine: { lineStyle: { color: '#f0f2f0' } } }, series: [{ type: 'bar', data: publish.map((item) => item.count), barMaxWidth: 24, itemStyle: { color: '#7596bd', borderRadius: [4, 4, 0, 0] } }] }, true);
    else setEmptyChart(charts.notePublishChart, '没有可解析的发布时间');
    const trends = chartsData.metric_trends || [];
    $('#noteTrendStatus').textContent = data.availability?.metric_trends || '等待重复快照';
    if (trends.length) {
      const times = [...new Set(trends.flatMap((series) => series.points.map((point) => point.captured_at)))].sort();
      charts.noteTrendChart?.setOption({ ...baseChart, legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 7 } }, grid: { left: 45, right: 15, top: 12, bottom: 48 }, xAxis: { type: 'category', data: times, axisLabel: { fontSize: 7, rotate: 20, color: '#89948d' } }, yAxis: { type: 'value', axisLabel: { fontSize: 8, color: '#89948d' }, splitLine: { lineStyle: { color: '#f0f2f0' } } }, series: trends.map((group) => { const byTime = new Map(group.points.map((point) => [point.captured_at, point.value])); return { name: group.label, type: 'line', symbolSize: 5, connectNulls: false, data: times.map((time) => byTime.get(time) ?? null) }; }) }, true);
    } else setEmptyChart(charts.noteTrendChart, '缺少同笔记、来源、指标和统计周期的重复快照');
    const rows = data.notes || [];
    $('#noteRows').innerHTML = rows.length ? rows.map((row) => `<tr><td>${escapeHtml(row.creator_name)}</td><td>${escapeHtml(row.title)}</td><td>${escapeHtml(row.published_at || '—')}</td><td>${escapeHtml(row.note_type || '—')}</td><td>${escapeHtml((row.topics || []).join('、') || '—')}</td><td>${escapeHtml(row.source)}<br><small>${escapeHtml(row.batch_id || '—')}</small></td><td>${formatMetric(row.read_count)}</td><td>${formatMetric(row.like_count)}</td><td>${formatMetric(row.favorite_count)}</td><td>${formatMetric(row.interaction_comment_count)}</td><td>${formatMetric(row.share_count)}</td><td>${row.comment_count}</td></tr>`).join('') : '<tr><td colspan="12" class="empty-cell">当前筛选下没有笔记</td></tr>';
  }

  async function refreshOverview() {
    renderOverview(await requestJson('/api/overview'));
  }

  async function refreshNotes() {
    const params = getNoteFilterParams();
    if (elements.batchSelect.value) params.set('batch_id', elements.batchSelect.value);
    renderNotes(await requestJson(`/api/notes?${params.toString()}`));
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
    const labels = { baseline_import: '导入匿名基线', file_import: '导入 JSON / CSV', browser_open: '打开蒲公英浏览器', pgy_visible_capture: '采集当前可见页面', browser_close: '关闭蒲公英浏览器', visible_collection: '蒲公英 → 小红书可见采集' };
    container.innerHTML = tasks.slice(0, 8).map((task) => {
      const status = task.status || 'queued';
      const statusLabel = { queued: '排队中', running: `处理中 ${task.progress || 0}%`, paused: '已暂停', stopping: '正在停止', stopped: '已停止', completed: '已完成', failed: '失败' }[status] || status;
      const isCollection = task.task_type === 'visible_collection';
      let actions = '';
      if (isCollection && ['queued', 'running'].includes(status)) actions = `<button class="button button-secondary" data-task-action="pause" data-task-id="${escapeHtml(task.task_id)}">暂停</button><button class="button button-quiet" data-task-action="stop" data-task-id="${escapeHtml(task.task_id)}">停止</button>`;
      else if (isCollection && status === 'paused') actions = `<button class="button button-secondary" data-task-action="resume" data-task-id="${escapeHtml(task.task_id)}">恢复</button><button class="button button-quiet" data-task-action="stop" data-task-id="${escapeHtml(task.task_id)}">停止</button>`;
      else if (isCollection && ['stopped', 'failed'].includes(status)) actions = `<button class="button button-secondary" data-task-action="resume" data-task-id="${escapeHtml(task.task_id)}">从断点恢复</button>`;
      const countText = isCollection ? `<div class="task-counts">创作者 ${task.creators_visited || 0} · 笔记 ${task.notes_visited || 0} · 评论 ${task.comment_count || 0} · DOM 新增 ${task.dom_rows_added || 0}</div>` : '';
      const failures = (task.failures || []).length ? `<details class="task-failures"><summary>失败记录 ${task.failures.length} 项</summary><ul>${task.failures.map((failure) => `<li>创作者 ${failure.creator_index}${failure.note_index ? ` / 笔记 ${failure.note_index}` : ''}：${escapeHtml(failure.reason)}</li>`).join('')}</ul></details>` : '';
      const progress = isCollection ? `<div class="task-progress-track"><div class="task-progress-bar" style="width:${Math.max(0, Math.min(100, Number(task.progress) || 0))}%"></div></div>` : '';
      return `<div class="task-row"><div class="task-name">${escapeHtml(labels[task.task_type] || task.task_type)}</div><div class="task-message">${escapeHtml(task.message || '')} · ${escapeHtml(task.created_at || '')}</div><span class="task-state ${escapeHtml(status)}">${escapeHtml(statusLabel)}</span>${progress}${countText}${actions ? `<div class="task-controls">${actions}</div>` : ''}${failures}</div>`;
    }).join('');
  }

  function updateSelectOptions(data) {
    const currentBatch = elements.batchSelect.value;
    const batches = data.batches || [];
    elements.batchSelect.innerHTML = '<option value="__all__">全部批次</option>' + batches.map((batch) => `<option value="${escapeHtml(batch.batch_id)}">${escapeHtml(batch.source)} · ${escapeHtml(batch.captured_at)}</option>`).join('');
    if (currentBatch === '__all__' || batches.some((batch) => batch.batch_id === currentBatch)) elements.batchSelect.value = currentBatch;
    else if (data.selected_batch_id) elements.batchSelect.value = data.selected_batch_id;

    const currentType = elements.typeSelect.value;
    const types = data.record_types || [{ value: '', label: '全部数据' }];
    elements.typeSelect.innerHTML = types.map((type) => `<option value="${escapeHtml(type.value)}">${escapeHtml(type.label)}</option>`).join('');
    if (types.some((type) => type.value === currentType)) elements.typeSelect.value = currentType;
    const suffix = elements.batchSelect.value ? `?batch_id=${encodeURIComponent(elements.batchSelect.value)}` : '';
    $('#exportJson').href = `/api/export/json${suffix}`;
    $('#exportCsv').href = `/api/export/csv${suffix}`;
    $('#exportMetricsJson').href = `/api/export/metrics.json${suffix}`;
    $('#exportMetricsCsv').href = `/api/export/metrics.csv${suffix}`;
    $('#exportAnalyticsJson').href = `/api/export/analytics.json${suffix}`;
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
      location: $('#locationFilter').value,
      followers_min: $('#followersMin').value.trim(),
      followers_max: $('#followersMax').value.trim(),
      quote_min: $('#quoteMin').value.trim(),
      quote_max: $('#quoteMax').value.trim(),
      sort_by: $('#creatorSort').value,
    };
    Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
    if (elements.batchSelect.value) params.set('batch_id', elements.batchSelect.value);
    const data = await requestJson(`/api/creators?${params.toString()}`);
    const currentCategory = $('#categoryFilter').value;
    const currentLocation = $('#locationFilter').value;
    const compareSelection = Array.from($('#creatorCompareSelect').selectedOptions).map((option) => option.value);
    const categories = data.categories || [];
    $('#categoryFilter').innerHTML = '<option value="">全部领域</option>' + categories.map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join('');
    if (categories.includes(currentCategory)) $('#categoryFilter').value = currentCategory;
    const locations = data.locations || [];
    $('#locationFilter').innerHTML = '<option value="">全部地域</option>' + locations.map((location) => `<option value="${escapeHtml(location)}">${escapeHtml(location)}</option>`).join('');
    if (locations.includes(currentLocation)) $('#locationFilter').value = currentLocation;
    $('#creatorCount').textContent = `${data.count || 0} 位博主`;
    const body = $('#creatorBody');
    if (!data.creators?.length) {
      body.innerHTML = '<tr><td colspan="9" class="empty-cell">当前筛选条件下没有创作者</td></tr>';
      $('#creatorCompareSelect').innerHTML = '';
      return;
    }
    const typeLabels = { blogger_list_sample: '列表', creator_profile_sample: '详情', creator_content_performance: '表现', fan_analysis: '画像', note_detail: '笔记' };
    body.innerHTML = data.creators.map((creator) => `<tr><td>${escapeHtml(creator.pseudonym)}</td><td>${escapeHtml(creator.location || '—')}</td><td>${escapeHtml((creator.categories || []).join('、') || '—')}</td><td>${formatMetric(creator.followers, ' 人')}</td><td>${formatMetric(creator.quote, ' 元')}</td><td>${formatMetric(creator.reading)}</td><td>${formatMetric(creator.engagement)}</td><td>${escapeHtml((creator.record_types || []).map((type) => typeLabels[type] || type).join(' / '))}</td><td>${escapeHtml(creator.last_seen_at || '—')}</td></tr>`).join('');
    $('#creatorCompareSelect').innerHTML = data.creators.map((creator) => `<option value="${escapeHtml(creator.creator_id)}">${escapeHtml(creator.pseudonym)}</option>`).join('');
    Array.from($('#creatorCompareSelect').options).forEach((option) => { option.selected = compareSelection.includes(option.value); });
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
    await Promise.all([refreshOverview(), refreshAnalytics(), refreshCreatorLibrary(), refreshCreatorOptions(), refreshFieldDictionary(), refreshNotes()]);
  }

  async function refreshTasks() {
    const tasks = await requestJson('/api/tasks');
    renderTasks(tasks);
    pendingTask = tasks.some((task) => ['queued', 'running', 'paused', 'stopping'].includes(task.status));
    elements.importButton.disabled = pendingTask;
    elements.uploadButton.disabled = pendingTask;
    elements.openBrowserButton.disabled = pendingTask;
    elements.captureButton.disabled = pendingTask || !browserIsOpen;
    elements.closeBrowserButton.disabled = pendingTask || !browserIsOpen;
    elements.startCollectionButton.disabled = pendingTask;
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
    elements.startCollectionButton.addEventListener('click', () => {
      const settings = {
        source: $('#collectSource').value,
        collect_creator: $('#collectCreator').checked,
        collect_notes: $('#collectNotes').checked,
        collect_comments: $('#collectComments').checked,
        creator_count: Number($('#creatorLimit').value),
        notes_per_creator: Number($('#noteLimit').value),
        comments_per_note: Number($('#commentLimit').value),
      };
      submitTask('/api/tasks/collect-visible', '已提交可见采集任务', settings);
    });
    $('#taskList').addEventListener('click', async (event) => {
      const button = event.target.closest('[data-task-action]');
      if (!button) return;
      button.disabled = true;
      try {
        await requestJson(`/api/tasks/${encodeURIComponent(button.dataset.taskId)}/${button.dataset.taskAction}`, { method: 'POST' });
        await refreshTasks();
      } catch (error) {
        toast(error.message, true);
        button.disabled = false;
      }
    });
    elements.batchSelect.addEventListener('change', () => {
      Promise.all([refreshDashboard(), refreshAnalytics(), refreshCreatorLibrary(), refreshNotes()]).catch((error) => toast(error.message, true));
    });
    elements.typeSelect.addEventListener('change', refreshDashboard);
    $('#filterCreators').addEventListener('click', () => refreshCreatorLibrary().catch((error) => toast(error.message, true)));
    ['#categoryFilter', '#locationFilter', '#creatorSort'].forEach((selector) => $(selector).addEventListener('change', () => refreshCreatorLibrary().catch((error) => toast(error.message, true))));
    $('#creatorCompareSelect').addEventListener('change', () => {
      const selected = Array.from($('#creatorCompareSelect').selectedOptions);
      if (selected.length > 4) { selected.at(-1).selected = false; $('#creatorCompareStatus').textContent = '最多选择 4 位创作者'; }
      else $('#creatorCompareStatus').textContent = `${selected.length} 位已选择；请选 2 至 4 位`;
    });
    $('#compareCreators').addEventListener('click', async () => {
      if (elements.batchSelect.value === '__all__') {
        $('#creatorCompareStatus').textContent = '请选择一个具体采集批次，以保证来源和时间口径一致';
        return;
      }
      const params = new URLSearchParams();
      Array.from($('#creatorCompareSelect').selectedOptions).forEach((option) => params.append('creator_id', option.value));
      if (elements.batchSelect.value) params.set('batch_id', elements.batchSelect.value);
      try {
        const data = await requestJson(`/api/creators/compare?${params.toString()}`);
        $('#creatorCompareStatus').textContent = `${data.count} 位创作者 · 同一批次指标，单位按列保留`;
        $('#creatorCompareBody').innerHTML = data.creators.length ? data.creators.map((creator) => `<tr><td>${escapeHtml(creator.pseudonym)}</td><td>${escapeHtml(creator.location || '—')}</td><td>${escapeHtml((creator.categories || []).join('、') || '—')}</td><td>${formatMetric(creator.followers, ' 人')}</td><td>${formatMetric(creator.quote, ' 元')}</td><td>${formatMetric(creator.reading)}</td><td>${formatMetric(creator.engagement)}</td><td>${escapeHtml(creator.last_seen_at || '—')}</td></tr>`).join('') : '<tr><td colspan="8" class="empty-cell">所选创作者在当前批次没有可比记录</td></tr>';
      } catch (error) { $('#creatorCompareStatus').textContent = error.message; }
    });
    $('#filterNotes').addEventListener('click', () => refreshNotes().catch((error) => toast(error.message, true)));
    ['#noteCreatorFilter', '#noteTypeFilter', '#noteTopicFilter'].forEach((selector) => $(selector).addEventListener('change', () => refreshNotes().catch((error) => toast(error.message, true))));
    ['#noteDateFrom', '#noteDateTo'].forEach((selector) => $(selector).addEventListener('change', () => refreshNotes().catch((error) => toast(error.message, true))));
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
