/* Read-only, per-source overview. The host owns the source selector and detail view.
 * const view = CreatorScopeHistoryAll.create({ container, requestJson, onSelectSource });
 * await view.show(sourceOptions); // [{ key, label, sample_count }, ...]
 * view.hide(); // also prevents late requests from repainting the hidden view
 */
(function () {
  'use strict';

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function number(value) {
    if (value === null || value === undefined || value === '') return '—';
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString('zh-CN', { maximumFractionDigits: 1 }) : '—';
  }

  function create({ container, requestJson, onSelectSource }) {
    let generation = 0;

    function sourceCard(option, data) {
      const source = data.source || {};
      const metrics = data.metrics || {};
      const card = element('article', 'table-card history-all-card');
      const heading = element('div', 'card-heading table-heading');
      const title = element('div');
      title.append(element('h3', '', source.label || option.label || option.key));
      title.append(element('p', '', `${number(source.sample_count)} 条来源记录 · ${number(source.source_file_count)} 个来源文件`));
      const button = element('button', 'button button-secondary', '查看单来源详情');
      button.type = 'button';
      button.addEventListener('click', () => onSelectSource(option.key));
      heading.append(title, button);
      card.append(heading);

      const scroll = element('div', 'table-scroll');
      const table = element('table');
      const head = element('thead');
      const headRow = element('tr');
      ['字段', '统计值', '有效样本 / 来源记录', '统计周期'].forEach((label) => headRow.append(element('th', '', label)));
      head.append(headRow);
      const body = element('tbody');
      const fields = [
        ['followers', '粉丝数', 'average', '平均值'],
        ['daily_read_median', '日常阅读中位数', 'median', '样本中位数'],
        ['daily_interaction_median', '日常互动中位数', 'median', '样本中位数'],
        ['likes_and_favorites', '获赞与收藏', 'median', '样本中位数'],
        ['published_notes', '发布笔记数', 'median', '样本中位数'],
      ];
      fields.forEach(([key, label, stat, statLabel]) => {
        const metric = metrics[key] || {};
        const row = element('tr');
        const periods = (metric.periods || []).join('、');
        const period = periods ? `已记录：${periods}；部分可能未知` : '未记录';
        row.append(element('td', '', label));
        row.append(element('td', '', `${statLabel} ${number(metric[stat])}${metric[stat] == null ? '' : ` ${metric.unit || ''}`}`));
        row.append(element('td', '', `${number(metric.valid_count || 0)} / ${number(metric.total_count ?? source.sample_count ?? 0)}`));
        row.append(element('td', '', period));
        body.append(row);
      });
      table.append(head, body);
      scroll.append(table);
      card.append(scroll);
      return card;
    }

    function failureCard(option, reason) {
      const card = element('article', 'table-card history-all-card');
      card.append(element('h3', '', option.label || option.key));
      card.append(element('p', 'empty-cell', `读取失败：${reason && reason.message ? reason.message : '请稍后重试'}`));
      return card;
    }

    async function show(sourceOptions) {
      const current = ++generation;
      const sources = Array.isArray(sourceOptions) ? sourceOptions.filter((item) => item && item.key) : [];
      container.hidden = false;
      container.replaceChildren(element('p', 'history-status', '正在分组读取历史数据…'));
      if (!sources.length) {
        container.replaceChildren(element('p', 'empty-cell', '暂无可用历史数据集'));
        return;
      }
      const results = await Promise.allSettled(sources.map((source) => {
        const params = new URLSearchParams({ source: source.key });
        return requestJson(`/api/history-analysis?${params.toString()}`);
      }));
      if (current !== generation) return;
      const fragment = document.createDocumentFragment();
      fragment.append(element('p', 'history-status', '各数据集分开展示；数量为来源记录数，可能包含同一达人，不代表去重人数。'));
      results.forEach((result, index) => {
        fragment.append(result.status === 'fulfilled'
          ? sourceCard(sources[index], result.value)
          : failureCard(sources[index], result.reason));
      });
      container.replaceChildren(fragment);
    }

    function hide() {
      generation += 1;
      container.hidden = true;
    }

    return { show, hide };
  }

  window.CreatorScopeHistoryAll = { create };
}());
