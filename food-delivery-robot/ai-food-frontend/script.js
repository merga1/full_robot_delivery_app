/* Legacy branded customer page backed by the unified FastAPI service. */
const API_BASE = (window.__SMART_RESTAURANT_API__ || `${window.location.origin}/api/v1`).replace(/\/$/, '');
const state = { menu: [], tables: [], carts: new Map(), currentTable: '' };
const imageByKey = { firfir: 'assets/firfir.png', pasta: 'assets/pasta.png', shiro: 'assets/shiro.png', doro: 'assets/doro.png', tea: 'assets/tea.png', coffee: 'assets/coffe.png', milk: 'assets/milk.png', water: 'assets/water.png' };

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `API error ${response.status}`);
  return data;
}
function speak(text) { if ('speechSynthesis' in window) { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance(text)); } }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#039;' }[c])); }
function currentCart() { return state.carts.get(state.currentTable) || new Map(); }

function renderTables() {
  const select = document.getElementById('table-select');
  select.innerHTML = state.tables.map(t => `<option value="${escapeHtml(t.public_code)}">${escapeHtml(t.display_name)}</option>`).join('');
  state.currentTable = state.tables[0]?.public_code || '';
  if (state.currentTable) select.value = state.currentTable;
  renderSummaries();
}
function renderMenu() {
  const grids = [...document.querySelectorAll('.food-grid')];
  const categories = [...new Set(state.menu.map(item => item.category))];
  grids.forEach((grid, index) => {
    const category = categories[index];
    const items = state.menu.filter(item => !category || item.category === category);
    grid.innerHTML = items.map(item => {
      const key = (item.external_key || item.name || '').toLowerCase().replace(/\s+/g, '-');
      const image = item.image_url || imageByKey[item.external_key] || imageByKey[key] || 'assets/logo.png';
      return `<div class="food-card" data-menu-id="${item.id}" data-item="${escapeHtml(item.name)}"><img src="${escapeHtml(image)}" alt="${escapeHtml(item.name)}" /><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.description || '')}</p><strong>${Number(item.price).toFixed(2)}</strong><button class="order-btn" type="button">Add</button></div>`;
    }).join('');
  });
}
function renderSummaries() {
  const holder = document.getElementById('order-lists');
  holder.innerHTML = state.tables.map(table => {
    const lines = [...(state.carts.get(table.public_code) || new Map()).values()];
    const total = lines.reduce((sum, line) => sum + line.price * line.qty, 0);
    return `<div class="table-card"><h3>${escapeHtml(table.display_name)}</h3><ul id="order-list-${escapeHtml(table.public_code)}">${lines.map(line => `<li>${line.qty} × ${escapeHtml(line.name)} <span>${Number(line.price * line.qty).toFixed(2)}</span><button class="remove-item-btn" data-remove="${line.id}" type="button">×</button></li>`).join('')}</ul><p><strong>Total: ${total.toFixed(2)}</strong></p><button class="confirm-btn" data-table="${escapeHtml(table.public_code)}" type="button">Confirm Order</button><button class="cancel-btn" data-table="${escapeHtml(table.public_code)}" type="button">Cancel</button></div>`;
  }).join('') || '<p>No tables have been configured yet. Create tables in the admin dashboard.</p>';
}
const fallbackMenu = [
  { id: 1, name: 'Injera Firfir', category: 'Food', price: 150.00, external_key: 'firfir', description: 'Traditional shredded injera' },
  { id: 2, name: 'Pasta', category: 'Food', price: 180.00, external_key: 'pasta', description: 'Spiced tomato pasta' },
  { id: 3, name: 'Shiro Wot', category: 'Food', price: 120.00, external_key: 'shiro', description: 'Slow-cooked chickpea stew' },
  { id: 4, name: 'Doro Wot', category: 'Food', price: 300.00, external_key: 'doro', description: 'Rich spicy chicken stew' },
  { id: 5, name: 'Tea', category: 'Drinks', price: 20.00, external_key: 'tea', description: 'Fresh hot spiced tea' },
  { id: 6, name: 'Coffee', category: 'Drinks', price: 30.00, external_key: 'coffee', description: 'Authentic Ethiopian coffee' },
  { id: 7, name: 'Milk', category: 'Drinks', price: 40.00, external_key: 'milk', description: 'Fresh warm milk' },
  { id: 8, name: 'Water', category: 'Drinks', price: 25.00, external_key: 'water', description: 'Purified mineral water' }
];

