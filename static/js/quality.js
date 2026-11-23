(() => {
  const $ = (selector) => document.querySelector(selector);
  const charts = {};
  const TYPE_LABELS = {
    blogger_list_sample: '博主列表',
    creator_profile_sample: '博主详情',
    creator_content_performance: '内容表现',
    fan_analysis: '粉丝分析',
    note_detail: '笔记详情面板',
    xhs_public_note: '小红书公开笔记',
    comment_snapshot: '评论快照',
  };
  const typeLabel = (key) => TYPE_LABELS[key] || key || '—';
  const STATE_LABELS = {
    captured: '已捕获',
    not_displayed: '页面未展示',
    not_loaded: '未加载',
    access_denied: '访问受限',
    parse_failed: '解析失败',
    not_attempted: '未尝试',
    not_applicable: '不适用',
  };
  const stateLabel = (key) => STATE_LABELS[key] || key;
  const GRADE_LABELS = { stable: '小样本稳定', partial: '部分可用', unstable: '不稳定', incomplete: '测试不完整', not_verified: '未验证' };
  const STATUS_LABELS = {
    completed: '已完成',
    running: '运行中',
    queued: '排队中',
    paused: '已暂停',
    waiting_user: '等待处理',
    stopping: '正在停止',
    stopped: '已停止',
    failed: '失败',
    error: '失败',
  };
  const statusLabel = (key) => STATUS_LABELS[key] || key || '—';
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const number = (value) => (value === null || value === undefined ? '—' : Number(value).toLocaleString('zh-CN'));
  const percent = (value) => (value === null || value === undefined ? '—' : `${Number(value).toFixed(1)}%`);
  const seconds = (value) => {
    if (value === null || value === undefined) return '—';
    const total = Number(value);
    if (total < 60) return `${total.toFixed(1)} 秒`;
    return `${Math.floor(total / 60)} 分 ${Math.round(total % 60)} 秒`;
  };
  const emptyChart = (chart, message) => {
    chart?.clear();
    chart?.setOption({ graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: message, fill: '#a5ada7', fontSize: 11 } }] });
  };

  const state = { batches: [], selected: null };

  async function requestJson(url) {
    const response = await fetch(url, { headers: { Accept: 'application/json' } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
    return data;
  }

  function coverageClass(value) {
    if (value === null || value === undefined) return 'muted';
    if (value >= 90) return 'good';
    if (value >= 60) return 'warn';
    return 'bad';
  }

  function renderBatches() {
    const rows = state.batches;
    $('#batchCount').textContent = `${rows.length} 个批次`;
    if (!rows.length) {
      $('#batchRows').innerHTML = '<tr><td colspan="12" class="empty-cell">本机还没有采集批次；先完成一次采集或导入后回来看质量</td></tr>';
      return;
    }
    $('#batchRows').innerHTML = rows.map((item) => {
      const active = state.selected === item.batch_id ? ' class="is-selected"' : '';
      return `<tr data-batch="${escapeHtml(item.batch_id)}"${active}>
        <td><button type="button" class="quality-link">${escapeHtml(item.batch_id)}</button></td>
        <td>${escapeHtml(item.source || '—')}</td>
        <td><span class="quality-status ${escapeHtml(item.status || '')}">${escapeHtml(statusLabel(item.status))}</span></td>
        <td>${escapeHtml((item.completed_at || item.started_at || '—').replace('T', ' '))}</td>
        <td>${seconds(item.elapsed_seconds)}</td>
        <td>${number(item.counts.records)}</td>
        <td>${number(item.counts.creators)}</td>
        <td>${number(item.counts.notes)}</td>
        <td>${number(item.counts.comments)}</td>
        <td><span class="quality-pill ${coverageClass(item.coverage_pct)}">${percent(item.coverage_pct)}</span></td>
        <td>${percent(item.parse_success_pct)}</td>
        <td>${number(item.partial_fields)} / ${number(item.field_count)}</td>
      </tr>`;
    }).join('');
  }

  function renderCoverageChart(report) {
    const items = (report.field_coverage || []).filter((item) => item.coverage_pct !== null).slice(0, 15);
    const chart = charts.coverageChart;
    if (!chart) return;
    if (!items.length) {
      emptyChart(chart, '本批次没有可计算的字段覆盖率');
      return;
    }
    chart.setOption({
      color: ['#438f6c'],
      grid: { left: 190, right: 42, top: 12, bottom: 30 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const item = items[params[0].dataIndex];
          return `${typeLabel(item.record_type)} · ${item.field_name}<br/>覆盖率 ${percent(item.coverage_pct)}<br/>已捕获 ${number(item.captured_records)} / 应有 ${number(item.expected_records)}`;
        },
      },
      xAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%', fontSize: 8, color: '#89948d' }, splitLine: { lineStyle: { color: '#f0f2f0' } } },
      yAxis: {
        type: 'category',
        data: items.map((item) => `${typeLabel(item.record_type)} · ${item.field_name}`),
        axisLabel: { fontSize: 8, width: 180, overflow: 'truncate', color: '#6c756f' },
      },
      series: [{
        type: 'bar',
        data: items.map((item) => item.coverage_pct),
        barWidth: 11,
        label: { show: true, position: 'right', formatter: '{c}%', fontSize: 8, color: '#7f8982' },
        itemStyle: {
          color: (params) => (params.value >= 90 ? '#438f6c' : params.value >= 60 ? '#d19a64' : '#c8796d'),
          borderRadius: [0, 4, 4, 0],
        },
      }],
    }, true);
  }

  function renderDetail(report) {
    $('#detailSection').hidden = false;
    $('#detailTitle').textContent = `批次 ${report.batch_id}`;
    $('#detailScope').textContent = `来源 ${report.source || '—'} · 状态 ${statusLabel(report.status)} · 耗时 ${seconds(report.elapsed_seconds)} · 记录 ${number(report.counts?.records)} · 评论 ${number(report.counts?.comments)}`;
    const link = $('#rawQualityLink');
    link.href = `/api/batches/${encodeURIComponent(report.batch_id)}/quality`;
    $('#batchReportLink').href = `/report?batch_id=${encodeURIComponent(report.batch_id)}`;

    const fields = report.field_coverage || [];
    $('#fieldCount').textContent = `${fields.length} 个字段项`;
    $('#fieldRows').innerHTML = fields.length ? fields.map((item) => {
      const states = Object.entries(item.states || {}).map(([key, value]) => `<span class="state-chip ${escapeHtml(key)}">${escapeHtml(stateLabel(key))} ${value}</span>`).join(' ');
      return `<tr>
        <td>${escapeHtml(typeLabel(item.record_type))}</td>
        <td>${escapeHtml(item.field_name)}</td>
        <td>${number(item.expected_records)}</td>
        <td>${number(item.captured_records)}</td>
        <td><span class="quality-pill ${coverageClass(item.coverage_pct)}">${percent(item.coverage_pct)}</span></td>
        <td>${escapeHtml(GRADE_LABELS[item.five_creator_grade] || '—')}</td>
        <td class="quality-states">${states || '—'}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="7" class="empty-cell">本批次没有字段观察记录</td></tr>';

    const failures = report.failure_records || [];
    $('#failureCount').textContent = `${failures.length} 条`;
    $('#failureRows').innerHTML = failures.length ? failures.map((item) => `<tr>
      <td>${number(item.creator_index)}</td>
      <td>${number(item.note_index)}</td>
      <td>${escapeHtml(item.reason || item.message || JSON.stringify(item))}</td>
    </tr>`).join('') : '<tr><td colspan="3" class="empty-cell">没有记录到失败项</td></tr>';

    renderCoverageChart(report);
  }

  async function loadOverview() {
    const limit = $('#limitSelect').value;
    const data = await requestJson(`/api/quality/overview?limit=${encodeURIComponent(limit)}`);
    state.batches = data.batches || [];
    const totals = data.totals || {};
    $('#generatedLabel').textContent = (data.generated_at || '').replace('T', ' ').slice(0, 19);
    $('#totalBatches').textContent = number(totals.batches);
    $('#totalRecords').textContent = number(totals.records);
    $('#totalCoverage').textContent = percent(totals.coverage_pct);
    $('#partialFields').textContent = number(totals.partial_fields);
    $('#qualityNotes').innerHTML = (data.notes || []).map((note) => `<li>${escapeHtml(note)}</li>`).join('');
    renderBatches();
    if (state.batches.length && !state.selected) {
      await selectBatch(state.batches[0].batch_id);
    }
  }

  async function selectBatch(batchId) {
    state.selected = batchId;
    renderBatches();
    try {
      const report = await requestJson(`/api/batches/${encodeURIComponent(batchId)}/quality`);
      renderDetail(report);
    } catch (error) {
      $('#detailSection').hidden = false;
      $('#detailTitle').textContent = '批次明细';
      $('#detailScope').textContent = error.message;
      $('#fieldRows').innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  function bindEvents() {
    $('#batchRows').addEventListener('click', (event) => {
      const row = event.target.closest('tr[data-batch]');
      if (!row) return;
      selectBatch(row.dataset.batch);
    });
    $('#limitSelect').addEventListener('change', () => {
      state.selected = null;
      loadOverview().catch((error) => {
        $('#batchRows').innerHTML = `<tr><td colspan="12" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
      });
    });
  }

  function initCharts() {
    const chartDom = document.getElementById('coverageChart');
    if (chartDom && window.echarts) charts.coverageChart = window.echarts.init(chartDom);
  }

  window.addEventListener('resize', () => Object.values(charts).forEach((chart) => chart?.resize()));

  document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    bindEvents();
    loadOverview().catch((error) => {
      $('#batchRows').innerHTML = `<tr><td colspan="12" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
      $('#batchCount').textContent = '读取失败';
    });
  });
})();
