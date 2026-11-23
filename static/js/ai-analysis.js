(() => {
  const $ = (selector) => document.querySelector(selector);
  const settingsKey = 'creatorscope-ai-local-settings-v1';
  let previewBody = null;
  const unifiedState = { offset: 0, limit: 30, total: 0, selected: new Map(), categories: [], ipLocations: [] };
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || ('请求失败 (' + response.status + ')'));
    return payload;
  }

  function selectedValues(selector) {
    return Array.from($(selector).selectedOptions).map((option) => option.value).filter(Boolean);
  }

  function currentSettings() {
    return {
      provider: $('#providerSelect').value,
      base_url: $('#baseUrlInput').value.trim(),
      model: $('#modelInput').value.trim(),
    };
  }

  function requestBody() {
    return {
      ...currentSettings(),
      analysis_type: $('#analysisType').value,
      creator_ids: selectedValues('#creatorSelect'),
      unified_creator_refs: Array.from(unifiedState.selected.keys()),
      note_ids: selectedValues('#noteSelect'),
      batch_id: $('#batchSelect').value,
      include_raw_text: $('#includeRawText').checked,
    };
  }

  function invalidatePreview(message = '范围已变更；请重新生成预览。') {
    previewBody = null;
    $('#previewCard').hidden = true;
    $('#resultCard').hidden = true;
    $('#uploadConsent').checked = false;
    $('#runAnalysis').disabled = true;
    if (message) $('#scopeStatus').textContent = message;
  }

  async function refreshProviderStatus(useDefaults = false) {
    const settings = currentSettings();
    const params = new URLSearchParams({ provider: settings.provider });
    if (!useDefaults && settings.base_url) params.set('base_url', settings.base_url);
    if (!useDefaults && settings.model) params.set('model', settings.model);
    const data = await requestJson('/api/ai/config?' + params.toString());
    if (useDefaults || !settings.base_url) $('#baseUrlInput').value = data.base_url;
    if (useDefaults || !settings.model) $('#modelInput').value = data.model;
    $('#keyStatus').textContent = data.key_available ? ('密钥已由 ' + data.key_env + ' 提供') : ('未配置密钥 · ' + data.key_env);
    $('#keyStatus').className = 'analysis-count ' + (data.key_available ? 'ai-key-ready' : 'ai-key-missing');
    $('#providerHelp').textContent = data.key_available
      ? '密钥只由服务进程读取；界面、响应和数据库不会显示或保存密钥值。模型调用需单独确认。'
      : '请先在启动 CreatorScope 的本机进程环境中设置 ' + data.key_env + '。本地预览和传统分析不需要密钥。';
    return data;
  }

  function loadLocalSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(settingsKey) || '{}');
      if (saved.provider) $('#providerSelect').value = saved.provider;
      if (saved.base_url) $('#baseUrlInput').value = saved.base_url;
      if (saved.model) $('#modelInput').value = saved.model;
    } catch (_) { /* Ignore malformed local browser preferences. */ }
  }

  async function loadOptions() {
    const data = await requestJson('/api/ai/options');
    const fill = (selector, items, valueKey, label, first) => {
      const node = $(selector);
      node.innerHTML = '<option value="">' + escapeHtml(first) + '</option>' + items.map((item) => '<option value="' + escapeHtml(item[valueKey]) + '">' + escapeHtml(label(item)) + '</option>').join('');
    };
    fill('#creatorSelect', data.creators || [], 'value', (item) => item.label, '选择创作者');
    fill('#noteSelect', data.notes || [], 'value', (item) => item.label, '选择笔记');
    fill('#batchSelect', data.batches || [], 'batch_id', (item) => item.source + ' · ' + (item.captured_at || item.batch_id), '选择批次');
  }

  const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 });
  function displayNumber(value) {
    return value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? '—' : numberFormat.format(Number(value));
  }

  function selectedUnifiedRefs() {
    return Array.from(unifiedState.selected.keys());
  }

  function updateUnifiedSelection() {
    const count = unifiedState.selected.size;
    $('#unifiedSelectionSummary').textContent = `已选择 ${count} / 4 位`;
    $('#compareUnifiedCreators').disabled = count < 2 || count > 4;
    $('#previewUnifiedAdvice').disabled = count < 2 || count > 4;
    document.querySelectorAll('[data-unified-ref]').forEach((checkbox) => {
      checkbox.checked = unifiedState.selected.has(checkbox.dataset.unifiedRef);
    });
  }

  function renderUnifiedRows(records) {
    const body = $('#unifiedCreatorRows');
    if (!records.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty-cell">没有匹配记录；可减少条件或切换来源组。</td></tr>';
      return;
    }
    body.innerHTML = records.map((record) => {
      const fields = record.fields || {};
      const sourceMeta = [record.bucket_label, record.source_name, record.captured_at || '时间未知'].filter(Boolean).join(' · ');
      const complete = record.completeness || {};
      return `<tr><td><input type="checkbox" data-unified-ref="${escapeHtml(record.ref)}" aria-label="选择 ${escapeHtml(record.name)}"></td><td><strong>${escapeHtml(record.name)}</strong><small>${escapeHtml(sourceMeta)}</small></td><td>${escapeHtml(displayNumber(fields.followers))}</td><td>${escapeHtml(displayNumber(fields.daily_read_median))}</td><td>${escapeHtml(displayNumber(fields.daily_interaction_median))}</td><td>${escapeHtml(fields.category || '—')}</td><td>${escapeHtml(complete.valid_fields ?? 0)} / ${escapeHtml(complete.total_fields ?? 0)} · ${escapeHtml(complete.percentage ?? 0)}%</td></tr>`;
    }).join('');
    body.querySelectorAll('[data-unified-ref]').forEach((checkbox) => {
      checkbox.checked = unifiedState.selected.has(checkbox.dataset.unifiedRef);
      checkbox.addEventListener('change', () => {
        const ref = checkbox.dataset.unifiedRef;
        const record = records.find((item) => item.ref === ref);
        if (checkbox.checked) {
          if (unifiedState.selected.size >= 4) {
            checkbox.checked = false;
            $('#unifiedFilterStatus').textContent = '最多选择 4 位达人。';
            return;
          }
          if (record) unifiedState.selected.set(ref, record);
        } else unifiedState.selected.delete(ref);
        updateUnifiedSelection();
        invalidatePreview('对比对象已变更；请重新生成本机预览。');
      });
    });
    updateUnifiedSelection();
  }

  function fillFacet(selector, values, firstLabel) {
    const node = $(selector);
    const current = node.value;
    node.innerHTML = `<option value="">${escapeHtml(firstLabel)}</option>` + values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
    if (values.includes(current)) node.value = current;
  }

  function unifiedSearchParams() {
    const params = new URLSearchParams({ offset: String(unifiedState.offset), limit: String(unifiedState.limit) });
    const values = {
      q: $('#unifiedSearch').value.trim(), bucket: $('#unifiedBucket').value,
      category: $('#unifiedCategory').value, ip_location: $('#unifiedIp').value,
      followers_min: $('#filterFollowersMin').value, followers_max: $('#filterFollowersMax').value,
      daily_read_min: $('#filterDailyReadMin').value, daily_read_max: $('#filterDailyReadMax').value,
      daily_interaction_min: $('#filterDailyInteractionMin').value, daily_interaction_max: $('#filterDailyInteractionMax').value,
    };
    Object.entries(values).forEach(([key, value]) => { if (value !== '') params.set(key, value); });
    return params;
  }

  async function refreshUnifiedCreators() {
    $('#unifiedFilterStatus').textContent = '正在查询本机只读数据…';
    try {
      const data = await requestJson('/api/unified-creators?' + unifiedSearchParams().toString());
      unifiedState.total = Number(data.total_count || 0);
      renderUnifiedRows(data.records || []);
      $('#unifiedResultCount').textContent = `${numberFormat.format(unifiedState.total)} 条匹配记录`;
      const start = unifiedState.total ? unifiedState.offset + 1 : 0;
      const end = Math.min(unifiedState.offset + unifiedState.limit, unifiedState.total);
      $('#unifiedPageStatus').textContent = `${start}–${end} / ${numberFormat.format(unifiedState.total)}`;
      $('#unifiedPrevious').disabled = unifiedState.offset <= 0;
      $('#unifiedNext').disabled = unifiedState.offset + unifiedState.limit >= unifiedState.total;
      $('#unifiedFilterStatus').textContent = '本地只读筛选完成；不同来源的统计口径仍需按来源和时间判断。';
    } catch (error) {
      $('#unifiedCreatorRows').innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
      $('#unifiedFilterStatus').textContent = error.message;
    }
  }

  async function loadUnifiedSelector() {
    const facetData = await requestJson('/api/unified-creators?limit=1');
    unifiedState.categories = facetData.categories || [];
    unifiedState.ipLocations = facetData.ip_locations || [];
    fillFacet('#unifiedCategory', unifiedState.categories, '全部领域');
    fillFacet('#unifiedIp', unifiedState.ipLocations, '全部 IP');
    const bucket = $('#unifiedBucket');
    bucket.innerHTML = '<option value="">全部来源</option>' + (facetData.groups || []).map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)} · ${escapeHtml(item.count)}</option>`).join('');
    const refs = Array.from(new URLSearchParams(window.location.search).getAll('creator_ref')).slice(0, 4);
    if (refs.length >= 2) {
      try {
        const params = new URLSearchParams();
        refs.forEach((ref) => params.append('ref', ref));
        const selected = await requestJson('/api/unified-creators/compare?' + params.toString());
        (selected.records || []).forEach((record) => unifiedState.selected.set(record.ref, record));
        $('#analysisType').value = 'unified_creator_compare';
      } catch (error) {
        $('#unifiedFilterStatus').textContent = error.message;
      }
    }
    updateUnifiedSelection();
    await refreshUnifiedCreators();
  }

  function quantityValue(text) {
    const match = String(text || '').replace(/,/g, '').match(/(\d+(?:\.\d+)?)\s*(亿|万|w|千|k)?/i);
    if (!match) return null;
    const multiplier = ({ '亿': 100000000, '万': 10000, w: 10000, '千': 1000, k: 1000 })[match[2]?.toLowerCase()] || 1;
    return Number(match[1]) * multiplier;
  }

  function parseNaturalNeed() {
    const text = $('#naturalNeed').value.trim();
    if (!text) {
      $('#naturalNeedStatus').textContent = '先写一条合作需求，再整理筛选条件。';
      return;
    }
    ['#unifiedSearch', '#filterFollowersMin', '#filterFollowersMax', '#filterDailyReadMin', '#filterDailyReadMax', '#filterDailyInteractionMin', '#filterDailyInteractionMax'].forEach((selector) => { $(selector).value = ''; });
    $('#unifiedCategory').value = '';
    $('#unifiedIp').value = '';
    const parsed = [];
    const quantity = '(\\d+(?:,\\d{3})*(?:\\.\\d+)?\\s*(?:亿|万|w|千|k)?)';
    const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const rules = [
      { aliases: ['粉丝数', '粉丝'], min: '#filterFollowersMin', max: '#filterFollowersMax', label: '粉丝数' },
      { aliases: ['日常阅读中位数', '日常阅读', '阅读中位数', '阅读量', '阅读'], min: '#filterDailyReadMin', max: '#filterDailyReadMax', label: '日常阅读' },
      { aliases: ['日常互动中位数', '日常互动', '互动中位数', '互动量', '互动'], min: '#filterDailyInteractionMin', max: '#filterDailyInteractionMax', label: '日常互动' },
    ];
    const minInclusiveOps = '(?:至少|不少于|不低于|大于等于|>=|≥)';
    const minStrictOps = '(?:超过|高于|大于|多于)';
    const maxInclusiveOps = '(?:至多|最多|不超过|不高于|小于等于|<=|≤|以内)';
    const maxStrictOps = '(?:低于|少于|小于)';
    rules.forEach((rule) => {
      const aliases = rule.aliases.slice().sort((a, b) => b.length - a.length).map(escapeRegex).join('|');
      const patterns = [
        { side: 'min', strict: false, regex: new RegExp(`(?:${aliases})\\s*${minInclusiveOps}\\s*${quantity}`, 'i') },
        { side: 'min', strict: true, regex: new RegExp(`(?:${aliases})\\s*${minStrictOps}\\s*${quantity}`, 'i') },
        { side: 'min', regex: new RegExp(`(?:${aliases})\\s*${quantity}\\s*(?:及以上|以上)`, 'i') },
        { side: 'min', strict: false, regex: new RegExp(`${minInclusiveOps}\\s*${quantity}\\s*(?:个)?\\s*(?:${aliases})`, 'i') },
        { side: 'min', strict: true, regex: new RegExp(`${minStrictOps}\\s*${quantity}\\s*(?:个)?\\s*(?:${aliases})`, 'i') },
        { side: 'max', strict: false, regex: new RegExp(`(?:${aliases})\\s*${maxInclusiveOps}\\s*${quantity}`, 'i') },
        { side: 'max', strict: true, regex: new RegExp(`(?:${aliases})\\s*${maxStrictOps}\\s*${quantity}`, 'i') },
        { side: 'max', regex: new RegExp(`(?:${aliases})\\s*${quantity}\\s*(?:及以下|以下|以内)`, 'i') },
        { side: 'max', strict: false, regex: new RegExp(`${maxInclusiveOps}\\s*${quantity}\\s*(?:个)?\\s*(?:${aliases})`, 'i') },
        { side: 'max', strict: true, regex: new RegExp(`${maxStrictOps}\\s*${quantity}\\s*(?:个)?\\s*(?:${aliases})`, 'i') },
      ];
      patterns.forEach(({ side, strict = false, regex }) => {
        const match = text.match(regex);
        if (match) {
          let value = quantityValue(match[1]);
          if (value !== null) {
            if (strict) value += side === 'min' ? 1 : -1;
            value = Math.max(0, value);
            $(side === 'min' ? rule.min : rule.max).value = String(value);
            parsed.push(`${rule.label}${side === 'min' ? '≥' : '≤'}${displayNumber(value)}`);
          }
        }
      });
    });
    const category = unifiedState.categories.slice().sort((a, b) => b.length - a.length).find((value) => text.includes(value));
    if (category) { $('#unifiedCategory').value = category; parsed.push(`领域=${category}`); }
    const ip = unifiedState.ipLocations.slice().sort((a, b) => b.length - a.length).find((value) => text.includes(value));
    if (ip) { $('#unifiedIp').value = ip; parsed.push(`蒲公英账号 IP=${ip}`); }
    const nameMatch = text.match(/(?:名称|达人名|博主名|账号名|ID)\s*(?:是|为|叫|[:：])\s*([^\s，,。；;]{1,50})/i);
    if (nameMatch) { $('#unifiedSearch').value = nameMatch[1]; parsed.push(`名称/ID=${nameMatch[1]}`); }
    $('#naturalNeedStatus').textContent = parsed.length
      ? `已整理 ${parsed.join('；')}。请检查条件后点击“筛选达人”。`
      : '本机规则没有识别到白名单条件；请直接编辑下方筛选项。文本不会发送给模型。';
    if (rules.some((rule) => $(rule.min).value || $(rule.max).value)) document.querySelector('.unified-range-details').open = true;
    unifiedState.offset = 0;
  }

  function renderComparison(records) {
    const rows = $('#unifiedCompareRows');
    const metrics = [
      ['followers', '粉丝数'], ['daily_read_median', '日常阅读'], ['daily_interaction_median', '日常互动'],
    ];
    const signatures = new Set(records.map((item) => `${item.bucket}|${item.source_name}|${item.batch_id || ''}`));
    const comparableBatch = signatures.size === 1 && records.every((item) => Boolean(item.batch_id));
    $('#unifiedCompareNotice').textContent = comparableBatch
      ? '这些记录来自同一来源组、来源和批次；图表只纳入共同存在的数值。单位按原始字段含义显示。'
      : '记录来自不同来源组、来源或批次，或缺少批次信息。只并列展示真实原值，不用共同比例图；统计周期和单位以来源原始记录为准。';
    $('#unifiedCompareCharts').innerHTML = comparableBatch ? metrics.map(([key, label]) => {
      const values = records.map((record) => ({ record, value: record.fields?.[key] }));
      const valid = values.filter((item) => item.value !== null && item.value !== undefined && Number.isFinite(Number(item.value)));
      const max = Math.max(0, ...valid.map((item) => Number(item.value)));
      const bars = valid.map(({ record, value }) => `<div class="unified-bar-row"><span>${escapeHtml(record.name)}</span><i><b style="width:${max > 0 ? Math.max(1, Number(value) * 100 / max) : 0}%"></b></i><strong>${escapeHtml(displayNumber(value))}</strong></div>`).join('');
      return `<article class="unified-metric-chart"><h4>${escapeHtml(label)}</h4><small>共同有效 ${valid.length} / ${records.length}</small>${bars || '<p>当前没有共同有效数值</p>'}</article>`;
    }).join('') : '';
    rows.innerHTML = records.map((record) => {
      const fields = record.fields || {};
      const image = fields.image_fixed_price || '—';
      const video = fields.video_fixed_price || '—';
      return `<tr><td><strong>${escapeHtml(record.name)}</strong><small>${escapeHtml(record.platform_id || '无平台 ID')}</small></td><td>${escapeHtml(record.bucket_label || '—')}</td><td>${escapeHtml(record.source_name || '—')}</td><td>${escapeHtml(record.captured_at || '未知')}</td><td>${escapeHtml(record.completeness?.valid_fields ?? 0)} / ${escapeHtml(record.completeness?.total_fields ?? 0)} · ${escapeHtml(record.completeness?.percentage ?? 0)}%</td><td>${escapeHtml(displayNumber(fields.followers))}</td><td>${escapeHtml(displayNumber(fields.daily_read_median))}</td><td>${escapeHtml(displayNumber(fields.daily_interaction_median))}</td><td>${escapeHtml(fields.category || '—')}</td><td>${escapeHtml(image)} / ${escapeHtml(video)}</td></tr>`;
    }).join('');
    $('#unifiedComparePanel').hidden = false;
  }

  async function compareUnifiedCreators() {
    const refs = selectedUnifiedRefs();
    const params = new URLSearchParams();
    refs.forEach((ref) => params.append('ref', ref));
    $('#unifiedCompareNotice').textContent = '正在读取所选记录…';
    try {
      const data = await requestJson('/api/unified-creators/compare?' + params.toString());
      renderComparison(data.records || []);
    } catch (error) {
      $('#unifiedCompareNotice').textContent = error.message;
      $('#unifiedComparePanel').hidden = false;
    }
  }

  async function loadReportHistory(selectRunId = '') {
    const data = await requestJson('/api/ai/reports');
    const select = $('#savedReportSelect');
    const reports = data.reports || [];
    select.innerHTML = '<option value="">选择一条本地 AI 报告</option>' + reports.map((item) => {
      const rawTextLabel = item.include_raw_text ? '包含原文' : '不含原文';
      const scope = item.input_creator_refs?.length ? `达人 ${item.input_creator_refs.length} 位` : `批次 ${item.input_batch_id || '未指定'}`;
      const label = `${item.analysis_at || '时间未知'} · ${item.provider_label || item.provider} / ${item.model || '模型未知'} · ${scope} · ${item.analysis_label || item.analysis_type} · ${rawTextLabel}`;
      return `<option value="${escapeHtml(item.run_id)}">${escapeHtml(label)}</option>`;
    }).join('');
    if (selectRunId && reports.some((item) => item.run_id === selectRunId)) select.value = selectRunId;
    $('#savedReportStatus').textContent = reports.length ? `本机保存 ${reports.length} 条报告记录` : '尚无已完成并保存的模型分析报告';
  }

  async function showSavedReport(runId) {
    if (!runId) return;
    const data = await requestJson(`/api/ai/reports/${encodeURIComponent(runId)}`);
    $('#factsOutput').textContent = JSON.stringify(data.facts || {}, null, 2);
    $('#modelOutput').textContent = data.model_output || '';
    const scope = data.input_creator_refs?.length ? `达人 ${data.input_creator_refs.length} 位` : `批次 ${data.input_batch_id || '未指定'}`;
    $('#resultMeta').textContent = `${data.provider_label || data.provider} · ${data.model} · ${scope} · ${data.include_raw_text ? '包含原文' : '不含原文'} · ${data.analysis_at || ''} · ${data.run_id}`;
    $('#resultCard').hidden = false;
    $('#savedReportStatus').textContent = `已打开本地记录 ${data.run_id}`;
  }

  async function initialize() {
    const data = await requestJson('/api/ai/config?provider=openai_compatible');
    const provider = $('#providerSelect');
    provider.innerHTML = data.providers.map((item) => '<option value="' + escapeHtml(item.value) + '">' + escapeHtml(item.label) + '</option>').join('');
    const analysisType = $('#analysisType');
    const visibleAnalyses = data.analyses.filter((item) => !['comment_topics', 'comment_sentiment', 'creator_note_compare'].includes(item.value));
    analysisType.innerHTML = visibleAnalyses.map((item) => '<option value="' + escapeHtml(item.value) + '">' + escapeHtml(item.label) + '</option>').join('');
    loadLocalSettings();
    await refreshProviderStatus(false);
    await loadOptions();
    await Promise.all([loadReportHistory(), loadUnifiedSelector()]);
  }

  async function createPreview() {
    $('#scopeStatus').textContent = '正在本机生成预览…';
    invalidatePreview('');
    try {
      previewBody = requestBody();
      const data = await requestJson('/api/ai/preview', { method: 'POST', body: JSON.stringify(previewBody) });
      $('#previewScope').textContent = data.scope_summary;
      $('#previewSize').textContent = data.preview_chars + ' 字符';
      $('#dataNotice').textContent = data.data_notice;
      $('#previewData').textContent = JSON.stringify(data.data, null, 2);
      $('#previewCard').hidden = false;
      $('#scopeStatus').textContent = '预览在本机生成；尚未调用模型服务。';
      $('#runStatus').textContent = data.scope_summary;
      $('#resultCard').hidden = true;
    } catch (error) {
      previewBody = null;
      $('#scopeStatus').textContent = error.message;
    }
  }

  async function runAnalysis() {
    if (!previewBody || !$('#uploadConsent').checked) return;
    $('#runAnalysis').disabled = true;
    $('#runStatus').textContent = '正在向所选服务商发送已预览的数据…';
    try {
      const data = await requestJson('/api/ai/analyze', {
        method: 'POST',
        body: JSON.stringify({ ...previewBody, confirm_upload: true }),
      });
      if (data.status !== 'completed') {
        $('#runStatus').textContent = data.message;
        $('#runAnalysis').disabled = false;
        return;
      }
      $('#factsOutput').textContent = JSON.stringify(data.facts, null, 2);
      $('#modelOutput').textContent = data.model_output;
      const scope = data.input_creator_refs?.length ? `达人 ${data.input_creator_refs.length} 位` : `批次 ${data.input_batch_id || '未指定'}`;
      $('#resultMeta').textContent = data.report_saved
        ? `${data.provider_label} · ${data.model} · ${scope} · ${data.include_raw_text ? '包含原文' : '不含原文'} · ${data.analysis_at} · ${data.run_id}`
        : `${data.provider_label} · ${data.model} · ${scope} · ${data.analysis_at} · 本地保存失败`;
      $('#resultCard').hidden = false;
      $('#runStatus').textContent = data.report_saved ? '完成并已保存本地报告。请将模型解释视为推断，并核对旁边的事实数据。' : '模型已返回结果，但本地报告保存失败；请复制需要保留的内容并检查本机写入权限。';
      if (data.report_saved) await loadReportHistory(data.run_id);
    } catch (error) {
      $('#runStatus').textContent = error.message;
      $('#runAnalysis').disabled = false;
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    try {
      await initialize();
    } catch (error) {
      $('#scopeStatus').textContent = '初始化失败：' + error.message;
      return;
    }
    $('#providerSelect').addEventListener('change', async () => {
      invalidatePreview('Provider 已变更。');
      try { await refreshProviderStatus(true); }
      catch (error) { $('#keyStatus').textContent = error.message; }
    });
    $('#saveSettings').addEventListener('click', () => {
      const settings = currentSettings();
      localStorage.setItem(settingsKey, JSON.stringify(settings));
      $('#scopeStatus').textContent = '本机浏览器设置已保存（不含 API Key）。';
      refreshProviderStatus().catch((error) => { $('#keyStatus').textContent = error.message; });
    });
    $('#testConnection').addEventListener('click', async () => {
      $('#providerHelp').textContent = '正在发送仅含“请只回复 OK”的连接测试；可能产生少量模型用量。';
      try {
        const data = await requestJson('/api/ai/test', { method: 'POST', body: JSON.stringify(currentSettings()) });
        $('#providerHelp').textContent = data.status === 'connected'
          ? ('连接成功 · ' + data.provider_label + ' · ' + data.model)
          : data.message;
      } catch (error) {
        $('#providerHelp').textContent = error.message;
      }
    });
    $('#previewButton').addEventListener('click', createPreview);
    $('#uploadConsent').addEventListener('change', () => {
      $('#runAnalysis').disabled = !previewBody || !$('#uploadConsent').checked;
    });
    $('#runAnalysis').addEventListener('click', runAnalysis);
    $('#loadSavedReport').addEventListener('click', async () => {
      try { await showSavedReport($('#savedReportSelect').value); }
      catch (error) { $('#savedReportStatus').textContent = error.message; }
    });
    $('#parseNaturalNeed').addEventListener('click', parseNaturalNeed);
    $('#searchUnifiedCreators').addEventListener('click', () => { unifiedState.offset = 0; refreshUnifiedCreators().catch((error) => { $('#unifiedFilterStatus').textContent = error.message; }); });
    $('#clearUnifiedFilters').addEventListener('click', () => {
      ['#naturalNeed', '#unifiedSearch', '#filterFollowersMin', '#filterFollowersMax', '#filterDailyReadMin', '#filterDailyReadMax', '#filterDailyInteractionMin', '#filterDailyInteractionMax'].forEach((selector) => { $(selector).value = ''; });
      $('#unifiedBucket').value = '';
      $('#unifiedCategory').value = '';
      $('#unifiedIp').value = '';
      $('#naturalNeedStatus').textContent = '筛选条件已清空。';
      unifiedState.offset = 0;
      refreshUnifiedCreators().catch((error) => { $('#unifiedFilterStatus').textContent = error.message; });
    });
    $('#unifiedPrevious').addEventListener('click', () => { unifiedState.offset = Math.max(0, unifiedState.offset - unifiedState.limit); refreshUnifiedCreators().catch((error) => { $('#unifiedFilterStatus').textContent = error.message; }); });
    $('#unifiedNext').addEventListener('click', () => { unifiedState.offset += unifiedState.limit; refreshUnifiedCreators().catch((error) => { $('#unifiedFilterStatus').textContent = error.message; }); });
    $('#unifiedSearch').addEventListener('keydown', (event) => { if (event.key === 'Enter') { unifiedState.offset = 0; refreshUnifiedCreators().catch((error) => { $('#unifiedFilterStatus').textContent = error.message; }); } });
    $('#compareUnifiedCreators').addEventListener('click', compareUnifiedCreators);
    $('#previewUnifiedAdvice').addEventListener('click', () => {
      $('#analysisType').value = 'unified_creator_compare';
      invalidatePreview('已选择统一达人对比；请检查本机预览。');
      createPreview();
    });
    ['#analysisType', '#batchSelect', '#creatorSelect', '#noteSelect', '#includeRawText'].forEach((selector) => {
      $(selector).addEventListener('change', () => invalidatePreview());
    });
    ['#baseUrlInput', '#modelInput'].forEach((selector) => {
      $(selector).addEventListener('input', () => invalidatePreview('模型配置已变更；请重新生成预览。'));
    });
  });
})();
