/* Applies the saved (or system) theme before first paint to avoid a flash. */
(function () {
    var theme = null;
    try { theme = localStorage.getItem('theme'); } catch (e) { /* storage blocked */ }
    if (theme !== 'light' && theme !== 'dark') {
        theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    document.documentElement.dataset.theme = theme;
})();