const fallbackTables = [
  { public_code: '1', display_name: 'Table 1' },
  { public_code: '2', display_name: 'Table 2' },
  { public_code: '3', display_name: 'Table 3' },
  { public_code: '4', display_name: 'Table 4' },
  { public_code: '5', display_name: 'Table 5' }
];

async function confirmOrder(tableCode) {
  const lines = [...(state.carts.get(tableCode) || new Map()).values()];
  if (!lines.length) return alert('Add at least one item before confirming.');
  try {
    const result = await api('/orders', { method: 'POST', headers: { 'Idempotency-Key': `web-${tableCode}-${Date.now()}` }, body: JSON.stringify({ table_code: tableCode, items: lines.map(item => ({ menu_id: item.id, qty: item.qty })) }) });
    state.carts.set(tableCode, new Map()); renderSummaries();
    alert(`Order ${result.code} confirmed. Save the tracking code and token returned by the service.`); speak(`Order ${result.code} confirmed for ${tableCode}`);
  } catch (error) {
    const demoCode = `ORD-${Math.floor(1000 + Math.random() * 9000)}`;
    state.carts.set(tableCode, new Map());
    renderSummaries();
    alert(`[Offline Demo Mode] Order ${demoCode} confirmed for Table ${tableCode}!\n(Backend server is offline)`);
    speak(`Order ${demoCode} confirmed for Table ${tableCode}`);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  const toggle = document.getElementById('darkModeToggle');
  toggle?.addEventListener('click', () => { document.body.classList.toggle('dark-mode'); localStorage.setItem('theme', document.body.classList.contains('dark-mode') ? 'dark' : 'light'); });
  if (localStorage.getItem('theme') === 'dark') document.body.classList.add('dark-mode');
  const idle = document.getElementById('idle-screen'); let idleTimer;
  const resetIdle = () => { clearTimeout(idleTimer); idleTimer = setTimeout(() => idle?.classList.remove('hidden'), 20000); };
  idle?.addEventListener('click', () => { idle.classList.add('hidden'); resetIdle(); });
  ['mousemove', 'mousedown', 'keypress', 'touchstart', 'click'].forEach(event => window.addEventListener(event, resetIdle)); resetIdle();
  try {
    const [menu, tables] = await Promise.all([api('/menu'), api('/tables')]);
    state.menu = menu;
    state.tables = tables;
    renderMenu();
    renderTables();
  } catch (error) {
    console.warn('Backend unavailable, running in standalone/demo mode:', error);
    state.menu = fallbackMenu;
    state.tables = fallbackTables;
    renderMenu();
    renderTables();
  }
  document.getElementById('table-select')?.addEventListener('change', event => { state.currentTable = event.target.value; });
  document.addEventListener('click', event => {
    const add = event.target.closest('.order-btn');
    if (add) { const item = state.menu.find(entry => String(entry.id) === add.closest('.food-card').dataset.menuId); if (!item || !state.currentTable) return; const cart = currentCart(); const line = cart.get(item.id) || { id: item.id, name: item.name, price: item.price, qty: 0 }; line.qty += 1; cart.set(item.id, line); state.carts.set(state.currentTable, cart); renderSummaries(); speak(`${item.name} added to ${state.currentTable}`); }
    const remove = event.target.closest('[data-remove]');
    if (remove) { const cart = currentCart(); cart.delete(Number(remove.dataset.remove)); state.carts.set(state.currentTable, cart); renderSummaries(); }
    const confirm = event.target.closest('.confirm-btn'); if (confirm) confirmOrder(confirm.dataset.table);
    const cancel = event.target.closest('.cancel-btn'); if (cancel) { state.carts.set(cancel.dataset.table, new Map()); renderSummaries(); }
  });
});
