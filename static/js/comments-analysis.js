(() => {
  const $ = (selector) => document.querySelector(selector);
  const charts = {};
  const ids = ['topWordsChart', 'noteCountChart', 'creatorCommentChart', 'lengthChart', 'lengthBoxplotChart', 'likesChart', 'replyCountChart', 'noteWordsChart', 'lengthLikesChart', 'wordChangeChart', 'sentimentChart', 'tfidfChart', 'topicChart', 'noteTopicChart', 'creatorTopicChart'];
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
    const batches = data.batches || [];
    fillSelect('#batchFilter', batches, 'batch_id', (row) => `${row.source} · ${row.started_at || row.batch_id}`, '全部批次');
    fillSelect('#wordChangeBaseline', batches, 'batch_id', (row) => `${row.source} · ${row.started_at || row.batch_id}`, '选择基线批次');
    fillSelect('#wordChangeCurrent', batches, 'batch_id', (row) => `${row.source} · ${row.started_at || row.batch_id}`, '选择对比批次');
    if (batches.length >= 2) {
      if (!$('#wordChangeBaseline').value) $('#wordChangeBaseline').value = batches[1].batch_id;
      if (!$('#wordChangeCurrent').value) $('#wordChangeCurrent').value = batches[0].batch_id;
    }
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

  function renderLengthBoxplot(boxplot) {
    const option = baseOption();
    option.grid = { left: 58, right: 26, top: 32, bottom: 55, containLabel: true };
    option.xAxis = { type: 'category', data: ['原始评论长度'] };
    option.yAxis = { type: 'value', name: '字符数', minInterval: 1 };
    if (!boxplot?.values?.length) {
      option.graphic = [{ type: 'text', left: 'center', top: 'middle', style: { text: '当前筛选没有评论', fill: '#9aa39d', fontSize: 13 } }];
      option.series = [];
    } else {
      option.tooltip = { trigger: 'item', formatter: () => `最小值：${boxplot.values[0]}<br>Q1：${boxplot.values[1]}<br>中位数：${boxplot.values[2]}<br>Q3：${boxplot.values[3]}<br>最大值：${boxplot.values[4]}<br>有效样本 n=${boxplot.sample_count}` };
      option.series = [{ type: 'boxplot', data: [boxplot.values], itemStyle: { color: '#dce9df', borderColor: '#5e9275' } }];
    }
    chart('lengthBoxplotChart').setOption(option, true);
  }

  function renderGroupTopicComparison(id, groups) {
    const populated = (groups || []).filter((group) => group.topics?.length);
    if (!populated.length) {
      chart(id).clear();
      chart(id).setOption({ graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '当前样本没有可比较主题', fill: '#9aa39d', fontSize: 13 } }] });
      return;
    }
    const totalByTopic = new Map();
    populated.forEach((group) => group.topics.forEach((item) => totalByTopic.set(item.label, (totalByTopic.get(item.label) || 0) + item.count)));
    const labels = [...totalByTopic.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 8).map(([label]) => label);
    const option = baseOption();
    option.grid = { left: 105, right: 26, top: 18, bottom: 50, containLabel: true };
    option.legend = { bottom: 4, type: 'scroll' };
    option.xAxis = { type: 'category', data: labels, axisLabel: { rotate: 18, interval: 0 } };
    option.yAxis = { type: 'value', minInterval: 1 };
    option.series = populated.map((group) => {
      const values = new Map(group.topics.map((item) => [item.label, item.count]));
      return { name: `${group.label} (n=${group.sample_count})`, type: 'bar', barMaxWidth: 24, data: labels.map((label) => values.get(label) || 0) };
    });
    chart(id).setOption(option, true);
  }

  function renderTfidf(items) {
    const populated = (items || []).slice(0, 12);
    const option = baseOption();
    option.grid = { left: 36, right: 34, top: 18, bottom: 35, containLabel: true };
    option.xAxis = { type: 'value', min: 0, axisLabel: { formatter: (value) => Number(value).toFixed(2) } };
    option.yAxis = { type: 'category', data: populated.map((item) => item.word).reverse(), axisLabel: { width: 220, overflow: 'truncate' } };
    if (!populated.length) {
      option.graphic = [{ type: 'text', left: 'center', top: 'middle', style: { text: '当前筛选没有可计算的关键词', fill: '#9aa39d', fontSize: 13 } }];
      option.series = [];
    } else {
      const values = populated.map((item) => ({ value: item.score, documentFrequency: item.document_frequency, documentCount: item.document_count })).reverse();
      option.tooltip = { trigger: 'axis', formatter: (params) => {
        const item = params[0];
        return `${escapeHtml(item.name)}<br>TF-IDF 汇总分：${Number(item.value).toFixed(6)}<br>出现评论数：${item.data.documentFrequency} / ${item.data.documentCount}`;
      } };
      option.series = [{ type: 'bar', data: values, barMaxWidth: 22, itemStyle: { color: '#7596bd', borderRadius: [0, 5, 5, 0] } }];
    }
    chart('tfidfChart').setOption(option, true);
  }

  function renderSentiment(data) {
    const summary = data.summary || {};
    const counts = summary.sentiment_counts || {};
    const sentimentItems = Object.entries(counts).map(([name, value]) => ({ name, value })).filter((item) => item.value > 0);
    $('#sentimentScope').textContent = `${summary.scope_notice || '词典规则只作倾向线索'} · 当前样本 n=${summary.comment_count || 0} · 人工复核抽样 ${data.review_sample_count || 0} 条`;
    $('#sentimentMethod').textContent = summary.method || '';
    $('#sentimentReviewLink').href = `/api/comments/sentiment/review.csv${filters().toString() ? `?${filters().toString()}` : ''}`;
    renderTfidf(summary.tfidf_keywords || []);
    if (sentimentItems.length) {
      const option = baseOption();
      option.legend = { bottom: 0, type: 'scroll' };
      option.tooltip = { trigger: 'item', formatter: (item) => `${escapeHtml(item.name)}<br>评论数 ${item.value}<br>占样本 ${item.percent}%` };
      option.series = [{ type: 'pie', radius: ['35%', '62%'], center: ['50%', '43%'], label: { formatter: '{b} {d}%' }, data: sentimentItems }];
      chart('sentimentChart').setOption(option, true);
    } else {
      chart('sentimentChart').clear();
      chart('sentimentChart').setOption({ graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '没有可分析评论', fill: '#9aa39d', fontSize: 13 } }] });
    }
    const topics = summary.topic_counts || [];
    setBar('topicChart', topics.slice(0, 12).map((item) => item.label).reverse(), topics.slice(0, 12).map((item) => item.count).reverse(), true, '#7596bd');
    renderGroupTopicComparison('noteTopicChart', data.by_note || []);
    renderGroupTopicComparison('creatorTopicChart', data.by_creator || []);
  }

  function renderRows(rows) {
    const body = $('#commentRows');
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="10" class="empty-cell">当前筛选下没有评论记录</td></tr>';
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
      <td class="raw-payload-cell"><details><summary>查看字段</summary><pre>${escapeHtml(JSON.stringify(row.raw_payload || {}, null, 2))}</pre></details></td>
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
    const [data, sentiment] = await Promise.all([
      requestJson(`/api/comments/analysis${query ? `?${query}` : ''}`),
      requestJson(`/api/comments/sentiment${query ? `?${query}` : ''}`),
    ]);
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
    $('#lengthScope').textContent = `原始评论文本；Unicode 字符数 n=${summary.length_count}`;
    $('#lengthBoxplotScope').textContent = `原文字符数；有效样本 n=${data.length_boxplot?.sample_count || 0}`;
    $('#replyScope').textContent = `精确回复数 n=${data.reply_histogram_sample_count}；${data.reply_histogram_excluded_count} 条缺失/非精确值不参与`;
    $('#scatterScope').textContent = `有效散点 n=${data.scatter.length}；仅描述本批样本，不代表因果关系`;
    $('#analysisStatus').textContent = summary.comment_count < 30 ? `${summary.scope_notice}。词频、点赞和长度关系均不作总体推断。` : '按当前筛选结果更新；描述统计不作总体推断。';
    $('#commentWordcloud').src = `/api/comments/wordcloud.png${query ? `?${query}&` : '?'}v=${Date.now()}`;

    const top = data.top_words || [];
    setBar('topWordsChart', top.map((item) => item.word).reverse(), top.map((item) => item.count).reverse(), true);
    const notes = data.by_note || [];
    setBar('noteCountChart', notes.map((item) => item.label), notes.map((item) => item.count), true, '#789c83');
    const creators = data.by_creator || [];
    setBar('creatorCommentChart', creators.map((item) => item.label), creators.map((item) => item.count), true, '#7596bd');
    setBar('lengthChart', data.length_histogram.map((item) => item.label), data.length_histogram.map((item) => item.count), false, '#799cbb');
    renderLengthBoxplot(data.length_boxplot);
    const likes = data.like_histogram || [];
    setBar('likesChart', likes.map((item) => item.label), likes.map((item) => item.count), false, '#dfa05c');
    const replies = data.reply_histogram || [];
    setBar('replyCountChart', replies.map((item) => item.label), replies.map((item) => item.count), false, '#8b83ae');
    renderNoteWords(data.note_words || []);
    renderScatter(data.scatter || []);
    renderRows(data.comments || []);
    renderSentiment(sentiment);
  }

  async function loadWordChange() {
    const baseline = $('#wordChangeBaseline').value;
    const comparison = $('#wordChangeCurrent').value;
    if (!baseline || !comparison || baseline === comparison) {
      $('#wordChangeStatus').textContent = '请选择两个不同的批次';
      return;
    }
    const params = filters();
    params.delete('batch_id');
    params.set('baseline_batch_id', baseline);
    params.set('comparison_batch_id', comparison);
    try {
      const data = await requestJson(`/api/comments/word-change?${params.toString()}`);
      $('#wordChangeStatus').textContent = `${data.message} · 共同笔记 ${data.common_note_count || 0} 篇 · ${data.baseline_comment_count || 0} / ${data.comparison_comment_count || 0} 条评论`;
      const words = data.words || [];
      if (!words.length) {
        chart('wordChangeChart').clear();
        chart('wordChangeChart').setOption({ graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: data.message || '没有可比较词频', fill: '#9aa39d', fontSize: 13 } }] });
        return;
      }
      const option = baseOption();
      option.grid = { left: 58, right: 28, top: 24, bottom: 90, containLabel: true };
      option.legend = { bottom: 8, type: 'scroll' };
      option.xAxis = { type: 'category', data: words.map((item) => item.word), axisLabel: { rotate: 28, interval: 0 } };
      option.yAxis = { type: 'value', minInterval: 1 };
      option.tooltip = { trigger: 'axis', formatter: (items) => {
        const row = words.find((item) => item.word === items[0]?.axisValue);
        return `${escapeHtml(row?.word)}<br>基线：${row?.baseline_count ?? 0}<br>对比：${row?.comparison_count ?? 0}<br>变化：${row?.delta > 0 ? '+' : ''}${row?.delta ?? 0}`;
      } };
      option.series = [
        { name: '基线', type: 'bar', data: words.map((item) => item.baseline_count), barMaxWidth: 24 },
        { name: '对比', type: 'bar', data: words.map((item) => item.comparison_count), barMaxWidth: 24 },
      ];
      chart('wordChangeChart').setOption(option, true);
    } catch (error) { $('#wordChangeStatus').textContent = error.message; }
  }

  async function refresh() {
    try {
      await loadOptions();
      await loadAnalysis();
    } catch (error) {
      $('#analysisStatus').textContent = `评论分析服务暂不可用：${error.message}`;
      $('#commentRows').innerHTML = '<tr><td colspan="10" class="empty-cell">读取失败，请检查验证数据库是否已导入</td></tr>';
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
    $('#compareWordChange').addEventListener('click', loadWordChange);
    window.addEventListener('resize', () => Object.values(charts).forEach((instance) => instance.resize()));
    refresh();
  });
})();
