(() => {
  const $ = (selector) => document.querySelector(selector);
  const Theme = window.CSTheme || {
    chartColors: ['#438f6c', '#d19a64', '#7596bd', '#c8796d'], splitLineColor: '#f0f2f0', emptyColor: '#a5ada7',
  };
  const charts = {};
  const colors = Theme.chartColors.slice(0, 4);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const emptyChart = (chart, message) => {
    chart?.clear();
    chart?.setOption({ graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: message, fill: Theme.emptyColor, fontSize: 11 } }] });
  };

  async function requestJson(url, options = {}) {
    const response = await fetch(url, { headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
    return data;
  }

  function renderItems(items) {
    $('#newsItemCount').textContent = `${items.length} 条新闻`;
    $('#newsItems').innerHTML = items.length ? items.map((item) => `<tr><td>${escapeHtml(item.source_name)}<br><small>${escapeHtml(item.source_key)}</small></td><td class="news-title-cell"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.summary || 'RSS 未提供摘要')}</small></td><td>${escapeHtml((item.topic_tags || []).join('、') || '未提取到关键词')}</td><td>${escapeHtml(item.published_at || '—')}<br><small>${escapeHtml(item.published_at_method || 'missing')}</small></td><td>${escapeHtml(item.collected_at || '—')}</td><td><a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener noreferrer">打开原文</a></td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">暂无已采集新闻</td></tr>';
  }

  function renderCorrelation(correlation) {
    const events = correlation.events || [];
    const count = events.reduce((sum, item) => sum + (item.matched_note_count || 0), 0);
    $('#correlationCount').textContent = `${events.length} 个新闻事件 · ${count} 次匹配`;
    $('#methodNotice').textContent = correlation.method_notice || '选择笔记批次后计算词项和时间窗口关联。';
    const tableRows = [];
    events.forEach((event) => {
      if (!event.by_source_and_period?.length) {
        tableRows.push(`<tr><td><a href="${escapeHtml(event.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(event.title)}</a><br><small>${escapeHtml(event.published_at || '—')}</small></td><td>${escapeHtml((event.topic_tags || []).join('、') || '—')}</td><td colspan="7">匹配笔记 ${event.matched_note_count || 0} 篇；当前没有兼容来源/周期的指标组</td></tr>`);
        return;
      }
      event.by_source_and_period.forEach((group) => tableRows.push(`<tr><td><a href="${escapeHtml(event.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(event.title)}</a><br><small>${escapeHtml(event.published_at || '—')}</small></td><td>${escapeHtml((event.topic_tags || []).join('、') || '—')}</td><td>${escapeHtml(group.source)}<br><small>${escapeHtml(group.metric_period || '互动周期不可比')}</small></td><td>${group.before_count ?? 0}</td><td>${group.event_day_count ?? 0}</td><td>${group.after_count ?? 0}</td><td>${group.before_mean_interactions === null ? '—' : Number(group.before_mean_interactions).toLocaleString('zh-CN')}</td><td>${group.after_mean_interactions === null ? '—' : Number(group.after_mean_interactions).toLocaleString('zh-CN')}</td><td>前 ${group.before_interaction_sample_count || 0} · 后 ${group.after_interaction_sample_count || 0}</td></tr>`));
    });
    $('#correlationRows').innerHTML = tableRows.length ? tableRows.join('') : '<tr><td colspan="9" class="empty-cell">暂无可分析的新闻；先采集 RSS，并选择一个笔记批次</td></tr>';

    const countRows = [];
    events.forEach((event) => {
      const sources = [...new Set((event.matched_notes || []).map((note) => note.source || '未知来源'))];
      sources.forEach((source) => {
        const notes = (event.matched_notes || []).filter((note) => (note.source || '未知来源') === source);
        const uniqueCount = (relative) => new Set(notes.filter((note) => note.relative_window === relative).map((note) => note.note_id)).size;
        countRows.push({ label: `${event.title} · ${source}`, before: uniqueCount('热点前'), on: uniqueCount('热点当天'), after: uniqueCount('热点后') });
      });
    });
    if (countRows.length) {
      const rows = countRows.slice(0, 16);
      charts.newsPostCountChart?.setOption({
        color: colors, grid: { left: 150, right: 18, top: 10, bottom: 38 },
        legend: { bottom: 0, textStyle: { fontSize: 8 } },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: Theme.splitLineColor } } },
        yAxis: { type: 'category', data: rows.map((item) => item.label), axisLabel: { fontSize: 7, width: 140, overflow: 'truncate' } },
        series: [
          { name: '热点前', type: 'bar', data: rows.map((item) => item.before) },
          { name: '当天', type: 'bar', data: rows.map((item) => item.on) },
          { name: '热点后', type: 'bar', data: rows.map((item) => item.after) },
        ],
      }, true);
    } else emptyChart(charts.newsPostCountChart, '没有匹配到标题或话题词项的笔记');

    const interactionRows = events.flatMap((event) => (event.by_source_and_period || []).map((group) => ({
      label: `${event.title} · ${group.source} · ${group.metric_period || '周期缺失'}`,
      before: group.before_mean_interactions,
      after: group.after_mean_interactions,
    })).filter((group) => group.before !== null || group.after !== null));
    if (interactionRows.length) {
      const rows = interactionRows.slice(0, 16);
      charts.newsInteractionChart?.setOption({
        color: [colors[0], colors[2]], grid: { left: 150, right: 18, top: 10, bottom: 38 },
        legend: { bottom: 0, textStyle: { fontSize: 8 } }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'value', splitLine: { lineStyle: { color: Theme.splitLineColor } } },
        yAxis: { type: 'category', data: rows.map((item) => item.label), axisLabel: { fontSize: 7, width: 140, overflow: 'truncate' } },
        series: [
          { name: '热点前平均互动数', type: 'bar', data: rows.map((item) => item.before) },
          { name: '热点后平均互动数', type: 'bar', data: rows.map((item) => item.after) },
        ],
      }, true);
    } else emptyChart(charts.newsInteractionChart, '缺少同来源、同周期的四项精确互动值');
  }

  async function refreshData() {
    $('#newsStatus').textContent = '正在读取本地新闻并计算匹配…';
    const params = new URLSearchParams();
    if ($('#newsBatch').value) params.set('batch_id', $('#newsBatch').value);
    const data = await requestJson(`/api/news?${params.toString()}`);
    renderItems(data.items || []);
    renderCorrelation(data.correlation || {});
    const methods = data.date_coverage?.methods || {};
    const dateInfo = `发布日期范围 ${data.date_coverage?.earliest || '无'} 至 ${data.date_coverage?.latest || '无'}；RSS 日期 ${methods.rss_field || 0}、URL 日期段 ${methods.source_url_path_date || 0}、缺失 ${methods.missing || 0}`;
    $('#newsStatus').textContent = `${data.batch_id ? `关联批次 ${data.batch_id}；` : ''}本地 ${data.count} 条新闻；${dateInfo}`;
  }

  async function initialize() {
    const data = await requestJson('/api/news/options');
    $('#newsSource').innerHTML = (data.sources || []).map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join('');
    $('#newsBatch').innerHTML = '<option value="">选择批次以分析关联</option>' + (data.batches || []).map((item) => `<option value="${escapeHtml(item.batch_id)}">${escapeHtml(item.source)} · ${escapeHtml(item.captured_at || item.batch_id)}</option>`).join('');
    await refreshData();
  }

  document.addEventListener('DOMContentLoaded', async () => {
    if (window.echarts) {
      charts.newsPostCountChart = echarts.init($('#newsPostCountChart'));
      charts.newsInteractionChart = echarts.init($('#newsInteractionChart'));
      window.addEventListener('resize', () => Object.values(charts).forEach((chart) => chart.resize()));
    }
    try { await initialize(); }
    catch (error) { $('#newsStatus').textContent = error.message; }
    $('#refreshNews').addEventListener('click', () => refreshData().catch((error) => { $('#newsStatus').textContent = error.message; }));
    $('#newsBatch').addEventListener('change', () => refreshData().catch((error) => { $('#newsStatus').textContent = error.message; }));
    $('#collectNews').addEventListener('click', async () => {
      const button = $('#collectNews');
      button.disabled = true;
      $('#newsStatus').textContent = '正在请求官方 RSS…';
      try {
        const data = await requestJson('/api/news/collect', { method: 'POST', body: JSON.stringify({ source_key: $('#newsSource').value }) });
        $('#newsStatus').textContent = `RSS 返回 ${data.received} 条；新增 ${data.added} 条；本地共 ${data.total} 条`;
        await refreshData();
      } catch (error) { $('#newsStatus').textContent = error.message; }
      finally { button.disabled = false; }
    });
  });
})();
