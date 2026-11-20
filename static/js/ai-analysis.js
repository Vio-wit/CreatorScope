(() => {
  const $ = (selector) => document.querySelector(selector);
  const settingsKey = 'creatorscope-ai-local-settings-v1';
  let previewBody = null;
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
      note_ids: selectedValues('#noteSelect'),
      batch_id: $('#batchSelect').value,
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

  async function initialize() {
    const data = await requestJson('/api/ai/config?provider=openai_compatible');
    const provider = $('#providerSelect');
    provider.innerHTML = data.providers.map((item) => '<option value="' + escapeHtml(item.value) + '">' + escapeHtml(item.label) + '</option>').join('');
    const analysisType = $('#analysisType');
    analysisType.innerHTML = data.analyses.map((item) => '<option value="' + escapeHtml(item.value) + '">' + escapeHtml(item.label) + '</option>').join('');
    loadLocalSettings();
    await refreshProviderStatus(false);
    await loadOptions();
  }

  async function createPreview() {
    $('#scopeStatus').textContent = '正在本机生成预览…';
    invalidatePreview('');
    try {
      previewBody = requestBody();
      const data = await requestJson('/api/ai/preview', { method: 'POST', body: JSON.stringify(previewBody) });
      $('#previewScope').textContent = data.scope_summary;
      $('#previewSize').textContent = data.preview_chars + ' 字符';
      $('#redactionNote').textContent = data.redaction;
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
      $('#resultMeta').textContent = data.provider_label + ' · ' + data.model + ' · ' + data.generated_at;
      $('#resultCard').hidden = false;
      $('#runStatus').textContent = '完成。请将模型解释视为推断，并核对旁边的事实数据。';
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
    ['#analysisType', '#batchSelect', '#creatorSelect', '#noteSelect'].forEach((selector) => {
      $(selector).addEventListener('change', () => invalidatePreview());
    });
    ['#baseUrlInput', '#modelInput'].forEach((selector) => {
      $(selector).addEventListener('input', () => invalidatePreview('模型配置已变更；请重新生成预览。'));
    });
  });
})();
