// Общий скрипт для страниц драфтов без спецфункций
// Автоприменение серверных фильтров
document.addEventListener('DOMContentLoaded', function () {
  const form = document.getElementById('filters-form');
  if (form) {
    form.querySelectorAll('select').forEach(sel => {
      sel.addEventListener('change', () => form.submit());
    });
  }
});
