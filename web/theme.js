const key = 'pathfinder:appearance';

export function initializeTheme() {
  let preference;
  try { preference = localStorage.getItem(key); } catch {}
  const theme = preference === 'dark' || preference === 'light'
    ? preference : matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  applyTheme(theme);
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#111622' : '#fbfaf7';
}

export function themeButton() {
  const pressed = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'dark';
  return { pressed, title: pressed ? '切换到日间模式' : '切换到夜间模式' };
}

export function toggleTheme(button) {
  const theme = themeButton().pressed ? 'light' : 'dark';
  applyTheme(theme);
  try { localStorage.setItem(key, theme); } catch {}
  const state = themeButton();
  button.title = state.title;
  button.setAttribute('aria-pressed', String(state.pressed));
}
