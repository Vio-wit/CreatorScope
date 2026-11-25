/* CreatorScope 前端图表主题（V2.6）。
 * 色板与 static/css/app.css 中的 --chart-1…6 及中性令牌保持一致；
 * 业务脚本通过 window.CSTheme 读取，避免在各 JS 中重复硬编码。 */
(function () {
  window.CSTheme = {
    fontFamily: 'Microsoft YaHei, Noto Sans SC, sans-serif',
    textColor: '#7e8981',
    axisLabelColor: '#89948d',
    axisLabelMuted: '#a0a9a2',
    axisLineColor: '#e8ece8',
    splitLineColor: '#f0f2f0',
    emptyColor: '#a5ada7',
    /* 系列色板：绿、橙、蓝、红、紫、浅绿 */
    chartColors: ['#438f6c', '#d19a64', '#7596bd', '#c8796d', '#8a78aa', '#78ad86'],
    seriesGreen: '#4a9b75',
    seriesGreenSoft: '#8bb79b',
    seriesBlue: '#7596bd',
    seriesAmber: '#dfa05c',
    seriesRed: '#c77668',
    seriesPurple: '#8b83ae',
    scatterGreen: '#438f6c'
  };
})();
