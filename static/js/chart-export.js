/**
 * 图表导出：给每张 ECharts 卡片加一个 PNG 下载按钮，并提供一个浮动的
 * “导出全部 PNG / 打印为 PDF”工具条。
 *
 * 图表由各页面自己的脚本异步渲染，所以这里用 MutationObserver 监听，等
 * ECharts 实例就绪后再插入按钮；没有实例的卡片会被跳过。
 */
(() => {
  if (window.__csChartExportReady) return;
  window.__csChartExportReady = true;

  const BUTTON_CLASS = 'chart-export-button';
  const scanDelay = 400;
  let scanTimer = null;

  const dayStamp = () => {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, '0');
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  };

  const safeName = (text) =>
    String(text || 'chart')
      .replace(/[\\/:*?"<>|\s]+/g, '_')
      .replace(/_+/g, '_')
      .slice(0, 48) || 'chart';

  const instanceFor = (element) => {
    if (!element || !window.echarts) return null;
    try {
      return window.echarts.getInstanceByDom(element);
    } catch (error) {
      return null;
    }
  };

  const chartElementOf = (card) => {
    for (const candidate of card.querySelectorAll('div[id]')) {
      const instance = instanceFor(candidate);
      if (instance) return candidate;
    }
    return null;
  };

  const download = (href, filename) => {
    const link = document.createElement('a');
    link.href = href;
    link.download = filename;
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const cardTitle = (card) => card.querySelector('.card-heading h2')?.textContent?.trim() || '图表';

  const exportPng = (card) => {
    const element = chartElementOf(card);
    const instance = instanceFor(element);
    if (!instance) return false;
    const url = instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#ffffff' });
    if (!url) return false;
    // Include the visible card heading so the exported figure keeps its context.
    const image = new Image();
    image.onload = () => {
      const heading = card.querySelector('.card-heading');
      const title = cardTitle(card);
      const detail = heading?.querySelector('p')?.textContent?.trim() || '';
      const canvas = document.createElement('canvas');
      canvas.width = image.width;
      canvas.height = image.height + 96;
      const context = canvas.getContext('2d');
      context.fillStyle = '#fff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = '#243b2e';
      context.font = 'bold 24px sans-serif';
      context.fillText(title, 20, 32, canvas.width - 40);
      context.fillStyle = '#657469';
      context.font = '18px sans-serif';
      if (detail) context.fillText(detail, 20, 62, canvas.width - 40);
      context.drawImage(image, 0, 96);
      download(canvas.toDataURL('image/png'), `${safeName(title)}_${dayStamp()}.png`);
    };
    image.src = url;
    return true;
  };

  const injectButton = (card) => {
    if (card.querySelector(`.${BUTTON_CLASS}`)) return;
    const heading = card.querySelector('.card-heading');
    if (!heading) return;
    if (!chartElementOf(card)) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = BUTTON_CLASS;
    button.title = '下载这张图为 PNG';
    button.textContent = 'PNG';
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      exportPng(card);
    });
    heading.appendChild(button);
  };

  const scan = () => {
    document.querySelectorAll('.chart-card').forEach(injectButton);
  };

  const scheduleScan = () => {
    if (scanTimer) return;
    scanTimer = setTimeout(() => {
      scanTimer = null;
      scan();
    }, scanDelay);
  };

  const buildToolbar = () => {
    if (document.querySelector('.export-fab')) return;
    const bar = document.createElement('div');
    bar.className = 'export-fab no-print';

    const exportAll = document.createElement('button');
    exportAll.type = 'button';
    exportAll.textContent = '导出全部 PNG';
    exportAll.addEventListener('click', () => {
      const cards = [...document.querySelectorAll('.chart-card')].filter((card) => chartElementOf(card));
      if (!cards.length) return;
      exportAll.disabled = true;
      exportAll.textContent = `导出中 0/${cards.length}`;
      let index = 0;
      const step = () => {
        if (index >= cards.length) {
          exportAll.disabled = false;
          exportAll.textContent = '导出全部 PNG';
          return;
        }
        exportPng(cards[index]);
        index += 1;
        exportAll.textContent = `导出中 ${index}/${cards.length}`;
        setTimeout(step, 220);
      };
      step();
    });

    const print = document.createElement('button');
    print.type = 'button';
    print.textContent = '打印 / PDF';
    print.addEventListener('click', () => window.print());

    bar.append(exportAll, print);
    document.body.appendChild(bar);
  };

  const start = () => {
    buildToolbar();
    scan();
    const observer = new MutationObserver(scheduleScan);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', scheduleScan);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
