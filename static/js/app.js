(() => {
  const $ = (selector) => document.querySelector(selector);
  const Theme = window.CSTheme || {
    fontFamily: 'Microsoft YaHei, Noto Sans SC, sans-serif', textColor: '#7e8981',
    axisLabelColor: '#89948d', axisLabelMuted: '#a0a9a2', axisLineColor: Theme.axisLineColor,
    splitLineColor: Theme.splitLineColor, emptyColor: '#a5ada7',
    chartColors: [Theme.scatterGreen, '#d19a64', Theme.seriesBlue, '#c8796d', '#8a78aa', '#78ad86'],
    seriesGreen: Theme.seriesGreen, seriesGreenSoft: Theme.seriesGreenSoft, seriesBlue: Theme.seriesBlue,
    seriesAmber: '#dfa05c', seriesRed: '#c77668', seriesPurple: '#8b83ae', scatterGreen: Theme.scatterGreen,
  };
  const demoMode = document.body.dataset.mode === 'demo';
  const elements = {
    batchSelect: $('#batchSelect'), sourceSelect: $('#sourceSelect'), typeSelect: $('#typeSelect'),
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
  let lastNotesData = null;
  let lastAnalyticsData = null;
  let lastCreatorCompareRows = [];
  let lastNoteCompareRows = [];
  const selectedUnifiedCreators = new Map();
  let unifiedCreatorOffset = 0;
  const UNIFIED_CREATOR_PAGE_SIZE = 50;
  let historyNoteOffset = 0;
  let historyAllView = null;
  let historyRequestVersion = 0;
  const HISTORY_NOTE_PAGE_SIZE = 12;

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
      'followerDistChart', 'quoteDistChart', 'quoteTypeChart', 'locationChart', 'followerPriceChart', 'readingEngagementChart',
      'fanAggregateChart', 'correlationChart', 'trendChart', 'noteMetricsChart', 'sourceShareChart',
      'noteReadRankChart', 'noteInteractionRankChart', 'metricDistributionChart', 'interactionBoxChart',
      'exposureReadChart', 'readingInteractionChart', 'notePublishChart', 'noteTopicChart', 'noteTrendChart',
      'audienceGenderChart', 'audienceAgeChart', 'audienceRegionChart', 'audienceInterestChart', 'audienceDeviceChart',
      'creatorCompareBarChart', 'creatorCompareRadarChart', 'noteCompareBarChart', 'noteCompareRadarChart',
      'historyFollowersChart', 'historyPerformanceChart', 'historyCoverageChart', 'libraryFollowersChart',
      'libraryReadChart', 'libraryInteractionChart', 'libraryCategoryChart', 'libraryLocationChart',
    ].forEach((id) => {
      const element = document.getElementById(id);
      if (element && window.echarts) charts[id] = echarts.init(element);
    });
    window.addEventListener('resize', () => Object.values(charts).forEach((chart) => chart.resize()));
  }

  const baseChart = {
    animationDuration: 500,
    textStyle: { fontFamily: Theme.fontFamily, color: Theme.textColor },
    tooltip: { trigger: 'item', confine: true },
  };

  function setEmptyChart(chart, message) {
    if (!chart) return;
    chart.clear();
    chart.setOption({
      ...baseChart,
      graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: message, fill: Theme.emptyColor, fontSize: 11 } }],
    });
  }

  function renderCharts(data) {
    if (!window.echarts) return;
    const types = data.type_summary || [];
    if (types.length) {
      charts.typeChart?.setOption({
        ...baseChart,
        color: Theme.chartColors,
        tooltip: { trigger: 'item', formatter: '{b}<br>{c} 条 ({d}%)' },
        legend: { bottom: 0, left: 'center', itemWidth: 7, itemHeight: 7, textStyle: { color: '#88938c', fontSize: 8 } },
        series: [{ type: 'pie', radius: ['43%', '68%'], center: ['50%', '43%'], avoidLabelOverlap: true, itemStyle: { borderColor: '#fff', borderWidth: 3 }, label: { show: false }, data: types.map((item) => ({ name: item.label, value: item.count })) }],
      }, true);
    } else setEmptyChart(charts.typeChart, '当前批次暂无记录');

    const noteMetrics = data.chart_data?.note_metrics || [];
    if (noteMetrics.length) {
      charts.noteChart?.setOption({ ...baseChart, grid: { left: 45, right: 15, top: 12, bottom: 27 }, xAxis: { type: 'category', data: noteMetrics.map((item) => item.name), axisLine: { lineStyle: { color: Theme.axisLineColor } }, axisTick: { show: false }, axisLabel: { color: '#929c95', fontSize: 8 } }, yAxis: { type: 'value', splitLine: { lineStyle: { color: Theme.splitLineColor } }, axisLabel: { color: '#a0a9a2', fontSize: 8 } }, series: [{ type: 'bar', data: noteMetrics.map((item) => item.value), barWidth: 21, itemStyle: { color: Theme.seriesGreen, borderRadius: [4, 4, 0, 0] } }] }, true);
    } else setEmptyChart(charts.noteChart, '当前批次没有笔记指标');

    const fans = data.chart_data?.fan_analysis || [];
    if (fans.length) {
      charts.fanChart?.setOption({ ...baseChart, grid: { left: 90, right: 24, top: 8, bottom: 13 }, xAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%', fontSize: 8, color: '#a0a9a2' }, splitLine: { lineStyle: { color: Theme.splitLineColor } } }, yAxis: { type: 'category', data: fans.map((item) => item.name), axisLabel: { fontSize: 8, color: '#858f88' }, axisTick: { show: false }, axisLine: { show: false } }, series: [{ type: 'bar', data: fans.map((item) => item.value), barWidth: 10, itemStyle: { color: Theme.seriesGreenSoft, borderRadius: 5 }, label: { show: true, position: 'right', formatter: '{c}%', fontSize: 8, color: '#728078' } }] }, true);
    } else setEmptyChart(charts.fanChart, '当前批次没有粉丝比例字段');


  }

  function setBarChart(id, items, horizontal = false, valueSuffix = '') {
    const chart = charts[id];
    if (!chart) return;
    const valid = (items || []).filter((item) => item.value !== null && item.value !== undefined);
    if (!valid.length) return setEmptyChart(chart, '当前样本没有可用字段');
    chart.setOption({
      ...baseChart,
      grid: { left: horizontal ? 90 : 38, right: 24, top: 20, bottom: horizontal ? 24 : 40 },
      xAxis: horizontal
        ? { type: 'value', axisLabel: { fontSize: 8, color: '#9aa39d' }, splitLine: { lineStyle: { color: Theme.splitLineColor } } }
        : { type: 'category', data: valid.map((item) => item.name), axisLabel: { interval: 0, rotate: 20, fontSize: 8, color: '#89948d' }, axisLine: { lineStyle: { color: Theme.axisLineColor } }, axisTick: { show: false } },
      yAxis: horizontal
        ? { type: 'category', data: valid.map((item) => item.name), axisLabel: { fontSize: 8, color: '#89948d' }, axisTick: { show: false }, axisLine: { show: false } }
        : { type: 'value', axisLabel: { fontSize: 8, color: '#9aa39d', formatter: `{value}${valueSuffix}` }, splitLine: { lineStyle: { color: Theme.splitLineColor } } },
      series: [{ type: 'bar', data: valid.map((item) => item.value), barMaxWidth: 22, itemStyle: { color: Theme.seriesGreen, borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] } }],
    }, true);
  }

  function setScatterChart(id, points, xName, yName) {
    const chart = charts[id];
    if (!chart) return;
    if (!points?.length) return setEmptyChart(chart, '没有具备共同字段的创作者');
    chart.setOption({
      ...baseChart,
      grid: { left: 45, right: 20, top: 22, bottom: 42 },
      tooltip: { trigger: 'item', formatter: (item) => `${escapeHtml(item.data[2])}<br>${escapeHtml(xName)}：${Number(item.data[0]).toLocaleString()}<br>${escapeHtml(yName)}：${Number(item.data[1]).toLocaleString()}` },
      xAxis: { type: 'value', name: xName, nameTextStyle: { fontSize: 8, color: '#929c95' }, axisLabel: { fontSize: 8, color: '#929c95', hideOverlap: true }, splitLine: { lineStyle: { color: Theme.splitLineColor } } },
      yAxis: { type: 'value', name: yName, nameTextStyle: { fontSize: 8, color: '#929c95' }, axisLabel: { fontSize: 8, color: '#929c95', hideOverlap: true }, splitLine: { lineStyle: { color: Theme.splitLineColor } } },
      series: [{ type: 'scatter', symbolSize: 9, itemStyle: { color: Theme.scatterGreen, opacity: .78 }, data: points.map((point) => [point.x, point.y, point.name]) }],
    }, true);
  }

  function renderComparisonBar(chartId, rows, key, labelKey, metricLabel) {
    const values = (rows || []).filter((row) => Number.isFinite(Number(row[key])) && row[key] !== null && row[key] !== undefined);
    if (!values.length) return setEmptyChart(charts[chartId], `没有共同可用的${metricLabel}精确值`);
    const labels = values.map((row) => row[labelKey]);
    charts[chartId]?.setOption({
      ...baseChart,
      grid: { left: 118, right: 24, top: 12, bottom: 18 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (params) => `${escapeHtml(labels[params[0].dataIndex])}<br>${escapeHtml(metricLabel)}：${Number(params[0].value).toLocaleString('zh-CN')}` },
      xAxis: { type: 'value', axisLabel: { fontSize: 8, color: '#929c95' }, splitLine: { lineStyle: { color: Theme.splitLineColor } } },
      yAxis: { type: 'category', data: labels, axisLabel: { fontSize: 8, color: '#89948d', width: 108, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
      series: [{ type: 'bar', data: values.map((row) => Number(row[key])), barMaxWidth: 22, itemStyle: { color: Theme.seriesGreen, borderRadius: [0, 4, 4, 0] } }],
    }, true);
  }

  function renderComparisonRadar(chartId, rows, axes, labelKey) {
    const complete = axes.filter((axis) => rows.length >= 2 && rows.every((row) => row[axis.key] !== null && row[axis.key] !== undefined && Number.isFinite(Number(row[axis.key]))) && Math.max(...rows.map((row) => Number(row[axis.key]))) > 0);
    if (!complete.length) return setEmptyChart(charts[chartId], '至少两条记录共有一个非零精确指标，才能绘雷达图');
    const indicators = complete.map((axis) => ({ name: axis.label, max: 100 }));
    charts[chartId]?.setOption({
      ...baseChart,
      color: Theme.chartColors.slice(0, 4),
      legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 8 } },
      tooltip: { trigger: 'item', formatter: (params) => {
        const row = rows.find((item) => item[labelKey] === params.name);
        return `${escapeHtml(params.name)}<br>${complete.map((axis) => `${escapeHtml(axis.label)}：${Number(row[axis.key]).toLocaleString('zh-CN')}`).join('<br>')}`;
      } },
      radar: { indicator: indicators, radius: '62%', axisName: { color: '#7f8b82', fontSize: 8 }, splitLine: { lineStyle: { color: '#e9eee9' } }, splitArea: { areaStyle: { color: ['#fbfcfb', '#f5f8f5'] } } },
      series: [{ type: 'radar', data: rows.map((row) => ({ name: row[labelKey], value: complete.map((axis) => {
        const maximum = Math.max(...rows.map((item) => Number(item[axis.key])));
        return maximum > 0 ? Number(row[axis.key]) / maximum * 100 : 0;
      }) })) }],
    }, true);
  }

  function renderCreatorComparison(data) {
    const rows = data.creators || [];
    lastCreatorCompareRows = rows;
    const status = data.availability?.publish_frequency || '同一批次、来源范围内对比';
    $('#creatorCompareStatus').textContent = `${data.count} 位创作者 · ${status}`;
    $('#creatorCompareBody').innerHTML = rows.length ? rows.map((creator) => `<tr><td>${escapeHtml(creator.pseudonym)}</td><td>${escapeHtml(creator.platform_creator_id || '—')}</td><td>${escapeHtml(creator.location || '—')}</td><td>${escapeHtml((creator.categories || []).join('、') || '—')}</td><td>${formatMetric(creator.followers, ' 人')}</td><td>${escapeHtml(quoteDisplay(creator))}</td><td>${formatMetric(creator.reading)}</td><td>${formatMetric(creator.engagement)}</td><td>${escapeHtml(creator.last_seen_at || '—')}</td><td>${creator.publish_frequency_30d === null || creator.publish_frequency_30d === undefined ? '—' : `${creator.publish_frequency_30d} 篇`}</td></tr>`).join('') : '<tr><td colspan="10" class="empty-cell">所选创作者在当前批次没有可比记录</td></tr>';
    const key = $('#creatorCompareMetric').value;
    const option = $('#creatorCompareMetric').selectedOptions[0];
    renderComparisonBar('creatorCompareBarChart', rows, key, 'pseudonym', option?.textContent || key);
    renderComparisonRadar('creatorCompareRadarChart', rows, [
      { key: 'followers', label: '粉丝数' }, { key: 'quote', label: '精确报价' },
      { key: 'reading', label: '阅读中位数' }, { key: 'engagement', label: '互动中位数' },
      { key: 'publish_frequency_30d', label: '可见发文数' },
    ], 'pseudonym');
    const profiles = (lastAnalyticsData?.charts?.audience_groups || []).flatMap((group) => group.profiles || []);
    renderAudienceComparison(Array.from($('#creatorCompareSelect').selectedOptions).map((item) => item.value), profiles);
  }

  function renderNoteComparison(data) {
    const rows = data.notes || [];
    lastNoteCompareRows = rows;
    $('#noteCompareStatus').textContent = `${rows.length} 篇 · 来源 ${data.source || '—'} · 批次 ${data.batch_id || '—'} · ${data.availability?.interaction_rate || ''} · ${data.availability?.publish_frequency || ''}`;
    $('#noteCompareBody').innerHTML = rows.length ? rows.map((note) => `<tr><td>${escapeHtml(note.creator_name)}<br><small>${escapeHtml(note.platform_creator_id || '—')}</small></td><td>${escapeHtml(note.title)}<br><small>${escapeHtml(note.note_id)}</small></td><td>${escapeHtml(note.source)}<br><small>${escapeHtml(note.batch_id || data.batch_id)}</small></td><td>${formatMetric(note.followers, ' 人')}</td><td>${formatMetric(note.quote, ' 元')}</td><td>${formatMetric(note.read_count)}</td><td>${formatMetric(note.like_count)}</td><td>${formatMetric(note.favorite_count)}</td><td>${formatMetric(note.interaction_comment_count)}</td><td>${formatMetric(note.share_count)}</td><td>${note.interaction_rate === null ? '—' : `${note.interaction_rate}%`}</td><td>${note.publish_frequency_30d === null || note.publish_frequency_30d === undefined ? '—' : `${note.publish_frequency_30d} 篇`}</td><td>${note.saved_comment_count ?? '—'}</td></tr>`).join('') : '<tr><td colspan="13" class="empty-cell">当前批次没有可比笔记记录</td></tr>';
    const key = $('#noteCompareMetric').value;
    const option = $('#noteCompareMetric').selectedOptions[0];
    const labels = rows.map((note) => ({ ...note, compare_label: `${note.creator_name} · ${note.title}` }));
    renderComparisonBar('noteCompareBarChart', labels, key, 'compare_label', option?.textContent || key);
    renderComparisonRadar('noteCompareRadarChart', rows, [
      { key: 'read_count', label: '阅读量' }, { key: 'like_count', label: '点赞量' },
      { key: 'favorite_count', label: '收藏量' }, { key: 'interaction_comment_count', label: '评论量' },
      { key: 'share_count', label: '分享量' }, { key: 'interaction_rate', label: '互动率 %' },
    ], 'label');
  }

  function renderAnalytics(data) {
    lastAnalyticsData = data;
    const chartsData = data.charts || {};
    const library = data.library_summary || null;
    $('#analysisScope').textContent = library
      ? `全量达人库（主库 + 课堂库 + 历史资料，共 ${library.total_count} 条展示记录）`
      : (data.scope || '基于已保存的创作者快照');
    $('#analysisCount').textContent = `${library ? library.total_count : (data.counts?.creators || 0)} 位创作者 · ${data.counts?.notes || 0} 条笔记（笔记指标仅当前批次）`;

    const followerItems = library?.follower_distribution?.length ? library.follower_distribution : (chartsData.follower_distribution || []);
    $('#followerDistStatus').textContent = library
      ? `全量库粉丝有效 n=${library.followers_valid}`
      : `精确粉丝值 n=${chartsData.follower_sample_count || 0}`;
    $('#quoteDistStatus').textContent = `全部报价精确值 n=${chartsData.quote_sample_count || 0}；起价与区间不进入数值区间`;
    $('#quoteTypeStatus').textContent = `${data.availability?.quote_types || '报价状态未计算'}；报价口径仅主库快照样本`;
    setBarChart('followerDistChart', followerItems);
    setBarChart('quoteDistChart', chartsData.quote_distribution, true);
    setBarChart('quoteTypeChart', chartsData.quote_status_distribution, true);
    setScatterChart('followerPriceChart', chartsData.follower_price_scatter, '粉丝数', '报价（元）');

    const readPoints = library?.reading_interaction_scatter?.length ? library.reading_interaction_scatter : (chartsData.reading_engagement_scatter || []);
    setScatterChart('readingEngagementChart', readPoints, '阅读中位数', '互动中位数');
    $('#followerPriceStatus').textContent = `共同有效样本 n=${chartsData.follower_price_sample_count || 0}；报价口径仅主库快照`;
    $('#readingStatus').textContent = library
      ? `全量库阅读/互动共同有效 n=${library.reading_interaction_valid}`
      : `共同有效样本 n=${chartsData.reading_engagement_sample_count || 0}`;

    const locations = library?.location_distribution?.length ? library.location_distribution : (chartsData.location_distribution || []);
    $('#locationStatus').textContent = `有效地域 ${locations.reduce((sum, item) => sum + item.value, 0)} 位创作者`;
    setBarChart('locationChart', locations, true);
    setBarChart('fanAggregateChart', (chartsData.fan_analysis || []).map((item) => ({ name: `${item.name} (${item.count})`, value: item.value })), true, '%');
    renderAudienceProfiles(chartsData.audience_groups || []);

    const rawCategories = library?.category_distribution?.length ? library.category_distribution : (chartsData.category_distribution || []);
    const categories = rawCategories.map((item) => ({ name: item.name, value: item.value ?? item.count ?? 0 }));
    if (categories.length) {
      charts.categoryChart?.setOption({ ...baseChart, color: Theme.chartColors, tooltip: { trigger: 'item', formatter: '{b}<br>{c} 位博主 ({d}%)' }, series: [{ type: 'pie', radius: ['38%', '66%'], center: ['50%', '48%'], label: { fontSize: 8, color: '#77847b' }, labelLine: { length: 7, length2: 5 }, data: categories }] }, true);
    } else setEmptyChart(charts.categoryChart, '当前样本没有内容领域');
    const words = chartsData.word_frequency || [];
    if (words.length) {
      $('#wordStatus').textContent = data.availability?.word_frequency || '已有分词';
      charts.wordChart?.setOption({ ...baseChart, grid: { left: 50, right: 15, top: 10, bottom: 24 }, xAxis: { type: 'category', data: words.map((item) => item.word), axisLabel: { interval: 0, rotate: 25, fontSize: 8, color: '#88938c' }, axisLine: { lineStyle: { color: Theme.axisLineColor } }, axisTick: { show: false } }, yAxis: { type: 'value', splitLine: { lineStyle: { color: Theme.splitLineColor } }, axisLabel: { fontSize: 8, color: '#a0a9a2' } }, series: [{ type: 'bar', data: words.map((item) => item.count), barWidth: 13, itemStyle: { color: Theme.seriesAmber, borderRadius: [3, 3, 0, 0] } }] }, true);
      const cloud = $('#wordCloudImage');
      cloud.hidden = false;
      const cloudParams = getNoteFilterParams();
      cloudParams.set('kind', $('#wordKindSelect').value);
      cloudParams.set('v', Date.now());
      cloud.src = `/api/wordcloud.png?${cloudParams.toString()}`;
      cloud.onerror = () => { cloud.hidden = true; };
    } else {
      $('#wordCloudImage').hidden = true;
      $('#wordCloudImage').removeAttribute('src');
      $('#wordStatus').textContent = data.availability?.word_frequency || '暂无笔记文本';
      setEmptyChart(charts.wordChart, '当前筛选文本不足以分词');
    }

    $('#followerPriceStatus').textContent = data.availability?.follower_price || '';
    $('#readingStatus').textContent = data.availability?.reading_engagement || '';
    const noteMetrics = chartsData.note_metrics || [];
    const metricKeys = ['曝光量', '阅读量', '点赞量', '收藏量', '评论量', '分享量', '新增关注'];
    const metricSources = [...new Set(noteMetrics.map((item) => item.source))];
    const noteMetricSeries = metricSources.map((source) => {
      const sourceRows = noteMetrics.filter((item) => item.source === source);
      return {
        name: `${source} (n=${sourceRows.length})`,
        type: 'bar',
        barMaxWidth: 18,
        data: metricKeys.map((key) => {
          const values = sourceRows.map((item) => item[key]).filter((value) => Number.isFinite(value));
          return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
        }),
      };
    });
    if (noteMetricSeries.length) {
      charts.noteMetricsChart?.setOption({ ...baseChart, legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 8 } }, grid: { left: 45, right: 14, top: 18, bottom: 48 }, xAxis: { type: 'category', data: metricKeys, axisLabel: { fontSize: 8, rotate: 22 } }, yAxis: { type: 'value', minInterval: 1 }, series: noteMetricSeries }, true);
    } else setEmptyChart(charts.noteMetricsChart, '当前筛选没有笔记指标');
  }

  function aggregateProfileValues(profiles, field, average = false) {
    const counts = new Map();
    profiles.forEach((profile) => (profile[field] || []).forEach((item) => {
      const current = counts.get(item.label) || { count: 0, sum: 0, n: 0 };
      current.count += 1;
      if (Number.isFinite(item.value)) { current.sum += item.value; current.n += 1; }
      counts.set(item.label, current);
    }));
    return [...counts.entries()].map(([name, item]) => ({
      name,
      value: average && item.n ? Number((item.sum / item.n).toFixed(2)) : item.count,
      sample_count: average ? item.n : item.count,
    })).sort((a, b) => b.value - a.value || a.name.localeCompare(b.name));
  }

  function renderAudienceProfiles(groups) {
    const select = $('#audienceGroupSelect');
    if (!select) return;
    const current = select.value;
    select.innerHTML = '<option value="">选择兼容来源与周期</option>' + groups.map((group) => `<option value="${group.group_id}">${escapeHtml(group.label)}</option>`).join('');
    if (groups.some((group) => String(group.group_id) === current)) select.value = current;
    else if (groups.length) select.value = String(groups[0].group_id);
    const group = groups.find((item) => String(item.group_id) === select.value);
    if (!group) {
      ['audienceGenderChart', 'audienceAgeChart', 'audienceRegionChart', 'audienceInterestChart', 'audienceDeviceChart'].forEach((id) => setEmptyChart(charts[id], '当前筛选没有真实受众画像'));
      $('#audienceScope').textContent = '没有已保存的年龄、性别、地域、兴趣或设备字段；不补造比例。';
      $('#audienceCompareBody').innerHTML = '<tr><td colspan="9" class="empty-cell">没有可比较的受众画像</td></tr>';
      return;
    }
    const profiles = group.profiles || [];
    $('#audienceScope').textContent = `来源 ${group.source} · 周期 ${group.period} · 有画像记录 ${group.creator_count} 位；图表只在此组内汇总`;

    let gender = aggregateProfileValues(profiles, 'gender_distribution', true);
    const hasFullGender = gender.length > 0;
    if (!hasFullGender) {
      const leaders = new Map();
      profiles.filter((profile) => profile.gender_leading).forEach((profile) => leaders.set(profile.gender_leading, (leaders.get(profile.gender_leading) || 0) + 1));
      gender = [...leaders.entries()].map(([name, value]) => ({ name, value }));
    }
    $('#audienceGenderStatus').textContent = hasFullGender ? `页面提供分类占比；按有该分类值的创作者平均，n=${profiles.filter((item) => item.gender_distribution?.length).length}` : `仅统计页面报告的主要性别类别次数，n=${profiles.filter((item) => item.gender_leading).length}；不推算其余类别`;
    if (gender.length) charts.audienceGenderChart?.setOption({ ...baseChart, tooltip: { trigger: 'item', formatter: (item) => `${escapeHtml(item.name)}<br>${hasFullGender ? `创作者平均值 ${item.value}%` : `主要类别报告次数 ${item.value}`}` }, legend: { type: 'scroll', bottom: 0 }, series: [{ type: 'pie', radius: ['36%', '62%'], center: ['50%', '43%'], label: { formatter: '{b} {d}%' }, data: gender }] }, true);
    else setEmptyChart(charts.audienceGenderChart, '没有已采集的性别画像');

    let ages = aggregateProfileValues(profiles, 'age_distribution', true);
    const hasFullAge = ages.length > 0;
    if (!hasFullAge) {
      const leaders = new Map();
      profiles.filter((profile) => profile.age_leading).forEach((profile) => leaders.set(profile.age_leading, (leaders.get(profile.age_leading) || 0) + 1));
      ages = [...leaders.entries()].map(([name, value]) => ({ name, value }));
    }
    $('#audienceAgeStatus').textContent = hasFullAge ? `按来源与周期组内平均；有效画像 n=${profiles.filter((item) => item.age_distribution?.length).length}` : `仅展示页面报告的主要年龄段次数，n=${profiles.filter((item) => item.age_leading).length}`;
    setBarChart('audienceAgeChart', ages, true, hasFullAge ? '%' : ' 位');

    const regions = aggregateProfileValues(profiles, 'regions').slice(0, 10);
    $('#audienceRegionStatus').textContent = `报告的粉丝省份 Top 10；创作者出现次数，画像 n=${profiles.filter((item) => item.regions?.length).length}`;
    setBarChart('audienceRegionChart', regions, true, ' 位');
    const interests = aggregateProfileValues(profiles, 'interests').slice(0, 10);
    $('#audienceInterestStatus').textContent = `页面报告的兴趣标签出现次数；画像 n=${profiles.filter((item) => item.interests?.length).length}`;
    setBarChart('audienceInterestChart', interests, true, ' 位');
    const devices = aggregateProfileValues(profiles, 'devices', true);
    $('#audienceDeviceStatus').textContent = `只显示页面采集到的品牌比例；样本 n=${profiles.filter((item) => item.devices?.length).length}，不补齐未提供品牌`;
    setBarChart('audienceDeviceChart', devices, true, '%');
    renderAudienceComparison(profiles.map((profile) => profile.creator_id), profiles);
  }

  function renderAudienceComparison(selectedIds, profiles) {
    const selected = new Set(selectedIds);
    const rows = profiles.filter((profile) => selected.has(profile.creator_id));
    const compatible = new Map();
    rows.forEach((profile) => {
      const key = `${profile.source}\u0000${profile.period}`;
      const group = compatible.get(key) || [];
      group.push(profile);
      compatible.set(key, group);
    });
    const comparableRows = [...compatible.values()].filter((group) => new Set(group.map((profile) => profile.creator_id)).size >= 2).flat();
    if (comparableRows.length < 2) {
      $('#audienceCompareBody').innerHTML = '<tr><td colspan="9" class="empty-cell">当前同来源、同周期画像少于 2 位创作者</td></tr>';
      return;
    }
    $('#audienceCompareBody').innerHTML = comparableRows.map((profile) => {
      const rawValues = (items) => (items || []).map((item) => `${item.label}${item.raw_value === null || item.raw_value === undefined ? '' : ` ${item.raw_value}`}`).join('、') || '未提供';
      const share = (key) => profile.shares?.[key]?.raw_value ?? '未提供';
      return `<tr><td>${escapeHtml(profile.creator_name)}</td><td>${escapeHtml(profile.source)}</td><td>${escapeHtml(profile.period)}</td><td>${escapeHtml(profile.gender_leading || rawValues(profile.gender_distribution))}</td><td>${escapeHtml(profile.age_leading || rawValues(profile.age_distribution))}</td><td>${escapeHtml(rawValues(profile.regions))}</td><td>${escapeHtml(rawValues(profile.interests))}</td><td>${escapeHtml(rawValues(profile.devices))}</td><td>活跃 ${escapeHtml(share('active'))}；阅读 ${escapeHtml(share('read'))}；互动 ${escapeHtml(share('interaction'))}</td></tr>`;
    }).join('');
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
    charts.sourceShareChart?.setOption({ ...baseChart, color: Theme.chartColors, tooltip: { trigger: 'item', formatter: (item) => `${escapeHtml(item.name)}<br>${item.value.toLocaleString()} 行 (${item.percent}%)` }, legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 8 } }, series: [{ type: 'pie', radius: ['38%', '66%'], center: ['50%', '44%'], label: { formatter: '{b} {d}%', fontSize: 8 }, data: sources.map((item) => ({ name: item.source, value: item.total_count })) }] }, true);
  }

  function renderGroupedRanking(chartId, groups, statusId = null) {
    const validGroups = (groups || []).filter((group) => group.items?.length);
    if (!validGroups.length) return setEmptyChart(charts[chartId], '当前筛选没有可用指标');
    const labels = [...new Set(validGroups.flatMap((group) => group.items.slice(0, 7).map((item) => item.label)))];
    charts[chartId]?.setOption({ ...baseChart, color: Theme.chartColors, legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 8 } }, grid: { left: 118, right: 18, top: 20, bottom: 40 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, xAxis: { type: 'value', axisLabel: { fontSize: 8, color: '#89948d' }, splitLine: { lineStyle: { color: Theme.splitLineColor } } }, yAxis: { type: 'category', data: labels, axisLabel: { fontSize: 8, color: '#89948d', width: 108, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } }, series: validGroups.map((group) => { const values = new Map(group.items.slice(0, 7).map((item) => [item.label, item.value])); return { name: group.source, type: 'bar', barMaxWidth: 12, data: labels.map((label) => values.get(label) ?? null) }; }) }, true);
    if (statusId) $(`#${statusId}`).textContent = validGroups.length > 1 ? `按来源分别排名（${validGroups.length} 组）` : `来源：${validGroups[0].source}`;
  }

  function renderNoteMetricDistribution(chartsData, metric) {
    const groups = (chartsData.metric_distributions || []).filter((item) => item.metric === metric);
    if (!groups.length) {
      $('#metricDistributionStatus').textContent = `${metric}：当前筛选没有精确数值`;
      return setEmptyChart(charts.metricDistributionChart, '没有可用于直方图的精确样本');
    }
    const labels = [...new Set(groups.flatMap((group) => group.bins.map((item) => item.label)))];
    $('#metricDistributionStatus').textContent = groups.map((group) => `${group.source} n=${group.sample_count}`).join(' · ');
    charts.metricDistributionChart?.setOption({
      ...baseChart,
      color: Theme.chartColors,
      legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 8 } },
      grid: { left: 44, right: 14, top: 20, bottom: 48 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 8, rotate: 20, color: '#89948d' } },
      yAxis: { type: 'value', minInterval: 1, name: '样本数' },
      series: groups.map((group) => {
        const counts = new Map(group.bins.map((item) => [item.label, item.count]));
        return { name: group.source, type: 'bar', barMaxWidth: 18, data: labels.map((label) => counts.get(label) ?? 0) };
      }),
    }, true);
  }

  function renderInteractionBoxplot(items) {
    if (!items.length) {
      $('#interactionBoxStatus').textContent = '没有精确点赞、收藏、评论或分享数';
      return setEmptyChart(charts.interactionBoxChart, '当前筛选没有箱线样本');
    }
    const labels = items.map((item) => `${item.label} (n=${item.sample_count})`);
    $('#interactionBoxStatus').textContent = `有效互动指标 n=${items.reduce((sum, item) => sum + item.sample_count, 0)}；每箱按来源和指标分组`;
    charts.interactionBoxChart?.setOption({
      ...baseChart,
      grid: { left: 48, right: 18, top: 20, bottom: 76 },
      tooltip: { trigger: 'item', formatter: (params) => `${escapeHtml(labels[params.dataIndex] || '')}<br>最小 ${params.data[0]} · Q1 ${params.data[1]} · 中位数 ${params.data[2]} · Q3 ${params.data[3]} · 最大 ${params.data[4]}` },
      xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 8, rotate: 25, width: 100, overflow: 'truncate' } },
      yAxis: { type: 'value', min: 0, name: '互动数' },
      series: [{ type: 'boxplot', data: items.map((item) => item.values), itemStyle: { color: '#dcebe0', borderColor: Theme.scatterGreen } }],
    }, true);
  }

  function renderGroupedScatter(chartId, points, xName, yName, statusId) {
    const groups = new Map();
    points.forEach((point) => {
      const group = groups.get(point.source) || [];
      group.push([point.x, point.y, point.label]);
      groups.set(point.source, group);
    });
    $(`#${statusId}`).textContent = `有效共同样本 n=${points.length}；${groups.size} 个来源组`;
    if (!points.length) return setEmptyChart(charts[chartId], '没有具备共同精确字段的笔记');
    const shortName = (source) => (source.length > 16 ? `${source.slice(0, 16)}…` : source);
    charts[chartId]?.setOption({
      ...baseChart,
      color: Theme.chartColors,
      legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 8 }, formatter: (name) => shortName(name) },
      grid: { left: 58, right: 18, top: 20, bottom: 52 },
      tooltip: { trigger: 'item', formatter: (params) => `${escapeHtml(params.value[2])}<br>${xName}：${params.value[0]}<br>${yName}：${params.value[1]}` },
      xAxis: { type: 'value', name: xName, axisLabel: { hideOverlap: true } },
      yAxis: { type: 'value', name: yName, axisLabel: { hideOverlap: true } },
      series: [...groups.entries()].map(([source, data]) => ({ name: shortName(source), type: 'scatter', symbolSize: 9, data })),
    }, true);
  }

  function getNoteFilterParams() {
    const params = new URLSearchParams();
    const values = {
      creator_id: $('#noteCreatorFilter').value,
      note_id: $('#noteIdFilter').value,
      category: $('#categoryFilter').value,
      source: elements.sourceSelect.value,
      batch_id: elements.batchSelect.value,
      date_from: $('#noteDateFrom').value,
      date_to: $('#noteDateTo').value,
      note_type: $('#noteTypeFilter').value,
      topic: $('#noteTopicFilter').value,
      q: $('#noteKeywordFilter').value.trim(),
    };
    Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
    return params;
  }

  function renderDashboardNoteSamples(rows) {
    const withBody = rows.filter((row) => String(row.body || '').trim());
    const samples = (withBody.length ? withBody : rows.filter((row) => (row.topics || []).length)).slice(0, 3);
    $('#dashboardNoteSamplesSummary').textContent = `展开真实笔记内容（${samples.length} 条）`;
    $('#dashboardNoteSamples').innerHTML = samples.map((row) => `<article class="dashboard-note-sample"><h3>${escapeHtml(row.title || '（无标题）')}</h3><p>${escapeHtml(row.body || '暂无正文')}</p><div class="history-note-topics">${(row.topics || []).map((topic) => `<span>${escapeHtml(topic)}</span>`).join('')}</div><small>${escapeHtml(row.creator_name || '未知创作者')} · ${escapeHtml(row.source || '未知来源')} · ${escapeHtml(row.batch_id || '未知批次')}</small></article>`).join('') || '<p class="empty-cell">当前筛选没有可展示的正文或话题</p>';
  }

  function renderNotes(data) {
    lastNotesData = data;
    renderDashboardNoteSamples(data.notes || []);
    $('#noteCountLabel').textContent = `${data.count || 0} 篇笔记`;
    const fill = (selector, rows, valueKey, labelFn, emptyLabel, current) => {
      const node = $(selector);
      node.innerHTML = `<option value="">${emptyLabel}</option>` + rows.map((row) => `<option value="${escapeHtml(row[valueKey])}">${escapeHtml(labelFn(row))}</option>`).join('');
      if (rows.some((row) => String(row[valueKey]) === current)) node.value = current;
    };
    fill('#noteCreatorFilter', data.creators || [], 'creator_id', (row) => row.pseudonym, '全部创作者', $('#noteCreatorFilter').value);
    fill('#noteIdFilter', data.note_options || [], 'note_id', (row) => `${row.creator_name} · ${row.title}`, '全部笔记', $('#noteIdFilter').value);
    fill('#noteTypeFilter', (data.note_types || []).map((value) => ({ value })), 'value', (row) => row.value, '全部类型', $('#noteTypeFilter').value);
    fill('#noteTopicFilter', (data.topics || []).map((value) => ({ value })), 'value', (row) => row.value, '全部话题', $('#noteTopicFilter').value);
    const params = getNoteFilterParams();
    $('#exportNotes').href = `/api/notes/export.csv?${params.toString()}`;
    const chartsData = data.charts || {};
    renderGroupedRanking('noteReadRankChart', chartsData.top_read, 'noteReadStatus');
    renderGroupedRanking('noteInteractionRankChart', chartsData.top_interactions);
    renderNoteMetricDistribution(chartsData, $('#noteMetricSelect').value);
    renderInteractionBoxplot(chartsData.interaction_boxplot || []);
    renderGroupedScatter('exposureReadChart', chartsData.exposure_read_scatter || [], '曝光量', '阅读量', 'exposureReadStatus');
    renderGroupedScatter('readingInteractionChart', chartsData.reading_interaction_scatter || [], '阅读量', '互动量', 'readingInteractionStatus');
    const publish = chartsData.publish_distribution || [];
    if (publish.length) charts.notePublishChart?.setOption({ ...baseChart, grid: { left: 38, right: 12, top: 20, bottom: 34 }, xAxis: { type: 'category', data: publish.map((item) => item.month), axisLabel: { fontSize: 8, color: '#89948d' } }, yAxis: { type: 'value', axisLabel: { fontSize: 8, color: '#89948d' }, splitLine: { lineStyle: { color: Theme.splitLineColor } } }, series: [{ type: 'bar', data: publish.map((item) => item.count), barMaxWidth: 24, itemStyle: { color: Theme.seriesBlue, borderRadius: [4, 4, 0, 0] } }] }, true);
    else setEmptyChart(charts.notePublishChart, '没有可解析的发布时间');
    const topicFreq = (data.text_analysis?.topics?.frequencies || []).map((item) => ({ name: item.word, value: item.count }));
    const historyTexts = data.history_texts || {};
    $('#noteTopicStatus').textContent = historyTexts.topics_count
      ? `含历史资料话题候选 ${formatMetric(historyTexts.topics_count || 0)} 篇；话题不代表互动表现`
      : '话题来自当前批次笔记；不代表互动表现';
    if (topicFreq.length) setBarChart('noteTopicChart', topicFreq.slice(0, 15), true);
    else setEmptyChart(charts.noteTopicChart, '当前筛选没有可用话题');
    const trends = chartsData.metric_trends || [];
    $('#noteTrendStatus').textContent = data.availability?.metric_trends || '等待重复快照';
    if (trends.length) {
      const times = [...new Set(trends.flatMap((series) => series.points.map((point) => point.captured_at)))].sort();
      charts.noteTrendChart?.setOption({ ...baseChart, legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 8 } }, grid: { left: 45, right: 15, top: 12, bottom: 48 }, xAxis: { type: 'category', data: times, axisLabel: { fontSize: 8, rotate: 20, color: '#89948d' } }, yAxis: { type: 'value', axisLabel: { fontSize: 8, color: '#89948d' }, splitLine: { lineStyle: { color: Theme.splitLineColor } } }, series: trends.map((group) => { const byTime = new Map(group.points.map((point) => [point.captured_at, point.value])); return { name: group.label, type: 'line', symbolSize: 5, connectNulls: false, data: times.map((time) => byTime.get(time) ?? null) }; }) }, true);
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
    await refreshNoteCompareOptions();
  }

  async function refreshNoteCompareOptions() {
    const select = $('#noteCompareSelect');
    const batchId = elements.batchSelect.value;
    if (!batchId || batchId === '__all__') {
      select.innerHTML = '';
      $('#noteCompareStatus').textContent = '请选择一个具体采集批次后选择 2 至 4 篇';
      lastNoteCompareRows = [];
      setEmptyChart(charts.noteCompareBarChart, '请选择一个具体采集批次');
      setEmptyChart(charts.noteCompareRadarChart, '请选择一个具体采集批次');
      return;
    }
    const params = new URLSearchParams({ batch_id: batchId });
    if (elements.sourceSelect.value) params.set('source', elements.sourceSelect.value);
    const result = await requestJson(`/api/notes/compare/options?${params.toString()}`);
    const previous = new Set(Array.from(select.selectedOptions).map((option) => option.value));
    select.innerHTML = (result.options || []).map((row) => `<option value="${escapeHtml(row.note_id)}">${escapeHtml(row.creator_name)} · ${escapeHtml(row.title || '（无标题）')} · ${escapeHtml(row.note_id)}</option>`).join('');
    Array.from(select.options).forEach((option) => { if (previous.has(option.value)) option.selected = true; });
    $('#noteCompareStatus').textContent = `${result.options?.length || 0} 篇当前批次可选笔记；选择 2 至 4 篇`;
    lastNoteCompareRows = [];
    setEmptyChart(charts.noteCompareBarChart, '选择 2 至 4 篇笔记后查看');
    setEmptyChart(charts.noteCompareRadarChart, '选择 2 至 4 篇笔记后查看');
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
    const labels = { baseline_import: '导入历史基线', file_import: '导入 JSON / CSV', browser_open: '打开蒲公英浏览器', pgy_visible_capture: '采集当前可见页面', browser_close: '关闭蒲公英浏览器', visible_collection: '蒲公英 → 小红书可见采集' };
    container.innerHTML = tasks.slice(0, 8).map((task) => {
      const status = task.status || 'queued';
      const statusLabel = { queued: '排队中', running: `处理中 ${task.progress || 0}%`, waiting_user: '等待你关闭弹窗', paused: '已暂停', stopping: '正在停止', stopped: '已停止', completed: '已完成', failed: '失败' }[status] || status;
      const isCollection = task.task_type === 'visible_collection';
      const hasFailedNotes = (task.failures || []).some((failure) => Number(failure.note_index) > 0);
      let actions = '';
      if (!demoMode && isCollection && ['queued', 'running', 'waiting_user'].includes(status)) actions = `<button class="button button-secondary" data-task-action="pause" data-task-id="${escapeHtml(task.task_id)}">暂停</button><button class="button button-quiet" data-task-action="stop" data-task-id="${escapeHtml(task.task_id)}">停止</button>`;
      else if (!demoMode && isCollection && status === 'paused') actions = `<button class="button button-secondary" data-task-action="resume" data-task-id="${escapeHtml(task.task_id)}">恢复</button><button class="button button-quiet" data-task-action="stop" data-task-id="${escapeHtml(task.task_id)}">停止</button>`;
      else if (!demoMode && isCollection && ['stopped', 'failed'].includes(status)) actions = `<button class="button button-secondary" data-task-action="resume" data-task-id="${escapeHtml(task.task_id)}">从断点恢复</button>${hasFailedNotes ? `<button class="button button-secondary" data-task-action="retry-failed" data-task-id="${escapeHtml(task.task_id)}">只重试失败笔记</button>` : ''}`;
      else if (!demoMode && isCollection && status === 'completed' && hasFailedNotes) actions = `<button class="button button-secondary" data-task-action="retry-failed" data-task-id="${escapeHtml(task.task_id)}">只重试失败笔记</button>`;
      const countText = isCollection ? `<div class="task-counts">创作者 ${task.creators_visited || 0} · 笔记 ${task.notes_visited || 0} · 评论 ${task.comment_count || 0} · 本批新增 ${task.records_added || 0} · 重复跳过 ${task.duplicate_records || 0} · DOM 新增 ${task.dom_rows_added || 0}</div>` : '';
      const failures = (task.failures || []).length ? `<details class="task-failures"><summary>失败记录 ${task.failures.length} 项</summary><ul>${task.failures.map((failure) => `<li>创作者 ${failure.creator_index}${failure.note_index ? ` / 笔记 ${failure.note_index}` : ''}：${escapeHtml(failure.reason)}${failure.attempts > 1 ? `（尝试 ${failure.attempts} 次）` : ''}</li>`).join('')}</ul></details>` : '';
      const progress = isCollection ? `<div class="task-progress-track"><div class="task-progress-bar" style="width:${Math.max(0, Math.min(100, Number(task.progress) || 0))}%"></div></div>` : '';
      const qualityLink = isCollection && task.batch_id ? `<a class="task-quality-link" href="/api/batches/${encodeURIComponent(task.batch_id)}/quality" target="_blank" rel="noopener">本批质量报告</a>` : '';
      return `<div class="task-row"><div class="task-name">${escapeHtml(labels[task.task_type] || task.task_type)}</div><div class="task-message">${escapeHtml(task.message || '')} · ${escapeHtml(task.created_at || '')}</div><span class="task-state ${escapeHtml(status)}">${escapeHtml(statusLabel)}</span>${progress}${countText}${actions || qualityLink ? `<div class="task-controls">${actions}${qualityLink}</div>` : ''}${failures}</div>`;
    }).join('');
  }

  function updateSelectOptions(data) {
    const currentBatch = elements.batchSelect.value;
    const currentSource = elements.sourceSelect.value;
    const batches = data.batches || [];
    elements.batchSelect.innerHTML = '<option value="__all__">全部批次</option>' + batches.map((batch) => `<option value="${escapeHtml(batch.batch_id)}">${escapeHtml(batch.source)} · ${escapeHtml(batch.captured_at)}</option>`).join('');
    if (currentBatch === '__all__' || batches.some((batch) => batch.batch_id === currentBatch)) elements.batchSelect.value = currentBatch;
    else if (data.selected_batch_id) elements.batchSelect.value = data.selected_batch_id;

    const sources = data.sources || [...new Set(batches.map((batch) => batch.source).filter(Boolean))];
    elements.sourceSelect.innerHTML = '<option value="">全部来源</option>' + sources.map((source) => `<option value="${escapeHtml(source)}">${escapeHtml(source)}</option>`).join('');
    if (sources.includes(currentSource)) elements.sourceSelect.value = currentSource;

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

  function quoteDisplay(creator) {
    const fields = creator.quote_fields || {};
    const labels = {
      '全部报价': '全',
      '图文笔记一口价': '图文',
      '视频笔记一口价': '视频',
    };
    const statuses = { parsed: '精确', starting_price: '起价', range_unresolved: '区间', not_numeric: '原值' };
    const values = Object.entries(labels).map(([key, label]) => {
      const detail = fields[key] || {};
      if (detail.raw_value === null || detail.raw_value === undefined || detail.raw_value === '') return `${label}：—`;
      return `${label}：${detail.raw_value}（${statuses[detail.parse_status] || detail.parse_status}）`;
    });
    return values.join('；');
  }

  async function refreshCreatorOptions() {
    const data = await requestJson('/api/creators');
    const current = elements.creatorSelect.value;
    elements.creatorSelect.innerHTML = '<option value="">新建创作者</option>' + (data.creators || []).map((creator) => `<option value="${escapeHtml(creator.creator_id)}">${escapeHtml(creator.pseudonym)}</option>`).join('');
    if ((data.creators || []).some((creator) => creator.creator_id === current)) elements.creatorSelect.value = current;
  }

  function updateUnifiedCompareButton() {
    const count = selectedUnifiedCreators.size;
    const button = $('#compareSelectedCreators');
    button.textContent = `去智能分析对比（${count}/4）`;
    button.disabled = count < 2 || count > 4;
  }

  function unifiedDetailMarkup(record) {
    const labels = {
      name: '名称', platform_id: '平台 ID', followers: '粉丝数',
      daily_read_median: '日常阅读中位数', daily_interaction_median: '日常互动中位数',
      likes_and_favorites: '获赞与收藏', published_notes: '发布笔记数',
      image_fixed_price: '图文报价原值', video_fixed_price: '视频报价原值',
      category: '领域', account_ip_location: '蒲公英账号 IP 属地', historical_region: '历史地域原值',
    };
    const values = Object.entries(record.fields || {}).map(([key, value]) => {
      if (value === null || value === undefined || value === '') return '';
      return `<div><dt>${escapeHtml(labels[key] || key)}</dt><dd>${escapeHtml(value)}</dd></div>`;
    }).filter(Boolean).join('');
    const identifiers = Object.entries(record.identifiers || {}).map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');
    const raw = (record.details || []).map((item) => {
      const label = item.label || item.key || item.original_field || '原始字段';
      const rawValue = item.raw_value ?? item.text_value ?? item.value ?? '';
      const meta = [item.original_field && `字段名 ${item.original_field}`, item.parse_status, item.unit, item.period].filter(Boolean).join(' · ');
      return `<div><dt>${escapeHtml(label)}${meta ? `<small> · ${escapeHtml(meta)}</small>` : ''}</dt><dd>${escapeHtml(rawValue || '—')}${item.numeric_value !== null && item.numeric_value !== undefined ? ` <small>解析值 ${formatMetric(item.numeric_value)}</small>` : ''}</dd></div>`;
    }).join('');
    const sources = (record.source_records || []).map((item) => {
      const paths = (item.source_paths || []).map((value) => `<small class="unified-source-path">${escapeHtml(value)}</small>`).join('');
      const facts = [item.source_name || record.source_name, item.row_number && `第 ${formatMetric(item.row_number)} 行`, item.batch_id && `批次 ${item.batch_id}`, item.captured_at || item.capture_time].filter(Boolean).join(' · ');
      return `<li>${escapeHtml(facts)}${paths}</li>`;
    }).join('');
    const prices = (record.prices || []).map((item) => `<div><dt>${escapeHtml(item.label || '报价')}</dt><dd>${escapeHtml(item.raw_value || '—')}</dd></div>`).join('');
    return `<details class="unified-row-details"><summary>查看全部字段与来源</summary><p class="unified-record-meta">${escapeHtml(record.bucket_label || '')} · ${escapeHtml(record.source_name || '来源未知')} · ${escapeHtml(record.captured_at || '采集时间未知')}</p><dl>${values}${identifiers}${prices}${raw || '<div><dd>没有额外原始字段</dd></div>'}</dl><ul class="history-lineage-list">${sources || '<li>暂无来源行信息</li>'}</ul></details>`;
  }

  async function refreshCreatorLibrary() {
    const params = new URLSearchParams({
      q: $('#creatorQuery').value.trim(),
      bucket: $('#creatorBucketFilter').value,
      category: $('#categoryFilter').value,
      ip_location: $('#locationFilter').value,
      followers_min: $('#followersMin').value.trim(),
      followers_max: $('#followersMax').value.trim(),
      offset: String(unifiedCreatorOffset),
      limit: String(UNIFIED_CREATOR_PAGE_SIZE),
    });
    if (elements.batchSelect.value && elements.batchSelect.value !== '__all__') params.set('batch_id', elements.batchSelect.value);
    if (elements.sourceSelect.value) params.set('source', elements.sourceSelect.value);
    const data = await requestJson(`/api/unified-creators?${params.toString()}`);
    const categorySelect = $('#categoryFilter');
    const locationSelect = $('#locationFilter');
    const previousCategory = categorySelect.value;
    const previousLocation = locationSelect.value;
    categorySelect.innerHTML = '<option value="">全部领域</option>' + (data.categories || []).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
    locationSelect.innerHTML = '<option value="">全部 IP 属地</option>' + (data.ip_locations || []).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
    if ((data.categories || []).includes(previousCategory)) categorySelect.value = previousCategory;
    if ((data.ip_locations || []).includes(previousLocation)) locationSelect.value = previousLocation;
    const groups = data.groups || [];
    const summary = data.filtered_summary || {};
    const medians = summary.medians || {};
    $('#libraryMatched').textContent = formatMetric(data.total_count || 0);
    $('#libraryFollowersValid').textContent = formatMetric(summary.followers_valid || 0);
    $('#libraryPerformanceValid').textContent = formatMetric(summary.reading_interaction_valid || 0);
    $('#libraryCategoryValid').textContent = formatMetric(summary.category_valid || 0);
    $('#libraryFollowersMedian').textContent = formatMetric(medians.followers, ' 人');
    $('#libraryReadMedian').textContent = formatMetric(medians.daily_read_median, ' 次');
    $('#libraryInteractionMedian').textContent = formatMetric(medians.daily_interaction_median, ' 次');
    $('#libraryFollowerStatus').textContent = `粉丝数有效 ${formatMetric(summary.followers_valid || 0)} 条；来源记录可能重复`;
    const toBuckets = (items) => (items || []).map((item) => ({ name: item.label, value: item.count }));
    const toTerms = (items) => (items || []).map((item) => ({ name: item.name, value: item.count }));
    if (summary.followers_valid) setBarChart('libraryFollowersChart', toBuckets(summary.follower_distribution));
    else setEmptyChart(charts.libraryFollowersChart, '当前筛选没有粉丝数');
    setBarChart('libraryReadChart', toBuckets(summary.read_distribution));
    setBarChart('libraryInteractionChart', toBuckets(summary.interaction_distribution));
    setBarChart('libraryCategoryChart', toTerms(summary.category_distribution), true);
    setBarChart('libraryLocationChart', toTerms(summary.location_distribution), true);
    $('#creatorCount').textContent = `${formatMetric(data.total_count || 0)} 条匹配记录`;
    $('#unifiedCreatorCount').textContent = formatMetric(data.total_count || 0);
    $('#unifiedCreatorGroups').textContent = groups.map((item) => `${item.label} ${formatMetric(item.count || 0)}`).join(' · ');
    const body = $('#unifiedCreatorBody');
    const records = data.records || [];
    if (!records.length) {
      body.innerHTML = '<tr><td colspan="9" class="empty-cell">当前筛选下没有达人资料</td></tr>';
    } else {
      body.innerHTML = records.map((record) => {
        const fields = record.fields || {};
        const selected = selectedUnifiedCreators.has(record.ref) ? ' checked' : '';
        const name = record.name || '未命名记录';
        const identifier = record.platform_id || Object.values(record.identifiers || {})[0] || '无平台 ID';
        const completeness = record.completeness || {};
        const source = [record.bucket_label, record.source_name, record.captured_at || '采集时间未知'].filter(Boolean).join(' · ');
        return `<tr><td><strong>${escapeHtml(name)}</strong><br><small>${escapeHtml(identifier)}</small>${unifiedDetailMarkup(record)}</td><td>${formatMetric(fields.followers, ' 人')}</td><td>${formatMetric(fields.daily_read_median, ' 次')}</td><td>${formatMetric(fields.daily_interaction_median, ' 次')}</td><td>${escapeHtml(fields.category || '—')}</td><td>${escapeHtml(fields.account_ip_location || '—')}</td><td><span class="coverage-pill coverage-tier-${completeness.percentage >= 80 ? 'high' : completeness.percentage >= 40 ? 'partial' : 'low'}">${formatMetric(completeness.valid_fields || 0)} / ${formatMetric(completeness.total_fields || 0)} · ${formatMetric(completeness.percentage || 0)}%</span></td><td>${escapeHtml(source)}</td><td><label class="unified-compare-check"><input type="checkbox" data-unified-compare data-ref="${escapeHtml(record.ref)}"${selected}>加入对比</label></td></tr>`;
      }).join('');
    }
    const offset = Number(data.offset || 0);
    const limit = Number(data.limit || UNIFIED_CREATOR_PAGE_SIZE);
    const total = Number(data.total_count || 0);
    $('#unifiedCreatorPageStatus').textContent = `${total ? offset + 1 : 0}–${Math.min(offset + limit, total)} / ${formatMetric(total)} 条`;
    $('#unifiedCreatorPrev').disabled = offset <= 0;
    $('#unifiedCreatorNext').disabled = offset + limit >= total;
    updateUnifiedCompareButton();
  }

  async function refreshFieldDictionary() {
    const fields = await requestJson('/api/fields');
    $('#dictionaryCount').textContent = `${fields.length} 个字段`;
    $('#dictionaryBody').innerHTML = fields.map((field) => `<tr><td>${escapeHtml(field.group)}</td><td>${escapeHtml(field.name)}</td><td>${escapeHtml(field.unit)}</td><td>${escapeHtml(field.period)}</td></tr>`).join('');
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

  function syncDashboardFilterSummary() {
    const activeCount = [$('#sourceSelect').value, $('#typeSelect').value].filter(Boolean).length;
    $('#dashboardExtraFiltersSummary').textContent = activeCount ? `更多筛选 · ${activeCount}` : '更多筛选';
  }

  async function refreshDashboard() {
    const params = new URLSearchParams();
    if (elements.batchSelect.value) params.set('batch_id', elements.batchSelect.value);
    if (elements.sourceSelect.value) params.set('source', elements.sourceSelect.value);
    if (elements.typeSelect.value) params.set('record_type', elements.typeSelect.value);
    const data = await requestJson(`/api/dashboard?${params.toString()}`);
    renderDashboard(data);
    syncDashboardFilterSummary();
  }

  async function refreshAnalytics() {
    const params = getNoteFilterParams();
    params.set('word_kind', $('#wordKindSelect').value);
    const data = await requestJson(`/api/analytics?${params.toString()}`);
    renderAnalytics(data);
  }

  function renderHistoryAnalysis(data) {
    const source = data.source || {};
    const metrics = data.metrics || {};
    const followers = metrics.followers || {};
    const reading = metrics.daily_read_median || {};
    const interaction = metrics.daily_interaction_median || {};
    $('#historyAnalysisStatus').textContent = `${source.label || '历史数据'} · ${source.sample_count || 0} 条样本`;
    $('#historySampleCount').textContent = formatMetric(source.sample_count || 0);
    $('#historySourceFiles').textContent = `来源文件 ${formatMetric(source.source_file_count || 0)} 个`;
    $('#historyFollowersAverage').textContent = followers.average === null || followers.average === undefined ? '暂无数据' : formatMetric(followers.average);
    $('#historyFollowersCount').textContent = `有效样本 ${formatMetric(followers.valid_count || 0)}`;
    $('#historyReadingMedian').textContent = reading.median === null || reading.median === undefined ? '暂无数据' : formatMetric(reading.median);
    $('#historyReadingCount').textContent = `有效样本 ${formatMetric(reading.valid_count || 0)}`;
    $('#historyInteractionMedian').textContent = interaction.median === null || interaction.median === undefined ? '暂无数据' : formatMetric(interaction.median);
    $('#historyInteractionCount').textContent = `有效样本 ${formatMetric(interaction.valid_count || 0)}`;
    $('#historyFollowerScope').textContent = `粉丝数，有效样本 ${followers.valid_count || 0}`;
    $('#historyPerformanceScope').textContent = `共同样本 ${data.performance_pairs?.length || 0} 条`;
    $('#historyRecordCount').textContent = `${data.records?.length || 0} 条记录`;

    const coverage = data.field_coverage || {};
    const coverageFields = coverage.fields || [];
    const coverageDistribution = coverage.distribution || [];
    $('#historyCoverageScope').textContent = `${coverage.record_count || 0} 条记录 · ${coverage.field_count || 0} 个字段 · 覆盖率按已取得原值统计`;
    $('#historyCoverageFieldCount').textContent = `${coverage.field_count || 0} 个字段`;
    if (coverageDistribution.some((item) => item.count > 0)) {
      charts.historyCoverageChart?.setOption({
        ...baseChart,
        grid: { left: 48, right: 16, top: 12, bottom: 38 },
        tooltip: { trigger: 'axis', formatter: (items) => `${escapeHtml(items[0]?.name || '')}<br>${formatMetric(items[0]?.value || 0)} 个字段` },
        xAxis: { type: 'category', data: coverageDistribution.map((item) => item.label), axisTick: { show: false }, axisLabel: { color: '#929c95', fontSize: 8, interval: 0 } },
        yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: Theme.splitLineColor } }, axisLabel: { color: '#a0a9a2', fontSize: 8 } },
        series: [{ type: 'bar', data: coverageDistribution.map((item) => item.count), barMaxWidth: 38, itemStyle: { color: Theme.seriesGreen, borderRadius: [4, 4, 0, 0] } }],
      }, true);
    } else setEmptyChart(charts.historyCoverageChart, '暂无字段记录');
    $('#historyCoverageBody').innerHTML = coverageFields.map((item) => {
      const unitPeriod = [...(item.units || []), ...(item.periods || [])].join(' / ') || item.unit || '—';
      const original = (item.original_fields || []).join(' / ') || '—';
      const parseSuccess = item.parse_success_pct === null || item.parse_success_pct === undefined ? '—' : `${formatMetric(item.parse_success_pct)}%`;
      return `<tr><td>${escapeHtml(item.label)}</td><td>${formatMetric(item.collected_count || 0)} / ${formatMetric(item.total_count || 0)}</td><td>${formatMetric(item.coverage_pct || 0)}%</td><td>${parseSuccess}</td><td>${formatMetric(item.numeric_success_count || 0)}</td><td>${formatMetric(item.text_count || 0)}</td><td>${formatMetric(item.starting_price_count || 0)}</td><td>${formatMetric(item.displayed_unavailable_count || 0)}</td><td>${formatMetric(item.empty_count || 0)}</td><td>${formatMetric(item.not_read_count || 0)}</td><td>${formatMetric(item.parse_failed_count || 0)}</td><td>${formatMetric(item.other_raw_count || 0)}</td><td><span class="coverage-pill coverage-tier-${escapeHtml(item.tier)}">${escapeHtml(item.tier_label)}</span></td><td>${escapeHtml(original)}</td><td>${escapeHtml(unitPeriod)}</td></tr>`;
    }).join('') || '<tr><td colspan="15" class="empty-cell">暂无字段证据</td></tr>';

    const bucketRows = data.follower_distribution || [];
    if (bucketRows.some((item) => item.count > 0)) {
      charts.historyFollowersChart?.setOption({
        ...baseChart,
        grid: { left: 48, right: 16, top: 12, bottom: 32 },
        tooltip: { trigger: 'axis', formatter: (items) => `${escapeHtml(items[0]?.name || '')}<br>${formatMetric(items[0]?.value || 0)} 条样本` },
        xAxis: { type: 'category', data: bucketRows.map((item) => item.label), axisTick: { show: false }, axisLabel: { color: '#929c95', fontSize: 8, interval: 0, rotate: 16 } },
        yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: Theme.splitLineColor } }, axisLabel: { color: '#a0a9a2', fontSize: 8 } },
        series: [{ type: 'bar', data: bucketRows.map((item) => item.count), barMaxWidth: 32, itemStyle: { color: Theme.seriesGreen, borderRadius: [4, 4, 0, 0] } }],
      }, true);
    } else setEmptyChart(charts.historyFollowersChart, '暂无可用粉丝数');

    const pairs = data.performance_pairs || [];
    if (pairs.length) {
      charts.historyPerformanceChart?.setOption({
        ...baseChart,
        grid: { left: 48, right: 14, top: 12, bottom: 37 },
        tooltip: { trigger: 'item', formatter: (item) => `${escapeHtml(item.data.name)}<br>日常阅读：${formatMetric(item.value[0])} 次<br>日常互动：${formatMetric(item.value[1])} 次` },
        xAxis: { type: 'value', name: '日常阅读中位数', nameTextStyle: { fontSize: 8 }, splitLine: { lineStyle: { color: Theme.splitLineColor } }, axisLabel: { color: '#a0a9a2', fontSize: 8 } },
        yAxis: { type: 'value', name: '日常互动中位数', nameTextStyle: { fontSize: 8 }, splitLine: { lineStyle: { color: Theme.splitLineColor } }, axisLabel: { color: '#a0a9a2', fontSize: 8 } },
        series: [{ type: 'scatter', symbolSize: 8, itemStyle: { color: Theme.seriesGreen, opacity: 0.7 }, data: pairs.map((item) => ({ name: item.sample, value: [item.reading, item.interaction] })) }],
      }, true);
    } else setEmptyChart(charts.historyPerformanceChart, '暂无共同有效样本');

    const labels = {
      followers: '粉丝数', daily_read_median: '日常阅读中位数', daily_interaction_median: '日常互动中位数',
      likes_and_favorites: '获赞与收藏', published_notes: '发布笔记数',
    };
    const units = { followers: '人', daily_read_median: '次', daily_interaction_median: '次', likes_and_favorites: '次', published_notes: '篇' };
    const scopes = { followers: '采集记录值', daily_read_median: '日常中位数', daily_interaction_median: '日常中位数', likes_and_favorites: '页面显示值', published_notes: '页面显示值' };
    $('#historyFieldBody').innerHTML = Object.entries(metrics).map(([key, item]) => `<tr><td>${escapeHtml(labels[key] || item.label || key)}</td><td>${formatMetric(item.valid_count || 0)} / ${formatMetric(item.total_count || 0)}</td><td>${escapeHtml(units[key] || item.unit || '')}</td><td>${escapeHtml(scopes[key] || '')}</td></tr>`).join('');
    const recordFields = ['followers', 'daily_read_median', 'daily_interaction_median'];
    $('#historyRecordBody').innerHTML = (data.records || []).map((row) => `<tr><td>${escapeHtml(row.sample)}</td>${recordFields.map((field) => `<td>${row[field] === null || row[field] === undefined ? '—' : formatMetric(row[field])}</td>`).join('')}</tr>`).join('') || '<tr><td colspan="4" class="empty-cell">暂无样本</td></tr>';
  }

  function renderHistoryNoteSamples(payload) {
    const samples = payload.samples || [];
    $('#historyNoteCount').textContent = `${formatMetric(payload.total_count || 0)} 篇`;
    $('#historyNoteStatus').textContent = `正文 ${formatMetric(payload.body_count || 0)} 篇 · 标题 ${formatMetric(payload.title_count || 0)} 篇 · 有话题 ${formatMetric(payload.topics_count || 0)} 篇`;
    $('#historyNoteSamples').innerHTML = samples.map((item) => `<article class="history-note-card"><h4>${escapeHtml(item.title || '（无标题）')}</h4>${item.author ? `<small>${escapeHtml(item.author)}</small>` : ''}<p class="history-note-body">${escapeHtml(item.body || '暂无已解析正文')}</p><div class="history-note-topics">${(item.topics || []).map((topic) => `<span>#${escapeHtml(topic)}</span>`).join('') || '<span>暂无已解析话题</span>'}</div><small>${escapeHtml(item.lineage?.source_name || '历史笔记')} · 第 ${formatMetric(item.lineage?.row_number || 0)} 行${item.lineage?.capture_time ? ` · ${escapeHtml(item.lineage.capture_time)}` : ''}</small></article>`).join('') || '<article class="history-note-card empty-cell">暂无匹配笔记内容</article>';
    const offset = Number(payload.offset || 0);
    const limit = Number(payload.limit || HISTORY_NOTE_PAGE_SIZE);
    const resultCount = Number(payload.result_count || 0);
    const first = resultCount ? offset + 1 : 0;
    const last = Math.min(offset + limit, resultCount);
    $('#historyNotePageStatus').textContent = `${formatMetric(first)}–${formatMetric(last)} / ${formatMetric(resultCount)}`;
    $('#historyNoteCoverage').textContent = `原始资料共 ${formatMetric(payload.total_count || 0)} 篇`;
    $('#historyNotePrev').disabled = offset <= 0;
    $('#historyNoteNext').disabled = offset + limit >= resultCount;
  }

  async function refreshHistoryNoteLibrary() {
    const params = new URLSearchParams({
      q: $('#historyNoteQuery').value.trim(),
      offset: String(historyNoteOffset),
      limit: String(HISTORY_NOTE_PAGE_SIZE),
    });
    renderHistoryNoteSamples(await requestJson(`/api/history-analysis/note-library?${params.toString()}`));
  }

  async function refreshHistoryAnalysis() {
    const version = ++historyRequestVersion;
    try {
      const sourcePayload = await requestJson('/api/history-analysis/sources');
      if (version !== historyRequestVersion) return;
      const select = $('#historySourceSelect');
      const sourceOptions = sourcePayload.sources || [];
      const selected = select.value === '__all__' ? '__all__' : (sourceOptions.some((item) => item.key === select.value) ? select.value : (sourceOptions.length ? '__all__' : ''));
      select.innerHTML = '<option value="__all__">全部数据集（分开展示）</option>' + sourceOptions.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)} · ${formatMetric(item.sample_count)} 条</option>`).join('');
      select.value = selected;
      const allView = historyAllView ||= window.CreatorScopeHistoryAll.create({ container: $('#historyAllGroups'), requestJson, onSelectSource: (key) => { select.value = key; refreshHistoryAnalysis(); } });
      if (selected === '__all__') {
        $('#historySingleSource').hidden = true;
        $('#historyAnalysisStatus').textContent = '全部历史数据集，按来源分开展示';
        await allView.show(sourceOptions);
      } else if (selected) {
        allView.hide();
        $('#historySingleSource').hidden = false;
        const params = new URLSearchParams({ source: selected });
        const data = await requestJson(`/api/history-analysis?${params.toString()}`);
        if (version !== historyRequestVersion) return;
        renderHistoryAnalysis(data);
        window.requestAnimationFrame(() => ['historyFollowersChart', 'historyPerformanceChart', 'historyCoverageChart'].forEach((id) => charts[id]?.resize()));
      } else {
        allView.hide();
        $('#historySingleSource').hidden = false;
        $('#historyAnalysisStatus').textContent = '暂无可用历史样本';
        setEmptyChart(charts.historyFollowersChart, '暂无可用样本');
        setEmptyChart(charts.historyPerformanceChart, '暂无可用样本');
        setEmptyChart(charts.historyCoverageChart, '暂无可用样本');
      }
      if (version === historyRequestVersion) await refreshHistoryNoteLibrary();
    } catch (error) {
      if (version !== historyRequestVersion) return;
      $('#historyAnalysisStatus').textContent = error.message;
      setEmptyChart(charts.historyFollowersChart, '历史数据暂不可用');
      setEmptyChart(charts.historyPerformanceChart, '历史数据暂不可用');
      setEmptyChart(charts.historyCoverageChart, '历史数据暂不可用');
      $('#historyFieldBody').innerHTML = '<tr><td colspan="4" class="empty-cell">暂无样本</td></tr>';
      $('#historyCoverageBody').innerHTML = '<tr><td colspan="15" class="empty-cell">暂无字段证据</td></tr>';
      $('#historyRecordBody').innerHTML = '<tr><td colspan="4" class="empty-cell">暂无样本</td></tr>';
      renderHistoryNoteSamples({ samples: [], total_count: 0, result_count: 0, body_count: 0, title_count: 0, topics_count: 0 });
    }
  }

  async function refreshDataViews() {
    await refreshDashboard();
    await Promise.all([refreshOverview(), refreshAnalytics(), refreshCreatorLibrary(), refreshCreatorOptions(), refreshFieldDictionary(), refreshNotes()]);
  }

  async function refreshTasks() {
    const tasks = await requestJson('/api/tasks');
    renderTasks(tasks);
    pendingTask = tasks.some((task) => ['queued', 'running', 'waiting_user', 'paused', 'stopping'].includes(task.status));
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
    if (demoMode) {
      browserIsOpen = false;
      elements.captureButton.disabled = true;
      elements.closeBrowserButton.disabled = true;
      elements.openBrowserButton.disabled = true;
      elements.browserHint.innerHTML = '<span class="status-dot muted"></span><span>离线演示不会打开浏览器或发起网络请求。</span>';
      return;
    }
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
      await Promise.all([refreshDataViews(), refreshTasks(), refreshBrowserStatus(), refreshHistoryAnalysis()]);
    } catch (error) {
      toast(`本地服务暂不可用：${error.message}`, true);
    }
  }

  function syncPrimaryNavigation() {
    const hash = window.location.hash;
    const activeSection = hash === '#creators' ? 'creators' : (hash === '#collection' || hash === '#records') ? 'collection' : 'analytics';
    document.querySelectorAll('[data-primary-section]').forEach((link) => {
      link.classList.toggle('active', link.dataset.primarySection === activeSection);
    });
    document.querySelectorAll('[data-primary-view]').forEach((section) => {
      section.hidden = section.dataset.primaryView !== activeSection;
    });
    window.requestAnimationFrame(() => Object.values(charts).forEach((chart) => chart.resize()));
  }

  function bindEvents() {
    $('#historySourceSelect').addEventListener('change', () => refreshHistoryAnalysis().catch((error) => toast(error.message, true)));
    $('#unifiedCreatorPrev').addEventListener('click', () => { unifiedCreatorOffset = Math.max(0, unifiedCreatorOffset - UNIFIED_CREATOR_PAGE_SIZE); refreshCreatorLibrary().catch((error) => toast(error.message, true)); });
    $('#unifiedCreatorNext').addEventListener('click', () => { unifiedCreatorOffset += UNIFIED_CREATOR_PAGE_SIZE; refreshCreatorLibrary().catch((error) => toast(error.message, true)); });
    $('#unifiedCreatorBody').addEventListener('change', (event) => {
      const checkbox = event.target.closest('[data-unified-compare]');
      if (!checkbox) return;
      const ref = checkbox.dataset.ref;
      if (checkbox.checked) {
        if (selectedUnifiedCreators.size >= 4) {
          checkbox.checked = false;
          toast('横向对比最多选择 4 位达人', true);
          return;
        }
        const name = checkbox.closest('tr')?.querySelector('strong')?.textContent || ref;
        selectedUnifiedCreators.set(ref, name);
      } else selectedUnifiedCreators.delete(ref);
      updateUnifiedCompareButton();
    });
    $('#compareSelectedCreators').addEventListener('click', () => {
      if (selectedUnifiedCreators.size < 2 || selectedUnifiedCreators.size > 4) return;
      const params = new URLSearchParams();
      selectedUnifiedCreators.forEach((_name, ref) => params.append('creator_ref', ref));
      window.location.href = `/ai?${params.toString()}`;
    });
    $('#filterHistoryNotes').addEventListener('click', () => { historyNoteOffset = 0; refreshHistoryNoteLibrary().catch((error) => toast(error.message, true)); });
    $('#historyNoteQuery').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { historyNoteOffset = 0; refreshHistoryNoteLibrary().catch((error) => toast(error.message, true)); }
    });
    $('#historyNotePrev').addEventListener('click', () => { historyNoteOffset = Math.max(0, historyNoteOffset - HISTORY_NOTE_PAGE_SIZE); refreshHistoryNoteLibrary().catch((error) => toast(error.message, true)); });
    $('#historyNoteNext').addEventListener('click', () => { historyNoteOffset += HISTORY_NOTE_PAGE_SIZE; refreshHistoryNoteLibrary().catch((error) => toast(error.message, true)); });
    elements.importButton.addEventListener('click', () => submitTask('/api/tasks/import-baseline', '已提交历史基线导入任务'));
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
      unifiedCreatorOffset = 0;
      Promise.all([refreshDashboard(), refreshAnalytics(), refreshNotes(), refreshCreatorLibrary()]).catch((error) => toast(error.message, true));
    });
    elements.sourceSelect.addEventListener('change', () => {
      unifiedCreatorOffset = 0;
      Promise.all([refreshDashboard(), refreshAnalytics(), refreshNotes(), refreshCreatorLibrary()]).catch((error) => toast(error.message, true));
    });
    elements.typeSelect.addEventListener('change', refreshDashboard);
    $('#filterCreators').addEventListener('click', () => { unifiedCreatorOffset = 0; refreshCreatorLibrary().catch((error) => toast(error.message, true)); });
    $('#creatorQuery').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { unifiedCreatorOffset = 0; refreshCreatorLibrary().catch((error) => toast(error.message, true)); }
    });
    $('#categoryFilter').addEventListener('change', () => {
      unifiedCreatorOffset = 0;
      Promise.all([refreshCreatorLibrary(), refreshAnalytics(), refreshNotes()]).catch((error) => toast(error.message, true));
    });
    ['#locationFilter', '#creatorBucketFilter'].forEach((selector) => $(selector).addEventListener('change', () => {
      unifiedCreatorOffset = 0;
      refreshCreatorLibrary().catch((error) => toast(error.message, true));
    }));
    ['#followersMin', '#followersMax'].forEach((selector) => $(selector).addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { unifiedCreatorOffset = 0; refreshCreatorLibrary().catch((error) => toast(error.message, true)); }
    }));
    $('#noteCompareSelect').addEventListener('change', () => {
      const selected = Array.from($('#noteCompareSelect').selectedOptions);
      if (selected.length > 4) { selected.at(-1).selected = false; $('#noteCompareStatus').textContent = '最多选择 4 篇笔记'; }
      else $('#noteCompareStatus').textContent = `${selected.length} 篇已选择；请选 2 至 4 篇`;
      lastNoteCompareRows = [];
      setEmptyChart(charts.noteCompareBarChart, '点击“对比所选笔记”查看');
      setEmptyChart(charts.noteCompareRadarChart, '点击“对比所选笔记”查看');
    });
    $('#compareNotes').addEventListener('click', async () => {
      if (!elements.batchSelect.value || elements.batchSelect.value === '__all__') {
        $('#noteCompareStatus').textContent = '请选择一个具体采集批次，以保证来源和时间口径一致';
        return;
      }
      const selected = Array.from($('#noteCompareSelect').selectedOptions);
      if (selected.length < 2 || selected.length > 4) {
        $('#noteCompareStatus').textContent = '请选择 2 至 4 篇笔记';
        return;
      }
      const params = new URLSearchParams({ batch_id: elements.batchSelect.value });
      selected.forEach((option) => params.append('note_id', option.value));
      if (elements.sourceSelect.value) params.set('source', elements.sourceSelect.value);
      try { renderNoteComparison(await requestJson(`/api/notes/compare?${params.toString()}`)); }
      catch (error) { $('#noteCompareStatus').textContent = error.message; }
    });
    $('#noteCompareMetric').addEventListener('change', () => {
      const option = $('#noteCompareMetric').selectedOptions[0];
      const rows = lastNoteCompareRows.map((note) => ({ ...note, compare_label: `${note.creator_name} · ${note.title}` }));
      renderComparisonBar('noteCompareBarChart', rows, $('#noteCompareMetric').value, 'compare_label', option?.textContent || '指标');
    });
    const refreshNoteAnalytics = () => Promise.all([refreshNotes(), refreshAnalytics()]).catch((error) => toast(error.message, true));
    $('#filterNotes').addEventListener('click', refreshNoteAnalytics);
    ['#noteCreatorFilter', '#noteIdFilter', '#noteTypeFilter', '#noteTopicFilter'].forEach((selector) => $(selector).addEventListener('change', refreshNoteAnalytics));
    ['#noteDateFrom', '#noteDateTo'].forEach((selector) => $(selector).addEventListener('change', refreshNoteAnalytics));
    $('#noteKeywordFilter').addEventListener('input', () => {
      window.clearTimeout($('#noteKeywordFilter').dataset.timer);
      $('#noteKeywordFilter').dataset.timer = window.setTimeout(refreshNoteAnalytics, 250);
    });
    $('#wordKindSelect').addEventListener('change', () => refreshAnalytics().catch((error) => toast(error.message, true)));
    $('#noteMetricSelect').addEventListener('change', () => {
      if (lastNotesData) renderNoteMetricDistribution(lastNotesData.charts || {}, $('#noteMetricSelect').value);
    });
    const today = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }).format(new Date());
    $('#todayLabel').textContent = today;
  }

  document.addEventListener('DOMContentLoaded', () => {
    syncPrimaryNavigation();
    window.addEventListener('hashchange', syncPrimaryNavigation);
    initCharts();
    bindEvents();
    refreshAll();
    window.setInterval(async () => {
      try { await Promise.all([refreshTasks(), refreshBrowserStatus()]); }
      catch (_) { /* Keep the dashboard usable during a local restart. */ }
    }, 2000);
  });
})();
