(() => {
  document.addEventListener('DOMContentLoaded', () => {
    const printButton = document.getElementById('printReport');
    printButton?.addEventListener('click', () => window.print());
  });
})();
