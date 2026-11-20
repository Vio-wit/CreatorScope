(() => {
  const $ = (selector) => document.querySelector(selector);
  const charts = {};
  const ids = ['topWordsChart', 'noteCountChart', 'lengthChart', 'likesChart', 'noteWordsChart', 'lengthLikesChart'];
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

  async function requestJson(url) {
    const response = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || `HTTP ${response.status}`);
    }
    return response.json();
  }

  function filters() {
    const params = new URLSearchParams();
    const values = {
      creator_id: $('#creatorFilter').value,
      note_id: $('#noteFilter').value,
      batch_id: $('#batchFilter').value,
      comment_level: $('#levelFilter').value,
      is_pinned: $('#pinnedFilter').value,
      q: $('#keywordFilter').value.trim(),
    };
    Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
    return params;
  }

  function fillSelect(selector, rows, valueKey, labelFn, firstLabel) {
    const select = $(selector);
    const previous = select.value;
    select.innerHTML = `<option value="">${escapeHtml(firstLabel)}</option>` + rows.map((row) => `<option value="${escapeHtml(row[valueKey])}">${escapeHtml(labelFn(row))}</option>`).join('');
    if (rows.some((row) => String(row[valueKey]) === previous)) select.value = previous;
  }

  async function loadOptions() {
    const data = await requestJson('/api/comments/options');
    fillSelect('#creatorFilter', data.creators || [], 'creator_id', (row) => row.creator_name, '全部创作者');
    fillSelect('#noteFilter', data.notes || [], 'note_id', (row) => `${row.creator_name} · ${row.title}`, '全部笔记');
    fillSelect('#batchFilter', data.batches || [], 'batch_id', (row) => `${row.source} · ${row.started_at || row.batch_id}`, '全部批次');
  }

  function chart(id) {
    if (!charts[id]) charts[id] = echarts.init(document.getElementById(id));
    return charts[id];
  }

  function baseOption() {
    return {
      animationDuration: 250,
      textStyle: { fontFamily: 'Microsoft YaHei, SimHei, sans-serif', color: '#536158' },
      tooltip: { trigger: 'axis', confine: true },
      grid: { left: 58, right: 26, top: 26, bottom: 72, containLabel: true },
    };
  }

  function setBar(id, labels, values, horizontal = false, color = '#5e9275') {
    const option = baseOption();
    if (horizontal) {
      option.grid.left = 36;
      option.grid.bottom = 35;
      option.xAxis = { type: 'value', minInterval: 1 };
      option.yAxis = { type: 'category', data: labels, axisLabel: { width: 260, overflow: 'truncate' } };
      option.series = [{ type: 'bar', data: values, itemStyle: { color, borderRadius: [0, 5, 5, 0] }, barMaxWidth: 24 }];
    } else {
      option.xAxis = { type: 'category', data: labels, axisLabel: { rotate: labels.length > 5 ? 18 : 0, interval: 0, width: 170, overflow: 'truncate' } };
      option.yAxis = { type: 'value', minInterval: 1 };
      option.series = [{ type: 'bar', data: values, itemStyle: { color, borderRadius: [5, 5, 0, 0] }, barMaxWidth: 42 }];
    }
    chart(id).setOption(option, true);
  }

  function renderNoteWords(items) {
    const notes = [...new Set(items.map((item) => item.note_id))];
    const noteLabels = Object.fromEntries(items.map((item) => [item.note_id, item.note_label]));
    const words = [...new Set(items.map((item) => item.word))].slice(0, 18);
    const option = baseOption();
    option.grid = { left: 58, right: 32, top: 28, bottom: 96, containLabel: true };
    option.xAxis = { type: 'category', data: words, axisLabel: { rotate: 30, interval: 0 } };
    option.yAxis = { type: 'value', minInterval: 1 };
    option.legend = { bottom: 8, type: 'scroll' };
    option.series = notes.map((noteId) => ({
      name: noteLabels[noteId] || noteId,
      type: 'bar',
      data: words.map((word) => items.find((item) => item.note_id === noteId && item.word === word)?.count || 0),
      barMaxWidth: 28,
    }));
    chart('noteWordsChart').setOption(option, true);
  }

  function renderScatter(points) {
    const option = baseOption();
    option.xAxis = { type: 'value', name: '评论字符数', nameLocation: 'middle', nameGap: 34 };
    option.yAxis = { type: 'value', name: '精确点赞数', minInterval: 1 };
    option.series = [{ type: 'scatter', symbolSize: 11, data: points.map((point) => [point.length, point.likes, point.note_title]), itemStyle: { color: '#c77668', opacity: 0.8 }, tooltip: { formatter: (params) => `${escapeHtml(params.value[2])}<br>长度：${params.value[0]}<br>点赞：${params.value[1]}` } }];
    chart('lengthLikesChart').setOption(option, true);
  }

  function renderRows(rows) {
    const body = $('#commentRows');
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="9" class="empty-cell">当前筛选下没有评论记录</td></tr>';
      return;
    }
    body.innerHTML = rows.map((row) => `<tr>
      <td>${escapeHtml(row.creator_name)}</td>
      <td class="note-title-cell">${escapeHtml(row.note_title)}<br><small>${escapeHtml(row.note_id)}</small></td>
      <td class="comment-content-cell">${escapeHtml(row.comment_text)}</td>
      <td>${escapeHtml(row.displayed_time || '—')}</td>
      <td>${escapeHtml(row.like_count_raw ?? '—')}${row.like_count_normalized === null ? '' : ` <small>(${escapeHtml(row.like_count_normalized)})</small>`}</td>
      <td>${escapeHtml(row.reply_count_raw ?? '—')}</td>
      <td>${row.comment_level === 1 ? '一级' : '回复'}</td>
      <td>${row.is_pinned === true ? '是' : row.is_pinned === false ? '否' : '未提供'}</td>
      <td class="batch-cell">${escapeHtml(row.batch_id)}</td>
    </tr>`).join('');
  }

  function updateExportLinks(params) {
    const suffix = params.toString();
    $('#exportCsv').href = `/api/comments/export.csv${suffix ? `?${suffix}` : ''}`;
    $('#exportJsonl').href = `/api/comments/export.jsonl${suffix ? `?${suffix}` : ''}`;
  }

  async function loadAnalysis() {
    const params = filters();
    updateExportLinks(params);
    const query = params.toString();
    const data = await requestJson(`/api/comments/analysis${query ? `?${query}` : ''}`);
    const summary = data.summary;
    $('#commentCount').textContent = summary.comment_count;
    $('#levelCounts').textContent = `${summary.root_count} / ${summary.reply_count}`;
    $('#noteCount').textContent = summary.note_count;
    $('#creatorCount').textContent = `${summary.creator_count} 位创作者`;
    $('#averageLength').textContent = summary.average_length === null ? '—' : `${summary.average_length}`;
    $('#likeCoverage').textContent = `${summary.like_count_available} / ${summary.comment_count}`;
    $('#sampleNotice').textContent = `${summary.scope_notice} · 当前筛选 ${summary.comment_count} 条`;
    $('#resultCount').textContent = `当前显示 ${data.comments.length} 条 · 不代表评论区总量`;
    $('#likesScope').textContent = `精确数值 n=${summary.like_count_available}；${summary.unavailable_like_values} 条缺失/非精确值未当作 0`;
    $('#scatterScope').textContent = `有效散点 n=${data.scatter.length}；仅描述本批样本，不代表因果关系`;
    $('#analysisStatus').textContent = summary.comment_count < 30 ? `${summary.scope_notice}。词频、点赞和长度关系均不作总体推断。` : '按当前筛选结果更新；描述统计不作总体推断。';
    $('#commentWordcloud').src = `/api/comments/wordcloud.png${query ? `?${query}&` : '?'}v=${Date.now()}`;

    const top = data.top_words || [];
    setBar('topWordsChart', top.map((item) => item.word).reverse(), top.map((item) => item.count).reverse(), true);
    const notes = data.by_note || [];
    setBar('noteCountChart', notes.map((item) => item.label), notes.map((item) => item.count), true, '#789c83');
    setBar('lengthChart', data.length_histogram.map((item) => item.label), data.length_histogram.map((item) => item.count), false, '#799cbb');
    const likes = data.like_histogram || [];
    setBar('likesChart', likes.map((item) => item.label), likes.map((item) => item.count), false, '#dfa05c');
    renderNoteWords(data.note_words || []);
    renderScatter(data.scatter || []);
    renderRows(data.comments || []);
  }

  async function refresh() {
    try {
      await loadOptions();
      await loadAnalysis();
    } catch (error) {
      $('#analysisStatus').textContent = `评论分析服务暂不可用：${error.message}`;
      $('#commentRows').innerHTML = '<tr><td colspan="9" class="empty-cell">读取失败，请检查验证数据库是否已导入</td></tr>';
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    ids.forEach((id) => chart(id));
    $('#commentFilterForm').addEventListener('submit', (event) => { event.preventDefault(); loadAnalysis().catch((error) => { $('#analysisStatus').textContent = error.message; }); });
    ['#creatorFilter', '#noteFilter', '#batchFilter', '#levelFilter', '#pinnedFilter'].forEach((selector) => $(selector).addEventListener('change', () => loadAnalysis().catch((error) => { $('#analysisStatus').textContent = error.message; })));
    $('#clearFilters').addEventListener('click', () => {
      $('#creatorFilter').value = '';
      $('#noteFilter').value = '';
      $('#batchFilter').value = '';
      $('#levelFilter').value = '';
      $('#pinnedFilter').value = '';
      $('#keywordFilter').value = '';
      loadAnalysis().catch((error) => { $('#analysisStatus').textContent = error.message; });
    });
    window.addEventListener('resize', () => Object.values(charts).forEach((instance) => instance.resize()));
    refresh();
  });
})();
