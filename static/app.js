/* المحاسب الذكي — الواجهة الأمامية (JavaScript بدون أطر عمل) */
'use strict';

// ═════════════════════ أدوات عامة ═════════════════════
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = n => Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 2 });
const money = n => `<span class="num ${n < 0 ? 'neg' : ''}">${fmt(n)}</span>`;
const fx = (n, sym) => `<span class="num">${fmt(n)}</span> <span class="muted small">${esc(sym || '')}</span>`;
const today = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };
const monthStart = () => today().slice(0, 8) + '01';
const yearStart = () => today().slice(0, 5) + '01-01';
const KIND = { sale: 'فاتورة مبيع', purchase: 'فاتورة شراء', sale_return: 'مرتجع مبيع', purchase_return: 'مرتجع شراء' };
const TYPE = { asset: 'أصول', liability: 'خصوم', equity: 'حقوق ملكية', revenue: 'إيرادات', expense: 'مصروفات' };
const SOURCE = { manual: 'قيد يدوي', receipt: 'سند قبض', payment: 'سند دفع', opening: 'قيد افتتاحي', opening_stock: 'بضاعة أول المدة', ...KIND };
const S = { settings: {}, perms: new Set(), chat: [], chatSession: null };
const can = p => S.perms.has(p);
const baseCur = () => (S.settings.currencies || []).find(c => c.is_base) || { code: '', symbol: S.settings.currency || '', rate: 1 };
const curOf = code => (S.settings.currencies || []).find(c => c.code === code) || baseCur();

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers['Content-Type'] = 'application/json'; }
  let res;
  try { res = await fetch(path, init); }
  catch { const e = new Error('لا يوجد اتصال بالخادم'); e.offline = true; throw e; }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith('/api/auth/')) { showAuth(); throw new Error(data.detail || 'سجّل الدخول'); }
  if (!res.ok) { const e = new Error(typeof data.detail === 'string' ? data.detail : 'حدث خطأ غير متوقع'); e.status = res.status; throw e; }
  if (opts.withTotal) return { data, total: +res.headers.get('X-Total-Count') || 0 };
  return data;
}
/** زر «عرض المزيد» للقوائم الطويلة */
const moreBar = (shown, total) => total > shown
  ? `<div class="row no-print" style="margin-top:10px;justify-content:center"><span class="muted small">عُرض ${fmt(shown)} من ${fmt(total)}</span>
     <button class="btn sm" data-more>عرض المزيد</button></div>` : '';

// ── مبيعات نقطة البيع المحفوظة أثناء انقطاع الاتصال ──
const OFFLINE = {
  get list() { try { return JSON.parse(localStorage.getItem('axis_offline_sales') || '[]'); } catch { return []; } },
  set list(v) { try { localStorage.setItem('axis_offline_sales', JSON.stringify(v)); } catch { } },
  add(body, total) { this.list = [...this.list, { body, total, at: new Date().toISOString(), error: null }]; },
  /** يرحّل المحفوظ. مرجع الفاتورة الفريد يمنع تكرارها لو وصلت للخادم ولم يصل الرد. */
  async sync() {
    if (OFFLINE.busy) return 0;
    OFFLINE.busy = true;
    let done = 0;
    try {
      for (const sale of OFFLINE.list) {
        try { await post('/api/invoices', sale.body); done++; OFFLINE.list = OFFLINE.list.filter(x => x.body.client_ref !== sale.body.client_ref); }
        catch (e) {
          if (e.offline) break;                       // ما زال الاتصال مقطوعاً
          OFFLINE.list = OFFLINE.list.map(x => x.body.client_ref === sale.body.client_ref ? { ...x, error: e.message } : x);
        }
      }
    } finally { OFFLINE.busy = false; }
    return done;
  },
};
const newRef = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
setInterval(() => { if (S.settings.user && OFFLINE.list.some(x => !x.error)) OFFLINE.sync().then(n => { if (n) toast(`رُحِّلت ${n} فاتورة محفوظة`); }); }, 20000);
const post = (p, b) => api(p, { method: 'POST', body: b });
const put = (p, b) => api(p, { method: 'PUT', body: b });
const del = p => api(p, { method: 'DELETE' });

function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  $('#toast').append(t);
  setTimeout(() => t.remove(), err ? 6000 : 3000);
}

/** تنفيذ عملية مع عرض الخطأ للمستخدم وتعطيل الزر أثناء التنفيذ */
async function run(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { if (btn) btn.disabled = false; }
}

function modal(title, body, { wide = false } = {}) {
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal" style="${wide ? 'width:min(980px,100%)' : ''}"><h2>${title}</h2><div class="mb">${body}</div></div>`;
  const close = () => { bg.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  bg.addEventListener('mousedown', e => { if (e.target === bg) close(); });
  document.addEventListener('keydown', onKey);
  document.body.append(bg);
  const first = $('input:not([type=checkbox]),select,textarea', bg);
  if (first) first.focus();
  $$('[data-close]', bg).forEach(b => b.onclick = close);
  return { el: bg, close };
}

function confirmBox(msg) {
  return new Promise(resolve => {
    const m = modal('تأكيد', `<p>${esc(msg)}</p><div class="foot"><button class="btn danger" data-y>نعم</button><button class="btn" data-n>إلغاء</button></div>`);
    $('[data-y]', m.el).onclick = () => { m.close(); resolve(true); };
    $('[data-n]', m.el).onclick = () => { m.close(); resolve(false); };
  });
}

/** طباعة عبر إطار مخفي — تعمل في المتصفح وفي نسخة سطح المكتب */
function printHTML(title, html) {
  const s = S.settings;
  const f = document.createElement('iframe');
  f.style.cssText = 'position:fixed;width:0;height:0;border:0;left:0;bottom:0';
  document.body.append(f);
  const d = f.contentDocument;
  d.open();
  d.write(`<!doctype html><html dir="rtl" lang="ar" data-theme="light"><head><meta charset="utf-8"><title>${esc(title)}</title>
    <link rel="stylesheet" href="/static/style.css"><style>body{background:#fff;padding:18px}.btn{display:none}</style></head><body>
    <div style="text-align:center;margin-bottom:16px"><h2 style="margin:0">${esc(s.company_name)}</h2>
    <div class="muted small">${esc(s.address || '')} ${s.phone ? '— ' + esc(s.phone) : ''} ${s.tax_number ? '— الرقم الضريبي: ' + esc(s.tax_number) : ''}</div>
    <h3>${esc(title)}</h3></div>${html}</body></html>`);
  d.close();
  f.onload = () => { f.contentWindow.focus(); f.contentWindow.print(); setTimeout(() => f.remove(), 1500); };
}

/** إيصال حراري بعرض 80 ملم — يفتح حوار الطباعة مباشرة (درج النقود يُفتح من الطابعة إن كان موصولاً) */
function printReceipt(inv, { paid = 0, change = 0, cashier = '' } = {}) {
  const s = S.settings;
  const sym = inv.currency_symbol || baseCur().symbol;
  const line = (a, b, big) => `<div class="r ${big ? 'big' : ''}"><span>${a}</span><span>${b}</span></div>`;
  const html = `<div class="c"><b>${esc(s.company_name)}</b><br>${esc(s.phone || '')}${s.tax_number ? `<br>الرقم الضريبي: ${esc(s.tax_number)}` : ''}</div>
    <hr>${line('فاتورة', inv.number)}${line('التاريخ', new Date().toLocaleString('en-GB'))}
    ${cashier ? line('الكاشير', esc(cashier)) : ''}${inv.party_name ? line('الزبون', esc(inv.party_name)) : ''}<hr>
    ${inv.lines.map(l => `<div class="it"><div>${esc(l.item_name)}${l.unit_name && l.unit_factor > 1 ? ` (${esc(l.unit_name)})` : ''}</div>
      <div class="r"><span>${fmt(l.qty)} × ${fmt(l.price)}</span><span>${fmt(l.total)}</span></div></div>`).join('')}
    <hr>${line('المجموع', fmt(inv.subtotal))}${inv.discount ? line('الحسم', fmt(inv.discount)) : ''}
    ${inv.tax_amount ? line(`الضريبة ${fmt(inv.tax_rate)}%`, fmt(inv.tax_amount)) : ''}
    ${line('الإجمالي', `${fmt(inv.total)} ${sym}`, true)}
    ${paid ? line('المدفوع', fmt(paid)) + line('الباقي', fmt(change)) : ''}
    <hr><div class="c">شكراً لزيارتكم<br>${new Date().toLocaleDateString('en-GB')}</div>`;
  const f = document.createElement('iframe');
  f.style.cssText = 'position:fixed;width:0;height:0;border:0;left:0;bottom:0';
  document.body.append(f);
  const d = f.contentDocument;
  d.open();
  d.write(`<!doctype html><html dir="rtl" lang="ar"><head><meta charset="utf-8"><title>إيصال ${inv.number}</title><style>
    @page { size: 80mm auto; margin: 3mm; }
    body { font-family: "Cairo", Tahoma, sans-serif; font-size: 12px; width: 74mm; margin: 0; color: #000; background: #fff; }
    hr { border: 0; border-top: 1px dashed #000; margin: 4px 0; }
    .c { text-align: center; } .r { display: flex; justify-content: space-between; }
    .big { font-size: 15px; font-weight: 700; } .it { margin: 2px 0; }
  </style></head><body>${html}</body></html>`);
  d.close();
  f.onload = () => { f.contentWindow.focus(); f.contentWindow.print(); setTimeout(() => f.remove(), 1500); };
}

/** إعدادات الطباعة لهذا الجهاز (تُحفظ في المتصفح لأن لكل كاشير طابعته) */
const DEV = {
  get mode() { try { return localStorage.getItem('printMode') || 'browser'; } catch { return 'browser'; } },
  set mode(v) { try { localStorage.setItem('printMode', v); } catch { } },
  get agent() { try { return localStorage.getItem('agentUrl') || 'http://127.0.0.1:9777'; } catch { return 'http://127.0.0.1:9777'; } },
  set agent(v) { try { localStorage.setItem('agentUrl', v); } catch { } },
};
async function agentCall(path, body) {
  let res;
  try {
    res = await fetch(DEV.agent + path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch { throw new Error('وكيل الطباعة لا يعمل على هذا الجهاز — شغّل run_agent.bat'); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'خطأ من وكيل الطباعة');
  return data;
}
/** يطبع إيصال الفاتورة بالطريقة المضبوطة لهذا الجهاز، ويفتح الدرج عند الدفع النقدي */
async function printSale(inv, { paid = 0, change = 0, drawer = false } = {}) {
  const mode = DEV.mode;
  if (mode === 'server') return post(`/api/devices/print/${inv.id}`, { paid, change, drawer });
  if (mode === 'agent') {
    const job = await api(`/api/devices/job/${inv.id}?paid=${paid}&change=${change}&drawer=${drawer}`);
    return agentCall('/raw', { data: job.data });
  }
  return printReceipt(inv, { paid, change, cashier: S.settings.user.full_name });
}
async function openDrawer() {
  if (DEV.mode === 'agent') return agentCall('/drawer', {});
  if (DEV.mode === 'server') return post('/api/devices/drawer');
  throw new Error('فتح الدرج يحتاج الطباعة المباشرة (خادم أو وكيل) — اضبطها من الإعدادات');
}
async function readScale() {
  return (DEV.mode === 'agent' ? await agentCall('/scale') : await api('/api/devices/scale')).weight;
}
/** شاشة الزبون: نافذة ثانية تقرأ السلة من التخزين المحلي */
function pushDisplay(state) { try { localStorage.setItem('axis_display', JSON.stringify({ ...state, t: Date.now() })); } catch { } }

/** عارض Markdown مبسّط وآمن (يهرّب النص أولاً) لردود المساعد */
function md(src) {
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  const lines = String(src || '').split('\n');
  let out = '', list = null;
  const closeList = () => { if (list) { out += `</${list}>`; list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\s*\|.*\|\s*$/.test(l)) {
      closeList();
      const rowsTxt = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rowsTxt.push(lines[i++]);
      i--;
      const cells = r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      const body = rowsTxt.filter(r => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
      out += '<div class="table-wrap"><table><thead><tr>' + cells(body[0]).map(c => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>' +
        body.slice(1).map(r => '<tr>' + cells(r).map(c => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>';
      continue;
    }
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { closeList(); out += `<h${m[1].length + 1}>${inline(m[2])}</h${m[1].length + 1}>`; }
    else if ((m = l.match(/^\s*[-*•]\s+(.*)/))) { if (list !== 'ul') { closeList(); out += '<ul>'; list = 'ul'; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== 'ol') { closeList(); out += '<ol>'; list = 'ol'; } out += `<li>${inline(m[1])}</li>`; }
    else if (!l.trim()) closeList();
    else { closeList(); out += `<p>${inline(l)}</p>`; }
  }
  closeList();
  return out;
}

function accountOptions(accounts, { leafOnly = true, selected = null, filter = null } = {}) {
  return accounts.filter(a => (!leafOnly || !a.is_group) && (!filter || filter(a)))
    .map(a => `<option value="${a.id}" ${a.id == selected ? 'selected' : ''}>${esc(a.code)} — ${esc(a.name)}</option>`).join('');
}
const cashAccounts = accs => accs.filter(a => !a.is_group && a.is_cash);
const whOptions = (selected, activeOnly = true) => (S.settings.warehouses || []).filter(w => !activeOnly || w.active)
  .map(w => `<option value="${w.id}" ${w.id == selected ? 'selected' : ''}>${esc(w.name)}</option>`).join('');
const curOptions = selected => (S.settings.currencies || [])
  .map(c => `<option value="${c.code}" ${c.code === (selected || baseCur().code) ? 'selected' : ''}>${esc(c.name)} (${esc(c.symbol)})</option>`).join('');
const filterRows = (input, root) => input.oninput = () => $$('tbody tr', root).forEach(tr => tr.style.display = (tr.dataset.name || '').includes(input.value) ? '' : 'none');

/** حقل عملة + سعر صرف متزامنان. يعيد دالة تقرأ القيمة الحالية. */
function bindCurrency(sel, rateInp, onChange) {
  const sync = () => {
    const c = curOf(sel.value);
    rateInp.value = c.is_base ? 1 : c.rate;
    rateInp.disabled = !!c.is_base;
    rateInp.closest('label').style.display = c.is_base ? 'none' : '';
  };
  sel.onchange = () => { const old = +rateInp.value || 1; sync(); onChange && onChange(old, +rateInp.value || 1); };
  rateInp.oninput = () => onChange && onChange(null, +rateInp.value || 1);
  sync();
  return () => ({ currency: sel.value, rate: +rateInp.value || 1 });
}

// ═════════════════════ الدخول ═════════════════════
async function showAuth() {
  const st = await fetch('/api/auth/status').then(r => r.json()).catch(() => ({}));
  if (st.user) return start();
  document.body.classList.add('auth-mode');
  const setup = st.needs_setup;
  let box = $('#auth');
  if (!box) { box = document.createElement('div'); box.id = 'auth'; document.body.append(box); }
  box.innerHTML = `<form class="auth-card card" autocomplete="on">
    <div class="brand" style="padding:0 0 12px;justify-content:center"><div class="logo">م</div><div><b style="color:var(--text)">المحاسب الذكي</b><small class="muted">${esc(st.company_name || '')}</small></div></div>
    ${setup ? '<p class="small muted" style="text-align:center">مرحباً! أنشئ حساب المدير للبدء. هذا الحساب يملك كل الصلاحيات ويمكنه إضافة المستخدمين.</p>' : '<h3 style="justify-content:center">تسجيل الدخول</h3>'}
    ${setup ? '<label class="f">الاسم الكامل<input id="au-full" required></label>' : ''}
    <label class="f">اسم المستخدم<input id="au-user" autocomplete="username" required></label>
    <label class="f">كلمة المرور<input id="au-pass" type="password" autocomplete="${setup ? 'new-password' : 'current-password'}" required></label>
    ${setup ? '<label class="f">تأكيد كلمة المرور<input id="au-pass2" type="password" autocomplete="new-password" required></label>' : ''}
    <button class="btn primary" id="au-go" style="justify-content:center;margin-top:6px">${setup ? 'إنشاء الحساب والدخول' : 'دخول'}</button>
    <div id="au-err" class="neg small" style="text-align:center;min-height:20px"></div></form>`;
  $('#au-user').focus();
  $('form', box).onsubmit = e => {
    e.preventDefault();
    run($('#au-go'), async () => {
      $('#au-err').textContent = '';
      try {
        if (setup && $('#au-pass').value !== $('#au-pass2').value) throw new Error('كلمتا المرور غير متطابقتين');
        await post(setup ? '/api/auth/setup' : '/api/auth/login',
          { username: $('#au-user').value, password: $('#au-pass').value, full_name: setup ? $('#au-full').value : undefined });
        box.remove();
        document.body.classList.remove('auth-mode');
        start();
      } catch (err) { $('#au-err').textContent = err.message; }
    });
  };
}

function userMenu() {
  const u = S.settings.user;
  const m = modal(`${esc(u.full_name)}`, `<p class="small muted">اسم المستخدم: ${esc(u.username)} — الدور: ${esc(u.role_label)}</p>
    <h3>تغيير كلمة المرور</h3><div class="form-grid"><label class="f">الحالية<input type="password" id="pw-old"></label>
    <label class="f">الجديدة<input type="password" id="pw-new"></label></div>
    <div class="foot"><button class="btn primary" id="pw-save">حفظ كلمة المرور</button><button class="btn danger" id="lo"><i class="i i-logout"></i> تسجيل الخروج</button><button class="btn" data-close>إغلاق</button></div>`);
  $('#pw-save').onclick = e => run(e.target, async () => { await post('/api/auth/password', { old_password: $('#pw-old').value, new_password: $('#pw-new').value }); toast('تم تغيير كلمة المرور'); m.close(); });
  $('#lo').onclick = async () => {
    await post('/api/auth/logout').catch(() => {});
    m.close();
    Object.assign(S, { settings: {}, perms: new Set(), chat: [], chatSession: null });
    history.replaceState(null, '', location.pathname);
    showAuth();
  };
}

// ═════════════════════ الموجّه ═════════════════════
const PAGES = {};
const TITLES = {
  dashboard: 'لوحة التحكم', assistant: 'المساعد الذكي', invoices: 'سجل الفواتير', vouchers: 'سندات القبض والدفع',
  journal: 'قيود اليومية', accounts: 'دليل الحسابات', items: 'المواد والمخزون', reports: 'التقارير المالية', settings: 'الإعدادات',
  warehouses: 'المستودعات والتحويل', opening: 'الأرصدة الافتتاحية', users: 'المستخدمون والصلاحيات',
  closing: 'الإقفال المالي', audit: 'سجل التدقيق', pos: 'نقطة البيع', shifts: 'ورديات الكاشير',
  pricing: 'الأسعار والعروض', labels: 'ملصقات الباركود', stocktake: 'الجرد الفعلي', orders: 'أوامر الشراء',
  checks: 'الشيكات والأوراق التجارية', assets: 'الأصول الثابتة', hr: 'الموظفون والرواتب', centers: 'مراكز الكلفة',
  floor: 'الصالة والطلبات', order: 'الطلب', kitchen: 'شاشة المطبخ', rsetup: 'إعداد المطعم',
};
// الصلاحية المطلوبة لكل صفحة (أي واحدة منها تكفي)
const PAGE_PERMS = {
  dashboard: ['reports'], assistant: ['ai'], invoices: ['sales', 'purchases', 'reports'], vouchers: ['vouchers'], journal: ['journal'],
  accounts: ['accounts', 'reports'], items: ['items', 'purchases', 'sales'], reports: ['reports'], settings: ['settings'],
  warehouses: ['warehouses'], opening: ['opening'], users: ['users'], parties: ['parties'],
  closing: ['period'], audit: ['audit'], pos: ['sales'], shifts: ['sales'],
  pricing: ['items'], labels: ['items'], stocktake: ['warehouses'], orders: ['purchases'],
  checks: ['checks'], assets: ['assets'], hr: ['hr'], centers: ['accounts', 'reports'],
  floor: ['restaurant'], order: ['restaurant'], kitchen: ['kitchen', 'restaurant'], rsetup: ['settings', 'items'],
};
const pageAllowed = (page, arg) => {
  if (page === 'invoice') return can(arg && arg.startsWith('purchase') ? 'purchases' : 'sales');
  return (PAGE_PERMS[page] || []).some(can);
};

async function route() {
  let [page, arg] = location.hash.replace(/^#\/?/, '').split('/');
  if (!page || !pageAllowed(page, arg)) {
    const first = $$('#nav a').find(a => a.style.display !== 'none');
    if (!first) { $('#app').innerHTML = '<div class="card empty">لا توجد صلاحيات لهذا المستخدم، راجع المدير.</div>'; return; }
    return go(`#/${first.dataset.page}${first.dataset.kind ? '/' + first.dataset.kind : ''}`);
  }
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page && (!a.dataset.kind || a.dataset.kind === arg)));
  $('#sidebar').classList.remove('open');
  const title = page === 'invoice' ? KIND[arg] : page === 'parties' ? (arg === 'supplier' ? 'الموردون' : 'العملاء') : TITLES[page];
  $('#page-title').textContent = title || '';
  document.title = `${title || ''} — المحاسب الذكي`;
  const app = $('#app');
  app.onkeydown = null;
  if (S.timer) { clearInterval(S.timer); S.timer = null; }
  app.innerHTML = '<div class="empty">جارِ التحميل…</div>';
  try { await PAGES[page](app, arg); }
  catch (e) { app.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; }
}
const go = h => { if (location.hash === h) route(); else location.hash = h; };

$('#nav').addEventListener('click', e => {
  const a = e.target.closest('a[data-page]');
  if (a) go(`#/${a.dataset.page}${a.dataset.kind ? '/' + a.dataset.kind : ''}`);
});
window.addEventListener('hashchange', () => { if (S.settings.user) route(); });

async function loadSettings() {
  S.settings = await api('/api/settings');
  S.perms = new Set(S.settings.user.permissions);
  $('#company-name').textContent = S.settings.company_name;
  $('#user-btn').textContent = `${S.settings.user.full_name}`;
  const rest = S.settings.restaurant_mode === '1';
  $$('#nav a').forEach(a => {
    a.style.display = pageAllowed(a.dataset.page, a.dataset.kind) && (a.dataset.mode !== 'restaurant' || rest) ? '' : 'none';
  });
  $$('#nav .group').forEach(g => {  // أخفِ عنوان المجموعة إن اختفت كل عناصرها
    let n = g.nextElementSibling, any = false;
    while (n && !n.classList.contains('group')) { if (n.style.display !== 'none') any = true; n = n.nextElementSibling; }
    g.style.display = any ? '' : 'none';
  });
}

// ═════════════════════ لوحة التحكم ═════════════════════
PAGES.dashboard = async app => {
  const d = await api('/api/dashboard');
  const cur = esc(S.settings.currency || '');
  const kpi = (t, v, s = '', cls = '') => `<div class="card kpi ${cls}"><span class="t">${t}</span><span class="v">${money(v)}</span><span class="s">${s}</span></div>`;
  app.innerHTML = `
    <div class="row no-print" style="margin-bottom:16px">
      ${can('sales') ? `<button class="btn primary" onclick="go('#/invoice/sale')"><i class="i i-receipt"></i> فاتورة مبيع جديدة</button>` : ''}
      ${can('purchases') ? `<button class="btn" onclick="go('#/invoice/purchase')"><i class="i i-inbox"></i> فاتورة شراء</button>` : ''}
      ${can('vouchers') ? `<button class="btn" onclick="go('#/vouchers')"><i class="i i-cash"></i> سند قبض / دفع</button>` : ''}
      <span class="sp"></span>
      ${can('ai') ? `<button class="btn ai" onclick="go('#/assistant')">اسأل المساعد الذكي</button>` : ''}
    </div>
    <div class="grid g4">
      ${kpi('مبيعات اليوم', d.today.total, `${d.today.count} فاتورة · ربح ${fmt(d.today.profit)}`, 'hl')}
      ${kpi('مبيعات الشهر', d.month.total, `${d.month.count} فاتورة · مجمل ربح ${fmt(d.month.profit)}`)}
      ${kpi('صافي ربح الشهر', d.month_net_profit, `بعد المصاريف (${fmt(d.month_expenses)})`)}
      ${kpi('السيولة (صناديق + بنوك)', d.cash, cur)}
      ${kpi('ذمم العملاء (لنا)', d.receivables, cur)}
      ${kpi('ذمم الموردين (علينا)', d.payables, cur)}
      ${kpi('قيمة المخزون', d.stock_value, 'بمتوسط التكلفة')}
      ${kpi('مواد تحت حد الطلب', d.low_stock.length, d.low_stock.length ? 'تحتاج إعادة طلب' : 'المخزون جيد')}
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card chart"><h3><i class="i i-chart"></i> المبيعات والمشتريات — آخر 6 أشهر<span class="sp"></span>
        <span class="legend"><span><i style="background:var(--primary)"></i>مبيعات</span><span><i style="background:var(--accent)"></i>مشتريات</span><span><i style="background:var(--ok)"></i>ربح</span></span></h3>
        ${trendChart(d.trend)}</div>
      ${can('ai') ? `<div class="card insight"><h3>التحليل الذكي<span class="sp"></span><button class="btn sm ai" id="ins-btn">حلّل وضعي المالي</button></h3>
        <div class="md" id="ins">اضغط الزر ليقرأ المساعد أرقامك ويقدّم لك ملاحظات وتوصيات عملية.</div></div>` : '<div></div>'}
    </div>
    <div class="grid g3" style="margin-top:16px">
      <div class="card" style="grid-column: span 2"><h3><i class="i i-clock"></i> آخر الفواتير</h3>${invoiceTable(d.recent, true)}</div>
      <div>
        <div class="card"><h3><i class="i i-star"></i> الأكثر مبيعاً هذا الشهر</h3>
          ${d.top_items.length ? `<table><tbody>${d.top_items.map(t => `<tr><td>${esc(t.name)}</td><td class="muted small">${fmt(t.qty)}</td><td>${money(t.total)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">لا مبيعات بعد</div>'}</div>
        <div class="card"><h3>مواد ناقصة</h3>
          ${d.low_stock.length ? `<table><tbody>${d.low_stock.map(i => `<tr><td>${esc(i.name)}</td><td><span class="badge low">${fmt(i.qty)} ${esc(i.unit)}</span></td></tr>`).join('')}</tbody></table>` : '<div class="empty">لا توجد مواد ناقصة </div>'}</div>
      </div>
    </div>`;
  bindInvoiceRows(app);
  if ($('#ins-btn')) $('#ins-btn').onclick = e => run(e.target, async () => {
    $('#ins').innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
    try { $('#ins').innerHTML = md((await post('/api/ai/insights')).text); }
    catch (err) { $('#ins').textContent = err.message; throw err; }
  });
};

function trendChart(trend) {
  const months = [];
  const d = new Date(today());
  for (let i = 5; i >= 0; i--) { const x = new Date(d.getFullYear(), d.getMonth() - i, 1); months.push(`${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}`); }
  const by = Object.fromEntries(trend.map(t => [t.month, t]));
  const data = months.map(m => ({ m, s: by[m]?.sales || 0, p: by[m]?.purchases || 0, r: by[m]?.profit || 0 }));
  const max = Math.max(1, ...data.flatMap(x => [x.s, x.p]));
  const W = 600, H = 220, pad = 28, gw = (W - pad) / 6, bw = gw / 4;
  let bars = '';
  data.forEach((x, i) => {
    const gx = W - (i + 1) * gw;  // الأحدث على اليسار (اتجاه القراءة من اليمين)
    const h = v => Math.max(0, (v / max) * (H - 50));
    [[x.s, 'var(--primary)'], [x.p, 'var(--accent)'], [Math.max(x.r, 0), 'var(--ok)']].forEach(([v, c], k) => {
      bars += `<rect x="${gx + bw * (k + 0.5)}" y="${H - 24 - h(v)}" width="${bw - 3}" height="${h(v)}" rx="3" fill="${c}"><title>${fmt(v)}</title></rect>`;
    });
    bars += `<text x="${gx + gw / 2}" y="${H - 6}" text-anchor="middle">${x.m.slice(5)}/${x.m.slice(2, 4)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line x1="0" x2="${W}" y1="${H - 24}" y2="${H - 24}" stroke="var(--border)"/>${bars}</svg>`;
}

const invTotal = i => i.is_base === 0 || (i.currency && i.currency !== baseCur().code) ? fx(i.total, i.currency_symbol) : money(i.total);

function invoiceTable(list, compact = false) {
  if (!list.length) return '<div class="empty">لا توجد فواتير</div>';
  return `<div class="table-wrap"><table><thead><tr><th>الرقم</th><th>النوع</th><th>التاريخ</th><th>الطرف</th>${compact ? '' : '<th>الدفع</th>'}<th>الإجمالي</th>${compact || !can('reports') ? '' : '<th>الربح</th>'}</tr></thead><tbody>
    ${list.map(i => `<tr class="clickable" data-inv="${i.id}"><td>${i.number}</td><td><span class="badge ${i.kind}">${KIND[i.kind]}</span></td><td>${i.date}</td>
      <td>${esc(i.party_name || 'نقدي')}</td>${compact ? '' : `<td>${i.payment === 'cash' ? 'نقداً' : 'آجل'}</td>`}<td>${invTotal(i)}</td>
      ${compact || !can('reports') ? '' : `<td>${i.profit == null ? '<span class="muted">—</span>' : money(i.profit)}</td>`}</tr>`).join('')}
  </tbody></table></div>`;
}
function bindInvoiceRows(root, after) {
  $$('[data-inv]', root).forEach(tr => tr.onclick = () => showInvoice(+tr.dataset.inv, after));
}

// ═════════════════════ الفاتورة ═════════════════════
PAGES.invoice = async (app, kind = 'sale', editing = null) => {
  if (editing) kind = editing.kind;
  if (!KIND[kind]) kind = 'sale';
  const partyKind = kind.startsWith('sale') ? 'customer' : 'supplier';
  const isSale = kind.startsWith('sale');
  const whs = (S.settings.warehouses || []).filter(w => w.active);
  let [items, parties, accs] = await Promise.all([api(`/api/items?warehouse_id=${whs[0]?.id || ''}`), api(`/api/parties?kind=${partyKind}`), api('/api/accounts')]);
  const withUnits = async list => {   // الوحدات الإضافية تُحمَّل مرة واحدة لكل مادة لها وحدات
    const all = await api('/api/pos/init').catch(() => null);
    if (all) list.forEach(i => { const m = all.items.find(x => x.id === i.id); if (m) i.units = m.units; });
    return list;
  };
  if (can('sales') || can('purchases')) items = await withUnits(items);
  // مركز الكلفة والمندوب يظهران فقط عند وجود بيانات لهما
  const [centers, salespeople] = await Promise.all([
    api('/api/cost-centers').catch(() => []),
    isSale ? api('/api/employees').catch(() => []) : Promise.resolve([]),
  ]);
  const vatOn = S.settings.vat_enabled;
  const st = { lines: [] };
  const findItem = txt => {
    const t = (txt || '').trim();
    if (!t) return null;
    return items.find(i => `${i.code} - ${i.name}` === t || i.code === t || i.barcode === t || i.name === t) || null;
  };
  let getCur = () => ({ currency: editing ? editing.currency : baseCur().code, rate: editing ? editing.rate : 1 });
  const unitsOf = it => it.units || [{ name: it.unit, factor: 1, price: it.sale_price, base: true }];
  const defaultPrice = it => +((isSale ? it.sale_price : it.avg_cost) / getCur().rate).toFixed(2);
  const hasCur = (S.settings.currencies || []).length > 1;

  app.innerHTML = `
    ${editing ? `<div class="card" style="border-color:var(--warn)"><b><i class="i i-edit"></i> تعديل ${KIND[kind]} رقم ${editing.number}</b>
      <span class="muted small">— سيُعاد حساب المخزون والقيد المحاسبي، ويبقى رقم الفاتورة كما هو، ويُسجَّل التعديل في سجل التدقيق.</span>
      <button class="btn sm" style="margin-inline-start:10px" onclick="history.back()">إلغاء التعديل</button></div>` : ''}
    <div class="card">
      <div class="form-grid">
        <label class="f">التاريخ<input type="date" id="i-date" value="${editing ? editing.date : today()}"></label>
        <label class="f">طريقة الدفع<select id="i-pay"><option value="cash">نقداً</option><option value="credit">آجل (على الحساب)</option></select></label>
        <label class="f">${partyKind === 'customer' ? 'العميل' : 'المورد'}
          <div class="row" style="flex-wrap:nowrap"><select id="i-party" style="flex:1"><option value="">— ${isSale ? 'زبون نقدي' : 'بدون'} —</option>
          ${parties.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select>
          ${can('parties') ? '<button class="btn sm" id="i-newparty" title="إضافة جديد"><i class="i i-plus"></i></button>' : ''}</div></label>
        <label class="f" id="i-cash-wrap">الصندوق / البنك<select id="i-cash">${accountOptions(cashAccounts(accs))}</select></label>
        <label class="f" ${whs.length > 1 ? '' : 'style="display:none"'}>المستودع<select id="i-wh">${whOptions(whs[0]?.id)}</select></label>
        <label class="f" ${hasCur ? '' : 'style="display:none"'}>العملة<select id="i-cur">${curOptions()}</select></label>
        <label class="f">سعر الصرف<input type="number" id="i-rate" step="any" min="0"></label>
        <label class="f" id="i-cc-wrap" style="display:none">مركز الكلفة<select id="i-cc"><option value="">— بدون —</option></select></label>
        <label class="f" id="i-sp-wrap" style="display:none">المندوب<select id="i-sp"><option value="">— بدون —</option></select></label>
      </div>
      ${kind === 'purchase' && !editing ? `<div class="drop no-print" id="scan" style="margin-top:14px"><i class="i i-camera"></i> <b>قراءة فاتورة المورد بالذكاء الاصطناعي</b> — اسحب صورة أو PDF هنا أو اضغط للاختيار
        <input type="file" id="scan-file" accept="image/*,application/pdf" hidden></div>` : ''}
    </div>
    <div class="card">
      <datalist id="items-dl"></datalist>
      <div class="table-wrap"><table class="lines-table"><thead><tr><th style="width:36px">#</th><th>المادة (اكتب الاسم أو الرمز أو الباركود)</th><th style="width:90px">المتوفر</th>
        <th style="width:110px">الوحدة</th><th style="width:110px">الكمية</th><th style="width:130px">السعر</th><th style="width:130px">الإجمالي</th><th style="width:40px"></th></tr></thead><tbody id="i-lines"></tbody></table></div>
      <div class="row" style="margin-top:14px;align-items:flex-start">
        <label class="f" style="flex:1;min-width:220px">ملاحظات<textarea id="i-notes" rows="2"></textarea></label>
        <div class="totals">
          <span>المجموع</span><span id="t-sub" class="num">0</span>
          <span>الحسم</span><input type="number" id="i-disc" min="0" step="any" value="0" style="width:120px">
          <span class="tax-row">الضريبة %</span><input class="tax-row" type="number" id="i-tax" min="0" max="100" step="any" value="${editing ? editing.tax_rate : (vatOn ? S.settings.vat_rate : 0)}" style="width:120px">
          <span class="tax-row">مبلغ الضريبة</span><span id="t-tax" class="num tax-row">0</span>
          <span>الإجمالي</span><span id="t-total" class="num big">0</span>
          <span class="muted small fx-row">يعادل بالعملة الأساسية</span><span id="t-base" class="num small fx-row">0</span>
          ${kind === 'sale' && can('reports') ? '<span class="muted small">الربح المتوقع</span><span id="t-profit" class="num small">0</span>' : ''}
        </div>
      </div>
      <div class="row" style="margin-top:16px"><button class="btn primary" id="i-save"><i class="i i-save"></i> ${editing ? 'حفظ التعديل' : 'حفظ وترحيل'} (Ctrl+S)</button>
        <button class="btn" id="i-clear">مسح</button><span class="sp"></span><span class="muted small">يُنشأ القيد المحاسبي وتُحدَّث الكميات تلقائياً</span></div>
    </div>`;

  const fillDatalist = () => { $('#items-dl').innerHTML = items.map(i => `<option value="${esc(i.code)} - ${esc(i.name)}">`).join(''); };
  const showTax = on => $$('.tax-row', app).forEach(e => e.style.display = on ? '' : 'none');
  showTax(vatOn);
  const addEmpty = () => { if (!st.lines.length || st.lines[st.lines.length - 1].text) st.lines.push({ text: '', item_id: null, qty: 1, price: 0, unit_factor: 1, unit_name: null }); };
  const recalc = () => {
    const { rate, currency } = getCur();
    const sub = st.lines.reduce((s, l) => s + (l.item_id ? l.qty * l.price : 0), 0);
    const disc = +$('#i-disc').value || 0;
    const net = sub - disc;
    const tax = Math.round(net * (+$('#i-tax').value || 0)) / 100;
    const sym = curOf(currency).symbol;
    $('#t-sub').textContent = fmt(sub);
    $('#t-tax').textContent = fmt(tax);
    $('#t-total').textContent = `${fmt(net + tax)} ${sym}`;
    const foreign = !curOf(currency).is_base;
    $$('.fx-row', app).forEach(e => e.style.display = foreign ? '' : 'none');
    $('#t-base').textContent = `${fmt((net + tax) * rate)} ${baseCur().symbol}`;
    if ($('#t-profit')) {
      const cost = st.lines.reduce((s, l) => { const it = items.find(i => i.id === l.item_id); return s + (it ? l.qty * (l.unit_factor || 1) * it.avg_cost : 0); }, 0);
      $('#t-profit').textContent = fmt(net * rate - cost);
    }
    st.lines.forEach((l, i) => { const c = $(`#lt-${i}`); if (c) c.textContent = fmt(l.qty * l.price); });
  };
  const draw = () => {
    addEmpty();
    $('#i-lines').innerHTML = st.lines.map((l, i) => {
      const it = items.find(x => x.id === l.item_id);
      const unmatched = l.text && !it;
      const us = it ? unitsOf(it) : [];
      return `<tr><td class="muted">${i + 1}</td>
        <td><div class="row" style="flex-wrap:nowrap"><input list="items-dl" data-i="${i}" data-f="text" value="${esc(it ? `${it.code} - ${it.name}` : l.text)}" style="flex:1${unmatched ? ';border-color:var(--warn)' : ''}">
          ${unmatched && (can('items') || can('purchases')) ? `<button class="btn sm" data-create="${i}" title="إنشاء مادة جديدة بهذا الاسم"><i class="i i-plus"></i> مادة جديدة</button>` : ''}</div></td>
        <td class="small ${it && kind === 'sale' && it.qty < l.qty * (l.unit_factor || 1) ? 'neg' : 'muted'}">${it
          ? ((l.unit_factor || 1) > 1 ? `${fmt(it.qty / l.unit_factor)} ${esc(l.unit_name)}` : `${fmt(it.qty)} ${esc(it.unit)}`)
          : ''}</td>
        <td>${us.length > 1 ? `<select data-i="${i}" data-f="unit">${us.map(u => `<option value="${esc(u.name)}" ${u.name === (l.unit_name || (it ? it.unit : '')) ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select>`
          : `<span class="muted small">${it ? esc(it.unit) : ''}</span>`}</td>
        <td><input type="number" step="any" min="0" data-i="${i}" data-f="qty" value="${l.qty}"></td>
        <td><input type="number" step="any" min="0" data-i="${i}" data-f="price" value="${l.price}"></td>
        <td id="lt-${i}" class="num">${fmt(l.qty * l.price)}</td>
        <td>${l.text ? `<button class="btn sm ghost" data-rm="${i}"><i class="i i-x"></i></button>` : ''}</td></tr>
        ${it && it.track_batches && kind === 'purchase' ? `<tr class="batch-row"><td></td><td colspan="6">
          <div class="row small"><span class="muted">دفعة:</span>
            <input data-i="${i}" data-f="batch_code" value="${esc(l.batch_code || '')}" placeholder="رقم الدفعة" style="width:140px">
            <span class="muted">تنتهي في</span>
            <input type="date" data-i="${i}" data-f="batch_expiry" value="${l.batch_expiry || ''}"></div></td></tr>` : ''}`;
    }).join('');
    recalc();
  };
  const actCenters = (centers || []).filter(c => c.active);
  if (actCenters.length) {
    $('#i-cc-wrap').style.display = '';
    $('#i-cc').insertAdjacentHTML('beforeend', actCenters.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join(''));
  }
  const reps = (salespeople || []).filter(e => e.is_salesperson && e.active);
  if (reps.length) {
    $('#i-sp-wrap').style.display = '';
    $('#i-sp').insertAdjacentHTML('beforeend', reps.map(e => `<option value="${e.id}">${esc(e.name)}</option>`).join(''));
  }
  if (editing) {
    $('#i-pay').value = editing.payment;
    if (editing.cost_center_id) $('#i-cc').value = editing.cost_center_id;
    if (editing.salesperson_id) $('#i-sp').value = editing.salesperson_id;
    $('#i-party').value = editing.party_id || '';
    $('#i-wh').value = editing.warehouse_id;
    $('#i-cur').value = editing.currency;
    $('#i-disc').value = editing.discount;
    $('#i-notes').value = editing.notes || '';
    if (editing.tax_rate) showTax(true);
    st.lines = editing.lines.map(l => ({ text: `${l.item_code} - ${l.item_name}`, item_id: l.item_id, qty: l.qty,
                                        price: l.price, unit_name: l.unit_name, unit_factor: l.unit_factor || 1 }));
  }
  getCur = bindCurrency($('#i-cur'), $('#i-rate'), (oldRate, newRate) => {
    if (oldRate) st.lines.forEach(l => { l.price = +(l.price * oldRate / newRate).toFixed(2); });  // تحويل الأسعار للعملة الجديدة
    draw();
  });
  if (editing) $('#i-rate').value = editing.rate;
  $('#i-wh').onchange = () => run(null, async () => { items = await api(`/api/items?warehouse_id=${$('#i-wh').value}`); fillDatalist(); draw(); });
  $('#i-lines').addEventListener('change', e => {
    const inp = e.target.closest('[data-f]');
    if (!inp) return;
    const l = st.lines[+inp.dataset.i];
    if (inp.dataset.f === 'text') {
      l.text = inp.value;
      const it = findItem(inp.value);
      l.item_id = it ? it.id : null;
      if (it) {
        const u = unitsOf(it)[0];
        l.unit_name = u.name; l.unit_factor = u.factor; l.unit_id = u.id;
        l.price = defaultPrice(it);
        if (isSale) priceFor(l, it);        // عرض أو قائمة أسعار إن وُجدت
      }
      draw();
      const next = $(`[data-i="${inp.dataset.i}"][data-f="qty"]`);
      if (it && next) { next.focus(); next.select(); }
    } else if (inp.dataset.f === 'unit') {
      const it = items.find(x => x.id === l.item_id);
      const u = unitsOf(it).find(x => x.name === inp.value) || unitsOf(it)[0];
      l.unit_name = u.name; l.unit_factor = u.factor; l.unit_id = u.id;
      l.price = isSale ? +(u.price / getCur().rate).toFixed(2) : +((it.avg_cost * u.factor) / getCur().rate).toFixed(2);
      if (isSale) priceFor(l, it);
      draw();
    } else if (inp.dataset.f.startsWith('batch_')) {
      l[inp.dataset.f] = inp.value;
    } else { l[inp.dataset.f] = +inp.value || 0; draw(); }
  });

  /** السعر المعتمد من الخادم (عرض ← قائمة أسعار ← سعر المادة) */
  async function priceFor(l, it) {
    try {
      const party = $('#i-party').value;
      const r = await api(`/api/price?item_id=${it.id}&qty=${l.qty}&unit_factor=${l.unit_factor || 1}${l.unit_id ? `&unit_id=${l.unit_id}` : ''}${party ? `&party_id=${party}` : ''}`);
      l.price = +(r.price / getCur().rate).toFixed(2);
      l.promo = r.promo;
      draw();
    } catch { /* السعر الافتراضي يبقى كما هو */ }
  }
  $('#i-lines').addEventListener('input', e => {
    const inp = e.target.closest('[data-f="qty"],[data-f="price"]');
    if (inp) { st.lines[+inp.dataset.i][inp.dataset.f] = +inp.value || 0; recalc(); }
  });
  $('#i-lines').addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const idx = $$('#i-lines input').indexOf(e.target);
    e.target.dispatchEvent(new Event('change', { bubbles: true }));
    setTimeout(() => { const all = $$('#i-lines input'); (all[idx + 1] || all[all.length - 1])?.focus(); }, 0);
  });
  $('#i-lines').addEventListener('click', async e => {
    const rm = e.target.closest('[data-rm]');
    if (rm) { st.lines.splice(+rm.dataset.rm, 1); draw(); }
    const cr = e.target.closest('[data-create]');
    if (cr) {
      const l = st.lines[+cr.dataset.create];
      await run(cr, async () => {
        const base = l.price * getCur().rate;
        const it = await post('/api/items', { name: l.text, sale_price: isSale ? base : +(base * 1.25).toFixed(2) });
        it.stock = {}; it.qty = 0;
        items.push(it);
        fillDatalist();
        l.item_id = it.id;
        toast(`أُنشئت المادة «${it.name}»`);
        draw();
      });
    }
  });
  $('#i-disc').oninput = recalc;
  $('#i-tax').oninput = recalc;
  const syncPay = () => { $('#i-cash-wrap').style.display = $('#i-pay').value === 'cash' ? '' : 'none'; };
  $('#i-pay').onchange = syncPay;
  syncPay();
  if ($('#i-newparty')) $('#i-newparty').onclick = () => partyForm(partyKind, null, p => {
    parties.push(p);
    $('#i-party').insertAdjacentHTML('beforeend', `<option value="${p.id}">${esc(p.name)}</option>`);
    $('#i-party').value = p.id;
  });
  $('#i-clear').onclick = () => route();

  const save = () => run($('#i-save'), async () => {
    const lines = st.lines.filter(l => l.text);
    if (lines.some(l => !l.item_id)) throw new Error('توجد أسطر بمواد غير معرّفة، اخترها من القائمة أو أنشئها');
    const body = {
      kind, date: $('#i-date').value, payment: $('#i-pay').value, party_id: $('#i-party').value || null,
      cash_account_id: $('#i-cash').value || null, discount: +$('#i-disc').value || 0, notes: $('#i-notes').value,
      warehouse_id: +$('#i-wh').value, tax_rate: +$('#i-tax').value || 0, ...getCur(),
      cost_center_id: +$('#i-cc').value || null, salesperson_id: +$('#i-sp').value || null,
      lines: lines.map(l => ({ item_id: l.item_id, qty: l.qty, price: l.price,
                               unit_name: l.unit_name, unit_factor: l.unit_factor || 1,
                               batch_code: l.batch_code || null, batch_expiry: l.batch_expiry || null })),
    };
    const inv = editing ? await put(`/api/invoices/${editing.id}`, body) : await post('/api/invoices', body);
    toast(editing ? `عُدِّلت ${KIND[kind]} رقم ${inv.number}` : `حُفظت ${KIND[kind]} رقم ${inv.number}`);
    await PAGES.invoice(app, kind);
    showInvoice(inv.id);
  });
  $('#i-save').onclick = save;
  app.onkeydown = e => { if (e.ctrlKey && e.key.toLowerCase() === 's') { e.preventDefault(); save(); } };

  if (kind === 'purchase' && !editing) {
    const zone = $('#scan'), file = $('#scan-file');
    zone.onclick = () => file.click();
    zone.ondragover = e => { e.preventDefault(); };
    zone.ondrop = e => { e.preventDefault(); if (e.dataTransfer.files[0]) scan(e.dataTransfer.files[0]); };
    file.onchange = () => file.files[0] && scan(file.files[0]);
    const scan = f => run(null, async () => {
      zone.innerHTML = 'جارِ قراءة الفاتورة بالذكاء الاصطناعي…';
      const fd = new FormData();
      fd.append('file', f);
      try {
        const r = await api('/api/ai/scan-invoice', { method: 'POST', body: fd });
        if (/^\d{4}-\d{2}-\d{2}$/.test(r.date || '')) $('#i-date').value = r.date;
        const sup = parties.find(p => p.name === r.supplier_name);
        if (sup) $('#i-party').value = sup.id;
        const c = (S.settings.currencies || []).find(x => x.code === (r.currency_code || '').toUpperCase());
        if (c) { $('#i-cur').value = c.code; $('#i-cur').onchange(); }
        $('#i-disc').value = r.discount || 0;
        $('#i-notes').value = [r.invoice_number && `فاتورة المورد رقم ${r.invoice_number}`, !sup && r.supplier_name && `المورد: ${r.supplier_name}`, r.notes].filter(Boolean).join(' — ');
        st.lines = r.lines.map(l => {
          const it = items.find(i => i.id === l.matched_item_id);
          return { text: it ? `${it.code} - ${it.name}` : l.description, item_id: it ? it.id : null, qty: l.qty || 1, price: l.unit_price || 0 };
        });
        const net = st.lines.reduce((s, l) => s + l.qty * l.price, 0) - (r.discount || 0);
        if (r.tax_amount > 0 && net > 0) { showTax(true); $('#i-tax').value = Math.round(r.tax_amount / net * 10000) / 100; }
        draw();
        const miss = st.lines.filter(l => !l.item_id).length;
        zone.innerHTML = `قُرئت الفاتورة: ${r.lines.length} سطر${miss ? ` — ${miss} مادة غير موجودة، اضغط «مادة جديدة» بجانبها` : ''}. راجع البيانات ثم احفظ. ${!sup && r.supplier_name ? `<br>المورد «${esc(r.supplier_name)}» غير مسجّل — أضفه بزر الإضافة بجانب المورد.` : ''}`;
      } catch (e) { zone.innerHTML = '<b>قراءة فاتورة المورد بالذكاء الاصطناعي</b> — حاول مرة أخرى'; throw e; }
    });
  }
  fillDatalist();
  draw();
  $('#i-lines input')?.focus();
};

async function showInvoice(id, after) {
  const inv = await api(`/api/invoices/${id}`);
  const sym = inv.currency_symbol || '';
  const foreign = inv.currency && inv.currency !== baseCur().code;
  const body = `
    <div class="row small muted" style="margin-bottom:10px"><span>التاريخ: <b>${inv.date}</b></span><span>الطرف: <b>${esc(inv.party_name || 'نقدي')}</b></span>
      <span>الدفع: <b>${inv.payment === 'cash' ? 'نقداً' : 'آجل'}</b></span><span>المستودع: <b>${esc(inv.warehouse_name || '')}</b></span>
      ${foreign ? `<span>العملة: <b>${esc(inv.currency)}</b> بسعر ${fmt(inv.rate)}</span>` : ''}${inv.user_name ? `<span>بواسطة: <b>${esc(inv.user_name)}</b></span>` : ''}</div>
    <div id="inv-print"><table><thead><tr><th>#</th><th>المادة</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th></tr></thead><tbody>
      ${inv.lines.map((l, i) => `<tr><td>${i + 1}</td><td>${esc(l.item_name)}</td><td>${fmt(l.qty)} ${esc(l.unit_name || l.unit)}${l.unit_factor > 1 ? ` <span class="muted small">(${fmt(l.qty * l.unit_factor)} ${esc(l.unit)})</span>` : ''}</td><td>${money(l.price)}</td><td>${money(l.total)}</td></tr>`).join('')}
    </tbody><tfoot>
      <tr><td colspan="4">المجموع</td><td>${money(inv.subtotal)}</td></tr>
      ${inv.discount ? `<tr><td colspan="4">الحسم</td><td>${money(inv.discount)}</td></tr>` : ''}
      ${inv.tax_amount ? `<tr><td colspan="4">ضريبة القيمة المضافة ${fmt(inv.tax_rate)}%</td><td>${money(inv.tax_amount)}</td></tr>` : ''}
      <tr><td colspan="4">الإجمالي</td><td>${fx(inv.total, sym)}</td></tr>
      ${foreign ? `<tr><td colspan="4">يعادل بالعملة الأساسية</td><td>${fx(inv.total_base, baseCur().symbol)}</td></tr>` : ''}</tfoot></table>
      ${inv.notes ? `<p class="small">ملاحظات: ${esc(inv.notes)}</p>` : ''}</div>
    ${inv.kind === 'sale' && can('reports') ? `<p class="small muted">التكلفة: ${fmt(inv.cost_total)} — الربح: ${fmt(inv.profit)} ${esc(baseCur().symbol)}</p>` : ''}
    ${inv.payment === 'credit' ? `<p class="small">المسدَّد: <b>${fmt(inv.paid)}</b> — المتبقي: <b class="${inv.outstanding > 0 ? 'neg' : 'pos'}">${fmt(inv.outstanding)}</b> ${esc(baseCur().symbol)}
      ${inv.outstanding <= 0 ? '<span class="badge sale">مسدَّدة بالكامل</span>' : ''}</p>` : ''}
    ${inv.installments && inv.installments.length ? `<div class="card" style="margin-top:10px;background:var(--panel-2)">
      <b class="small"><i class="i i-calendar"></i> خطة الأقساط</b><table class="small"><thead><tr><th>#</th><th>الاستحقاق</th><th>المبلغ</th><th>المسدَّد</th><th>المتبقي</th><th>الحالة</th></tr></thead><tbody>
      ${inv.installments.map(i => `<tr><td>${i.seq}</td><td class="num">${i.due_date}</td><td class="num">${fmt(i.amount)}</td>
        <td class="num">${fmt(i.paid)}</td><td class="num">${fmt(i.remaining)}</td>
        <td><span class="badge ${i.status === 'مسدَّد' ? 'sale' : i.status === 'متأخر' ? 'low' : ''}">${i.status}</span></td></tr>`).join('')}
      </tbody></table></div>` : ''}
    <div class="foot"><button class="btn primary" data-print><i class="i i-print"></i> طباعة</button>
      <button class="btn" data-receipt title="إيصال حراري 80 ملم"><i class="i i-receipt"></i> إيصال</button>
      ${inv.payment === 'credit' && inv.kind === 'sale' && can('sales') ? '<button class="btn" data-inst><i class="i i-calendar"></i> خطة أقساط</button>' : ''}
      ${can('edit') ? '<button class="btn" data-edit><i class="i i-edit"></i> تعديل</button>' : ''}
      ${can('delete') ? '<button class="btn danger" data-del><i class="i i-trash"></i> حذف الفاتورة</button>' : ''}<button class="btn" data-close>إغلاق</button></div>`;
  const m = modal(`${KIND[inv.kind]} رقم ${inv.number}`, body);
  $('[data-print]', m.el).onclick = () => printHTML(`${KIND[inv.kind]} رقم ${inv.number}`,
    `<p>التاريخ: ${inv.date} — ${inv.kind.startsWith('sale') ? 'العميل' : 'المورد'}: ${esc(inv.party_name || 'نقدي')} — ${inv.payment === 'cash' ? 'نقداً' : 'آجل'}${S.settings.tax_number && inv.tax_amount ? ` — الرقم الضريبي: ${esc(S.settings.tax_number)}` : ''}</p>` + $('#inv-print', m.el).innerHTML);
  $('[data-receipt]', m.el).onclick = e => run(e.target, async () => { await printSale(inv); if (DEV.mode !== 'browser') toast('أُرسل الإيصال للطابعة'); });
  if ($('[data-inst]', m.el)) $('[data-inst]', m.el).onclick = () => {
    const im = modal(`خطة أقساط للفاتورة رقم ${inv.number}`, `<p class="small muted">قيمة الفاتورة ${fmt(inv.total_base)} ${esc(baseCur().symbol)}</p>
      <div class="form-grid"><label class="f">عدد الأقساط الشهرية<input type="number" min="1" max="60" id="in-count" value="${inv.installments?.length || 3}"></label>
        <label class="f">تاريخ أول قسط<input type="date" id="in-start" value="${inv.date}"></label></div>
      <p class="small muted">يقسّم النظام المبلغ على الأشهر بالتساوي ويضيف فرق التقريب للقسط الأخير. الحفظ يستبدل أي خطة سابقة.</p>
      <div class="foot"><button class="btn primary" id="in-save">حفظ الخطة</button>
        <button class="btn danger" id="in-clear">حذف الخطة</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#in-save').onclick = e => run(e.target, async () => {
      await put(`/api/invoices/${inv.id}/installments`, { count: +$('#in-count').value || 1, start: $('#in-start').value });
      im.close(); m.close(); toast('حُفظت خطة الأقساط'); showInvoice(inv.id, after);
    });
    $('#in-clear').onclick = e => run(e.target, async () => {
      await put(`/api/invoices/${inv.id}/installments`, { plan: [] });
      im.close(); m.close(); toast('حُذفت الخطة'); showInvoice(inv.id, after);
    });
  };
  if ($('[data-edit]', m.el)) $('[data-edit]', m.el).onclick = () => {
    m.close();
    $('#page-title').textContent = `تعديل ${KIND[inv.kind]} رقم ${inv.number}`;
    PAGES.invoice($('#app'), inv.kind, inv);
  };
  if ($('[data-del]', m.el)) $('[data-del]', m.el).onclick = async e => {
    if (!await confirmBox('سيتم حذف الفاتورة وعكس أثرها على المخزون والحسابات. متابعة؟')) return;
    await run(e.target, async () => { await del(`/api/invoices/${id}`); toast('حُذفت الفاتورة'); m.close(); after ? after() : route(); });
  };
}

// ═════════════════════ نقطة البيع ═════════════════════
PAGES.pos = async (app, whArg) => {
  let d = await api('/api/pos/init' + (whArg ? `?warehouse_id=${whArg}` : ''));
  const cart = [];
  let customer = '', keepThanks = false;
  const vatRate = d.settings.vat_enabled === '1' ? +d.settings.vat_rate || 0 : 0;

  app.innerHTML = `<div class="pos-layout">
    <div class="pos-side">
      <div class="card pos-shift" id="pos-shift"></div>
      <div class="card" style="flex:1;display:flex;flex-direction:column;min-height:0">
        <div class="row" style="margin-bottom:8px">
          <select id="pos-cust" style="flex:1"><option value="">زبون نقدي</option>
            ${d.customers.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select>
          <select id="pos-wh">${d.warehouses.map(w => `<option value="${w.id}" ${w.id === d.warehouse_id ? 'selected' : ''}>${esc(w.name)}</option>`).join('')}</select>
        </div>
        <div class="table-wrap" style="flex:1;overflow-y:auto"><table class="pos-cart"><thead><tr><th>المادة</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th><th></th></tr></thead>
          <tbody id="pos-lines"></tbody></table></div>
        <div class="pos-totals" id="pos-totals"></div>
        <div class="row" style="margin-top:8px">
          <button class="btn primary pos-pay" id="pos-pay"><i class="i i-cash"></i> الدفع (F2)</button>
          <button class="btn" id="pos-clear">مسح السلة</button>
          <button class="btn" id="pos-last" title="آخر فاتورة"><i class="i i-receipt"></i></button>
          <button class="btn" id="pos-hold" title="تعليق الفاتورة لخدمة زبون آخر"><i class="i i-pause"></i></button>
          <button class="btn" id="pos-held" title="الفواتير المعلّقة"><i class="i i-folder"></i><span id="pos-held-n"></span></button>
          <button class="btn" id="pos-scale" title="قراءة الوزن من الميزان للسطر الأخير"><i class="i i-scale"></i></button>
          <button class="btn" id="pos-drawer" title="فتح درج النقود"><i class="i i-cash"></i></button>
          <button class="btn" id="pos-display" title="شاشة الزبون (نافذة ثانية)"><i class="i i-monitor"></i></button>
        </div>
      </div>
    </div>
    <div class="pos-main">
      <div class="card"><div class="row">
        <input id="pos-scan" placeholder="امسح الباركود أو اكتب اسم المادة ثم Enter…" style="flex:1;font-size:16px" autocomplete="off">
        <button class="btn" id="pos-refresh" title="تحديث"><i class="i i-refresh"></i></button></div>
        <div class="muted small" id="pos-hint">الباركود الموزون يُقرأ تلقائياً (بادئة ${esc(d.settings.scale_prefix || '')}). F2 للدفع، F4 للزبون.</div>
      </div>
      <div class="pos-grid" id="pos-grid"></div>
    </div></div>`;

  const shiftBox = () => {
    const sh = d.shift;
    $('#pos-shift').innerHTML = sh
      ? `<div class="row"><b>وردية ${sh.number}</b><span class="badge sale">مفتوحة</span><span class="sp"></span>
          <span class="small muted">${esc(sh.cash_name)} · منذ ${sh.opened_at.slice(11, 16)}</span></div>
         <div class="row small" style="margin-top:6px"><span>مبيعات: <b>${fmt(sh.sales_total)}</b> (${sh.sales_count})</span>
          <span>المتوقع بالصندوق: <b>${fmt(sh.expected)}</b></span><span class="sp"></span>
          <button class="btn sm danger" id="pos-close-shift">إغلاق الوردية</button></div>`
      : `<div class="row"><b>لا توجد وردية مفتوحة</b><span class="sp"></span>
          <button class="btn sm primary" id="pos-open-shift">فتح وردية</button></div>
         <div class="small muted">افتح وردية لتتبّع مبيعاتك ورصيد صندوقك، أو تابع البيع مباشرة.</div>`;
    const pend = OFFLINE.list;
    if (pend.length) {
      $('#pos-shift').insertAdjacentHTML('beforeend', `<div class="row small" style="margin-top:6px;color:var(--warn)">
        <b>${pend.length}</b> فاتورة محفوظة على هذا الجهاز بانتظار الترحيل (${fmt(pend.reduce((a, x) => a + x.total, 0))})
        <span class="sp"></span><button class="btn sm" id="pos-sync">ترحيل الآن</button><button class="btn sm" id="pos-pending">عرض</button></div>`);
      $('#pos-sync').onclick = e => run(e.target, async () => {
        const n = await OFFLINE.sync();
        toast(n ? `رُحِّلت ${n} فاتورة` : 'تعذر الترحيل — تحقق من الاتصال', !n);
        await refresh().catch(() => shiftBox());
      });
      $('#pos-pending').onclick = () => {
        const m = modal('فواتير بانتظار الترحيل', `<table class="small"><thead><tr><th>الوقت</th><th>الأصناف</th><th>المبلغ</th><th>الحالة</th><th></th></tr></thead><tbody>
          ${OFFLINE.list.map((x, i) => `<tr><td class="num">${x.at.slice(11, 16)}</td><td>${x.body.lines.length}</td><td class="num">${fmt(x.total)}</td>
            <td class="${x.error ? 'neg' : 'muted'}">${x.error ? esc(x.error) : 'بانتظار الاتصال'}</td>
            <td>${x.error ? `<button class="btn sm danger" data-drop="${i}">تجاهل</button>` : ''}</td></tr>`).join('')}</tbody></table>
          <p class="small muted">الفاتورة التي يرفضها الخادم (مثلاً نفدت الكمية) تبقى هنا حتى تراجعها — أعد إدخالها يدوياً ثم اضغط «تجاهل».</p>
          <div class="foot"><button class="btn" data-close>إغلاق</button></div>`, { wide: true });
        $$('[data-drop]', m.el).forEach(b => b.onclick = async () => {
          if (!await confirmBox('تجاهل هذه الفاتورة نهائياً؟ تأكد أنك أعدت إدخالها.')) return;
          OFFLINE.list = OFFLINE.list.filter((_, k) => k !== +b.dataset.drop); m.close(); shiftBox();
        });
      };
    }
    if ($('#pos-open-shift')) $('#pos-open-shift').onclick = openShiftDialog;
    if ($('#pos-close-shift')) $('#pos-close-shift').onclick = () => closeShiftDialog(d.shift, async () => { await refresh(); });
  };

  const openShiftDialog = () => {
    const m = modal('فتح وردية', `<div class="form-grid">
        <label class="f">الصندوق<select id="sh-cash">${d.cash_accounts.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></label>
        <label class="f">المستودع<select id="sh-wh">${d.warehouses.map(w => `<option value="${w.id}" ${w.id === d.warehouse_id ? 'selected' : ''}>${esc(w.name)}</option>`).join('')}</select></label>
        <label class="f">النقد في الصندوق الآن<input type="number" id="sh-cash-amt" step="any" value="0"></label></div>
      <div class="foot"><button class="btn primary" id="sh-go">فتح الوردية</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#sh-go').onclick = e => run(e.target, async () => {
      await post('/api/shifts/open', { cash_account_id: +$('#sh-cash').value, warehouse_id: +$('#sh-wh').value,
        opening_cash: +$('#sh-cash-amt').value || 0 });
      m.close(); toast('فُتحت الوردية'); await refresh();
    });
  };

  const refresh = async () => { d = await api(`/api/pos/init?warehouse_id=${$('#pos-wh').value || d.warehouse_id}`); shiftBox(); grid(); };
  const grid = () => {
    const q = ($('#pos-scan').value || '').trim();
    const list = d.items.filter(i => !q || i.name.includes(q) || i.code.includes(q)).slice(0, 60);
    $('#pos-grid').innerHTML = list.length ? list.map(i => `<button class="pos-item ${i.qty <= 0 && !i.is_service && !i.is_recipe ? 'out' : ''}" data-add="${i.id}">
        <b>${esc(i.name)}</b><span class="muted small">${fmt(i.sale_price)}${i.is_service || i.is_recipe ? '' : ` · ${fmt(i.qty)} ${esc(i.unit)}`}</span></button>`).join('')
      : '<div class="empty">لا توجد مواد مطابقة</div>';
  };

  const priceOf = async (item, factor, unitId) => (await api(
    `/api/price?item_id=${item.id}&qty=1&unit_factor=${factor}${unitId ? `&unit_id=${unitId}` : ''}${customer ? `&party_id=${customer}` : ''}`));
  const addLine = async (item, { qty = 1, unit = null, price = null, promo = null } = {}) => {
    const units = item.units || [{ name: item.unit, factor: 1, price: item.sale_price, base: true }];
    const u = unit ? units.find(x => x.name === unit.name) || units[0] : units[0];
    const exist = cart.find(l => l.item_id === item.id && l.unit_name === u.name && !l.scale);
    if (exist && !promo) { exist.qty += qty; return draw(); }
    let p = price, src = promo;
    if (p === null) {
      try { const r = await priceOf(item, u.factor, u.id); p = r.price; src = r.promo; }
      catch (e) { if (!e.offline) throw e; p = u.price ?? item.sale_price * u.factor; }     // دون اتصال: السعر المحمّل
    }
    cart.push({ item_id: item.id, name: item.name, unit_name: u.name, unit_factor: u.factor, unit_id: u.id, units,
                qty, price: p, promo: src, stock: item.qty });
    draw();
  };

  const totals = () => {
    const sub = cart.reduce((a, l) => a + l.qty * l.price, 0);
    const disc = +($('#pos-disc')?.value || 0);
    const tax = Math.round((sub - disc) * vatRate) / 100;
    return { sub, disc, tax, total: sub - disc + tax };
  };
  const draw = () => {
    $('#pos-lines').innerHTML = cart.length ? cart.map((l, i) => `<tr>
      <td><b>${esc(l.name)}</b>${l.promo ? ` <span class="badge sale">${esc(l.promo)}</span>` : ''}
        ${l.units.length > 1 ? `<select data-u="${i}" class="small">${l.units.map(u => `<option value="${esc(u.name)}" ${u.name === l.unit_name ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select>` : ''}</td>
      <td><div class="qty"><button data-q="${i},-1">−</button><input type="number" step="any" min="0" data-qty="${i}" value="${l.qty}"><button data-q="${i},1">+</button></div></td>
      <td><input type="number" step="any" min="0" data-price="${i}" value="${l.price}" class="num" ${can('discount') ? '' : 'readonly title="تعديل السعر يحتاج صلاحية الحسم"'}></td>
      <td class="num">${fmt(l.qty * l.price)}</td><td><button class="btn sm ghost" data-rm="${i}"><i class="i i-x"></i></button></td></tr>`).join('')
      : '<tr><td colspan="5" class="empty">السلة فارغة — امسح باركود أو اختر مادة</td></tr>';
    const t = totals();
    $('#pos-totals').innerHTML = `<div class="r"><span>المجموع</span><span class="num">${fmt(t.sub)}</span></div>
      <div class="r"><span>الحسم</span><input type="number" step="any" min="0" id="pos-disc" value="${t.disc || 0}" style="width:100px" ${can('discount') ? '' : 'disabled'}></div>
      ${vatRate ? `<div class="r"><span>الضريبة ${fmt(vatRate)}%</span><span class="num">${fmt(t.tax)}</span></div>` : ''}
      <div class="r total"><span>الإجمالي</span><span class="num">${fmt(t.total)} ${esc(baseCur().symbol)}</span></div>`;
    $('#pos-disc').oninput = draw;
    if (keepThanks && !cart.length) return;      // أبقِ «شكراً لزيارتكم» حتى يبدأ الزبون التالي
    keepThanks = false;
    pushDisplay({ company: S.settings.company_name, symbol: baseCur().symbol, total: t.total,
      lines: cart.map(l => ({ name: l.name, qty: l.qty, price: l.price, total: l.qty * l.price })) });
  };

  const pay = async () => {
    if (!cart.length) return toast('السلة فارغة', true);
    const t = totals();
    const m = modal('إتمام البيع', `<div class="pay-total">الإجمالي: <b>${fmt(t.total)}</b> ${esc(baseCur().symbol)}</div>
      <div class="tabs" id="pay-tabs"><button class="on" data-p="cash">نقداً</button><button data-p="credit">آجل (على حساب الزبون)</button></div>
      <div id="pay-cash"><div class="form-grid">
        <label class="f">المدفوع<input type="number" id="pay-amt" step="any" value="${t.total}"></label>
        <label class="f">الصندوق<select id="pay-box">${d.cash_accounts.map(a => `<option value="${a.id}" ${d.shift && d.shift.cash_account_id === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select></label></div>
        <div class="row" style="margin-top:8px">${[t.total, 1000, 5000, 10000, 50000].map(v => `<button class="btn sm" data-quick="${v}">${fmt(v)}</button>`).join('')}</div>
        <div class="pay-change" id="pay-change"></div></div>
      <div class="foot"><button class="btn primary" id="pay-go">تأكيد البيع (Enter)</button><button class="btn" data-close>إلغاء</button></div>`);
    let method = 'cash';
    const payRef = newRef();       // مرجع واحد لهذه العملية: الضغط المكرر أو إعادة الإرسال لا يكرّر الفاتورة
    const change = () => { $('#pay-change').innerHTML = `الباقي للزبون: <b>${fmt(Math.max(0, (+$('#pay-amt').value || 0) - t.total))}</b>`; };
    $('#pay-amt').oninput = change; change();
    $('#pay-amt').select();
    $('#pay-tabs').onclick = e => {
      const b = e.target.closest('button'); if (!b) return;
      method = b.dataset.p;
      $$('#pay-tabs button').forEach(x => x.classList.toggle('on', x === b));
      $('#pay-cash').style.display = method === 'cash' ? '' : 'none';
      if (method === 'credit' && !customer) toast('اختر الزبون أولاً للبيع الآجل', true);
    };
    m.el.querySelectorAll('[data-quick]').forEach(b => b.onclick = () => { $('#pay-amt').value = b.dataset.quick; change(); });
    const go = () => run($('#pay-go'), async () => {
      if (method === 'credit' && !customer) throw new Error('البيع الآجل يحتاج اختيار زبون');
      const body = {
        kind: 'sale', payment: method, party_id: customer || null, warehouse_id: +$('#pos-wh').value,
        cash_account_id: method === 'cash' ? +$('#pay-box').value : null, discount: t.disc, tax_rate: vatRate,
        client_ref: payRef, date: today(),
        lines: cart.map(l => ({ item_id: l.item_id, qty: l.qty, price: l.price, unit_name: l.unit_name, unit_factor: l.unit_factor })),
      };
      const paid = method === 'cash' ? (+$('#pay-amt').value || t.total) : 0;
      let inv;
      try { inv = await post('/api/invoices', body); }
      catch (e) {
        if (!e.offline) throw e;
        // انقطع الاتصال: تُحفظ الفاتورة على هذا الجهاز وتُرحَّل تلقائياً عند عودته، والزبون لا ينتظر
        OFFLINE.add(body, t.total);
        m.close();
        toast('لا اتصال بالخادم — حُفظت الفاتورة على هذا الجهاز وستُرحَّل تلقائياً', true);
        printReceipt({ number: 'مؤقت', date: today(), subtotal: t.sub, discount: t.disc, tax_rate: vatRate, tax_amount: t.tax, total: t.total,
          lines: cart.map(l => ({ item_name: l.name, qty: l.qty, price: l.price, total: l.qty * l.price, unit_name: l.unit_name, unit_factor: l.unit_factor })) },
          { paid, change: Math.max(0, paid - t.total), cashier: S.settings.user.full_name });
        cart.length = 0; draw(); shiftBox(); $('#pos-scan').focus();
        return;
      }
      m.close();
      toast(`فاتورة رقم ${inv.number} — ${fmt(inv.total)}`);
      if (d.settings.pos_print === '1')
        printSale(inv, { paid, change: Math.max(0, paid - t.total), drawer: method === 'cash' })
          .catch(e => toast('تعذرت الطباعة: ' + e.message, true));
      keepThanks = true;
      pushDisplay({ company: S.settings.company_name, lines: [], total: 0, thanks: true, paid, change: Math.max(0, paid - t.total) });
      S.lastInvoice = inv;
      cart.length = 0;
      await refresh();
      draw();
      $('#pos-scan').focus();
    });
    $('#pay-go').onclick = go;
    m.el.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
  };

  $('#pos-grid').onclick = e => {
    const b = e.target.closest('[data-add]');
    if (b) addLine(d.items.find(i => i.id == b.dataset.add));
  };
  $('#pos-lines').onclick = e => {
    const rm = e.target.closest('[data-rm]');
    if (rm) { cart.splice(+rm.dataset.rm, 1); return draw(); }
    const q = e.target.closest('[data-q]');
    if (q) {
      const [i, delta] = q.dataset.q.split(',').map(Number);
      cart[i].qty = Math.max(0, +(cart[i].qty + delta).toFixed(3));
      if (!cart[i].qty) cart.splice(i, 1);
      draw();
    }
  };
  $('#pos-lines').oninput = e => {
    const qty = e.target.closest('[data-qty]'), price = e.target.closest('[data-price]');
    if (qty) { cart[+qty.dataset.qty].qty = +qty.value || 0; }
    if (price) { cart[+price.dataset.price].price = +price.value || 0; }
    if (qty || price) {
      const t = totals();
      $$('.pos-cart tbody tr').forEach((tr, i) => { if (cart[i]) tr.children[3].textContent = fmt(cart[i].qty * cart[i].price); });
      $('#pos-totals .total .num').textContent = `${fmt(t.total)} ${baseCur().symbol}`;
    }
  };
  $('#pos-lines').onchange = async e => {
    const u = e.target.closest('[data-u]');
    if (!u) return;
    const l = cart[+u.dataset.u];
    const unit = l.units.find(x => x.name === u.value);
    l.unit_name = unit.name; l.unit_factor = unit.factor; l.unit_id = unit.id;
    const r = await priceOf({ id: l.item_id }, unit.factor, unit.id);
    l.price = r.price; l.promo = r.promo;
    draw();
  };
  $('#pos-scan').onkeydown = async e => {
    if (e.key !== 'Enter') return;
    const code = $('#pos-scan').value.trim();
    if (!code) return;
    await run(null, async () => {
      let r;
      try { r = await api(`/api/pos/scan?code=${encodeURIComponent(code)}${customer ? `&party_id=${customer}` : ''}`); }
      catch (e) {
        if (!e.offline) throw e;                       // دون اتصال: نطابق محلياً بالباركود أو الرمز
        const it = d.items.find(i => i.barcode === code || i.code === code);
        const viaUnit = !it && d.items.find(i => (i.units || []).some(u => u.barcode === code));
        const unit = viaUnit ? viaUnit.units.find(u => u.barcode === code) : null;
        r = it || viaUnit ? { found: true, item: it || viaUnit, unit, qty: 1, price: unit ? unit.price : it.sale_price, promo: null } : { found: false };
      }
      if (!r.found) {
        const matches = d.items.filter(i => i.name.includes(code));
        if (matches.length === 1) await addLine(matches[0]);
        else { grid(); return toast(matches.length ? 'اختر المادة من القائمة' : 'لا توجد مادة بهذا الرمز', !matches.length); }
      } else {
        const item = d.items.find(i => i.id === r.item.id) || { ...r.item, units: [] };
        await addLine(item, { qty: r.qty, unit: r.unit, price: r.price, promo: r.promo });
      }
      $('#pos-scan').value = '';
      grid();
    });
  };
  $('#pos-scan').oninput = grid;
  $('#pos-cust').onchange = () => { customer = $('#pos-cust').value; };
  $('#pos-wh').onchange = refresh;
  $('#pos-refresh').onclick = () => run(null, refresh);
  $('#pos-clear').onclick = () => { cart.length = 0; draw(); };
  $('#pos-pay').onclick = pay;
  $('#pos-last').onclick = () => S.lastInvoice ? showInvoice(S.lastInvoice.id) : toast('لا توجد فاتورة بعد');
  const HELD = {
    get list() { try { return JSON.parse(localStorage.getItem('axis_held') || '[]'); } catch { return []; } },
    set list(v) { try { localStorage.setItem('axis_held', JSON.stringify(v)); } catch { } },
  };
  const heldBadge = () => { $('#pos-held-n').textContent = HELD.list.length ? ` ${HELD.list.length}` : ''; };
  $('#pos-hold').onclick = () => {
    if (!cart.length) return toast('السلة فارغة', true);
    HELD.list = [...HELD.list, { at: new Date().toISOString(), customer, cart: cart.map(l => ({ ...l })), total: totals().total }];
    cart.length = 0; draw(); heldBadge(); toast('عُلّقت الفاتورة'); $('#pos-scan').focus();
  };
  $('#pos-held').onclick = () => {
    const list = HELD.list;
    if (!list.length) return toast('لا توجد فواتير معلّقة');
    const m = modal('الفواتير المعلّقة', list.map((h, i) => `<div class="row" style="margin-bottom:8px">
      <button class="btn" style="flex:1;justify-content:space-between" data-res="${i}"><span>${h.at.slice(11, 16)} — ${h.cart.length} صنف</span><b>${fmt(h.total)}</b></button>
      <button class="btn sm danger" data-hdel="${i}"><i class="i i-x"></i></button></div>`).join('') + '<div class="foot"><button class="btn" data-close>إغلاق</button></div>');
    m.el.onclick = e => {
      const r = e.target.closest('[data-res]'), x = e.target.closest('[data-hdel]');
      if (r) {
        if (cart.length) return toast('أنهِ الفاتورة الحالية أو علّقها أولاً', true);
        const h = list[+r.dataset.res];
        h.cart.forEach(l => cart.push(l)); customer = h.customer || ''; $('#pos-cust').value = customer;
        HELD.list = list.filter((_, k) => k !== +r.dataset.res); m.close(); draw(); heldBadge();
      }
      if (x) { HELD.list = list.filter((_, k) => k !== +x.dataset.hdel); m.close(); heldBadge(); }
    };
  };
  heldBadge();
  $('#pos-drawer').onclick = e => run(e.target, async () => { await openDrawer(); toast('فُتح الدرج'); });
  $('#pos-display').onclick = () => window.open('/static/display.html', 'axis_display', 'width=900,height=600');
  $('#pos-scale').onclick = e => run(e.target, async () => {
    if (!cart.length) throw new Error('أضف المادة أولاً ثم اقرأ وزنها');
    const w = await readScale();
    cart[cart.length - 1].qty = w;
    toast(`${fmt(w)} كغ`);
    draw();
  });
  app.onkeydown = e => {
    if (e.key === 'F2') { e.preventDefault(); pay(); }
    if (e.key === 'F4') { e.preventDefault(); $('#pos-cust').focus(); }
  };
  shiftBox(); grid(); draw();
  $('#pos-scan').focus();
};

function closeShiftDialog(sh, done) {
  const m = modal(`إغلاق وردية رقم ${sh.number}`, `
    <table class="small"><tbody>
      <tr><td>رصيد افتتاحي</td><td class="num">${fmt(sh.opening_cash)}</td></tr>
      <tr><td>مبيعات نقدية</td><td class="num">${fmt(sh.sales_cash)}</td></tr>
      <tr><td>مبيعات آجلة</td><td class="num">${fmt(sh.sales_credit)}</td></tr>
      <tr><td>مرتجعات</td><td class="num">${fmt(sh.returns_total)}</td></tr>
      <tr><td>حركة الصندوق (تشمل السندات)</td><td class="num">${fmt(sh.cash_movement)}</td></tr>
      <tr><td><b>المتوقع في الصندوق</b></td><td class="num"><b>${fmt(sh.expected)}</b></td></tr></tbody></table>
    <div class="form-grid" style="margin-top:10px"><label class="f">النقد المعدود فعلياً<input type="number" id="cs-amt" step="any" value="${sh.expected}"></label>
      <label class="f">ملاحظات<input id="cs-notes"></label></div>
    <p id="cs-diff" class="small"></p>
    <div class="foot"><button class="btn primary" id="cs-go">إغلاق الوردية</button><button class="btn" data-close>إلغاء</button></div>`);
  const diff = () => {
    const v = (+$('#cs-amt').value || 0) - sh.expected;
    $('#cs-diff').innerHTML = Math.abs(v) < 0.005 ? '<span class="pos"><i class="i i-check"></i> مطابق</span>'
      : `<span class="neg">${v < 0 ? 'عجز' : 'زيادة'} ${fmt(Math.abs(v))}</span> — سيُرحَّل تلقائياً إلى حساب «عجز وزيادة الصندوق»`;
  };
  $('#cs-amt').oninput = diff; diff();
  $('#cs-go').onclick = e => run(e.target, async () => {
    const r = await post(`/api/shifts/${sh.id}/close`, { counted_cash: +$('#cs-amt').value || 0, notes: $('#cs-notes').value });
    m.close();
    toast(`أُغلقت الوردية — ${Math.abs(r.difference) < 0.005 ? 'مطابقة' : (r.difference < 0 ? 'عجز ' : 'زيادة ') + fmt(Math.abs(r.difference))}`);
    done && done();
  });
}

// ═════════════════════ الورديات ═════════════════════
PAGES.shifts = async app => {
  const d = await api('/api/shifts');
  app.innerHTML = `${d.current ? `<div class="card"><h3>وردية مفتوحة رقم ${d.current.number}<span class="sp"></span>
      <button class="btn danger sm" id="sh-close">إغلاق الوردية</button></h3>
      <div class="grid g4">
        <div class="card kpi"><span class="t">رصيد افتتاحي</span><span class="v">${money(d.current.opening_cash)}</span></div>
        <div class="card kpi"><span class="t">مبيعات الوردية</span><span class="v">${money(d.current.sales_total)}</span><span class="s">${d.current.sales_count} فاتورة</span></div>
        <div class="card kpi"><span class="t">ربح الوردية</span><span class="v">${money(d.current.profit)}</span></div>
        <div class="card kpi hl"><span class="t">المتوقع في الصندوق</span><span class="v">${money(d.current.expected)}</span></div></div></div>`
    : '<div class="card"><b>لا توجد وردية مفتوحة</b> <span class="muted small">— تُفتح من شاشة نقطة البيع.</span></div>'}
    <div class="card"><h3><i class="i i-clipboard"></i> سجل الورديات</h3>
      ${d.list.length ? `<div class="table-wrap"><table><thead><tr><th>#</th><th>الكاشير</th><th>الصندوق</th><th>الفتح</th><th>الإغلاق</th>
        <th>المبيعات</th><th>المتوقع</th><th>المعدود</th><th>الفرق</th></tr></thead><tbody>
        ${d.list.map(s => `<tr class="clickable" data-s="${s.id}"><td>${s.number}</td><td>${esc(s.user_name)}</td><td class="small">${esc(s.cash_name)}</td>
          <td class="small num">${s.opened_at.slice(0, 16)}</td><td class="small num">${s.closed_at ? s.closed_at.slice(0, 16) : '<span class="badge sale">مفتوحة</span>'}</td>
          <td>${money(s.sales_total)} <span class="muted small">(${s.sales_count})</span></td><td>${s.expected_cash == null ? '—' : money(s.expected_cash)}</td>
          <td>${s.counted_cash == null ? '—' : money(s.counted_cash)}</td>
          <td>${s.difference == null ? '—' : `<b class="${Math.abs(s.difference) < 0.005 ? 'pos' : 'neg'}">${fmt(s.difference)}</b>`}</td></tr>`).join('')}
      </tbody></table></div>` : '<div class="empty">لا توجد ورديات بعد</div>'}</div>`;
  if ($('#sh-close')) $('#sh-close').onclick = () => closeShiftDialog(d.current, route);
  $$('[data-s]', app).forEach(tr => tr.onclick = async () => {
    const sh = await api(`/api/shifts/${tr.dataset.s}`);
    modal(`وردية رقم ${sh.number}`, `<table class="small"><tbody>
      <tr><td>الكاشير</td><td>${esc(sh.user_name)}</td></tr><tr><td>الصندوق</td><td>${esc(sh.cash_name)}</td></tr>
      <tr><td>الفتح</td><td class="num">${sh.opened_at}</td></tr><tr><td>الإغلاق</td><td class="num">${sh.closed_at || 'مفتوحة'}</td></tr>
      <tr><td>رصيد افتتاحي</td><td class="num">${fmt(sh.opening_cash)}</td></tr>
      <tr><td>مبيعات نقدية / آجلة</td><td class="num">${fmt(sh.sales_cash)} / ${fmt(sh.sales_credit)}</td></tr>
      <tr><td>مرتجعات</td><td class="num">${fmt(sh.returns_total)}</td></tr>
      <tr><td>المتوقع / المعدود</td><td class="num">${fmt(sh.expected)} / ${sh.counted_cash == null ? '—' : fmt(sh.counted_cash)}</td></tr>
      <tr><td>الفرق</td><td class="num">${sh.difference == null ? '—' : fmt(sh.difference)}</td></tr>
      ${sh.notes ? `<tr><td>ملاحظات</td><td>${esc(sh.notes)}</td></tr>` : ''}</tbody></table>
      <div class="foot"><button class="btn" data-close>إغلاق</button></div>`);
  });
};

// ═════════════════════ سجل الفواتير ═════════════════════
PAGES.invoices = async app => {
  app.innerHTML = `<div class="card no-print"><div class="row">
      <select id="f-kind"><option value="">كل الأنواع</option>${Object.entries(KIND).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
      <label class="row small">من <input type="date" id="f-from" value="${monthStart()}"></label>
      <label class="row small">إلى <input type="date" id="f-to" value="${today()}"></label>
      <button class="btn" id="f-go">عرض</button><span class="sp"></span><span id="f-sum" class="muted small"></span></div></div>
    <div class="card" id="inv-list"></div>`;
  let limit = 300;
  const load = async () => {
    const q = new URLSearchParams({ kind: $('#f-kind').value, date_from: $('#f-from').value, date_to: $('#f-to').value, limit });
    [...q.keys()].forEach(k => !q.get(k) && q.delete(k));
    const { data: list, total } = await api('/api/invoices?' + q, { withTotal: true });
    $('#inv-list').innerHTML = invoiceTable(list) + moreBar(list.length, total);
    if ($('#inv-list [data-more]')) $('#inv-list [data-more]').onclick = () => { limit += 700; load(); };
    const sum = k => list.filter(i => i.kind === k).reduce((s, i) => s + i.net_base, 0);
    $('#f-sum').textContent = `${list.length} فاتورة · مبيعات ${fmt(sum('sale'))} · مشتريات ${fmt(sum('purchase'))} ${baseCur().symbol} (قبل الضريبة)`;
    bindInvoiceRows($('#inv-list'), load);
  };
  $('#f-go').onclick = load;
  $('#f-kind').onchange = load;
  await load();
};

// ═════════════════════ السندات ═════════════════════
PAGES.vouchers = async app => {
  const [accs, parties] = await Promise.all([api('/api/accounts'), api('/api/parties')]);
  const hasCur = (S.settings.currencies || []).length > 1;
  app.innerHTML = `<div class="grid g2">
    <div class="card"><h3><i class="i i-cash"></i> سند جديد</h3>
      <div class="tabs" id="v-tabs"><button class="on" data-k="receipt">سند قبض (استلام مال)</button><button data-k="payment">سند دفع (صرف مال)</button></div>
      <div class="form-grid">
        <label class="f">التاريخ<input type="date" id="v-date" value="${today()}"></label>
        <label class="f">المبلغ<input type="number" id="v-amount" min="0" step="any"></label>
        <label class="f" ${hasCur ? '' : 'style="display:none"'}>العملة<select id="v-cur">${curOptions()}</select></label>
        <label class="f">سعر الصرف<input type="number" id="v-rate" step="any" min="0"></label>
        <label class="f" style="grid-column:1/-1"><span id="v-acc-lbl">مقبوض من حساب</span><select id="v-acc"></select></label>
        <label class="f">الصندوق / البنك<select id="v-cash">${accountOptions(cashAccounts(accs))}</select></label>
        <label class="f">البيان<input id="v-desc" placeholder="اختياري"></label>
      </div>
      <p class="small muted" id="v-base"></p>
      <div id="v-alloc"></div>
      <div class="row" style="margin-top:8px"><button class="btn primary" id="v-save"><i class="i i-save"></i> حفظ السند</button></div>
      <p class="small muted">مثال: قبض دفعة من عميل، دفع لمورد، دفع إيجار أو رواتب، إيداع رأس مال.</p></div>
    <div class="card"><h3><i class="i i-clock"></i> آخر السندات</h3><div id="v-list"></div></div></div>`;
  let kind = 'receipt';
  const showBase = () => {
    const { currency, rate } = getCur();
    $('#v-base').textContent = curOf(currency).is_base ? '' : `يعادل ${fmt((+$('#v-amount').value || 0) * rate)} ${baseCur().symbol}`;
  };
  const getCur = bindCurrency($('#v-cur'), $('#v-rate'), showBase);
  $('#v-amount').oninput = () => { showBase(); allocSum(); };
  // تخصيص مبلغ السند على فواتير الطرف غير المسدَّدة (الأقدم أولاً)
  let openInv = [];
  const allocBox = () => $('#v-alloc');
  const renderAlloc = () => {
    if (!openInv.length) { allocBox().innerHTML = ''; return; }
    allocBox().innerHTML = `<div class="card" style="margin-top:10px;background:var(--panel-2)">
      <h3 style="font-size:14px"><i class="i i-receipt"></i> تخصيص المبلغ على الفواتير غير المسدَّدة<span class="sp"></span>
        <button class="btn sm" id="v-auto">توزيع تلقائي (الأقدم أولاً)</button><button class="btn sm ghost" id="v-clear">تصفير</button></h3>
      <table class="small"><thead><tr><th>الفاتورة</th><th>التاريخ</th><th>المتبقي</th><th style="width:130px">المخصَّص</th></tr></thead><tbody>
      ${openInv.map(i => `<tr><td>${i.kind_label} ${i.number}</td><td>${i.date}</td><td>${money(i.outstanding)}</td>
        <td><input type="number" step="any" min="0" max="${i.outstanding}" data-inv="${i.id}" value="${i.alloc || ''}"></td></tr>`).join('')}
      </tbody></table><p class="small muted" id="v-alloc-sum"></p></div>`;
    allocSum();
    $('#v-auto').onclick = () => {
      let left = (+$('#v-amount').value || 0) * (getCur().rate || 1);
      openInv.forEach(i => { i.alloc = Math.min(left, i.outstanding) || 0; left = Math.max(0, left - i.alloc); });
      renderAlloc();
    };
    $('#v-clear').onclick = () => { openInv.forEach(i => i.alloc = 0); renderAlloc(); };
    allocBox().oninput = e => {
      const inp = e.target.closest('[data-inv]');
      if (!inp) return;
      openInv.find(i => i.id == inp.dataset.inv).alloc = +inp.value || 0;
      allocSum();
    };
  };
  const allocSum = () => {
    const sum = openInv.reduce((s, i) => s + (i.alloc || 0), 0);
    const amount = (+$('#v-amount').value || 0) * (getCur().rate || 1);
    const el = $('#v-alloc-sum');
    if (el) el.innerHTML = `المخصَّص: <b>${fmt(sum)}</b> من ${fmt(amount)} ${esc(baseCur().symbol)}` +
      (sum > amount + 0.005 ? ' <span class="neg">— أكبر من مبلغ السند</span>' : sum < amount - 0.005 ? ` <span class="muted">— غير مخصَّص: ${fmt(amount - sum)}</span>` : ' <span class="pos"><i class="i i-check"></i></span>');
  };
  const loadOpen = async () => {
    const party = parties.find(p => p.account_id == +$('#v-acc').value);
    openInv = party ? (await api(`/api/parties/${party.id}/open-invoices`)) : [];
    renderAlloc();
  };
  const fill = () => {
    const pref = kind === 'receipt' ? ['113', '4', '3', '2'] : ['21', '5', '3', '12', '2'];
    const rank = a => { const i = pref.findIndex(p => a.code.startsWith(p)); return i < 0 ? 99 : i; };
    const list = accs.filter(a => !a.is_group && !a.is_cash).sort((a, b) => rank(a) - rank(b) || a.code.localeCompare(b.code));
    $('#v-acc').innerHTML = accountOptions(list);
    $('#v-acc-lbl').textContent = kind === 'receipt' ? 'مقبوض من حساب (عميل / إيراد / رأس مال…)' : 'مدفوع إلى حساب (مورد / مصروف…)';
    loadOpen();
  };
  $('#v-acc').onchange = loadOpen;
  $('#v-tabs').onclick = e => { const b = e.target.closest('button'); if (!b) return; kind = b.dataset.k; $$('#v-tabs button').forEach(x => x.classList.toggle('on', x === b)); fill(); };
  const load = async () => {
    const js = await api('/api/vouchers');
    $('#v-list').innerHTML = js.length ? `<table><thead><tr><th>#</th><th>النوع</th><th>التاريخ</th><th>البيان</th><th>المبلغ</th></tr></thead><tbody>
      ${js.map(j => `<tr class="clickable" data-j="${j.id}"><td>${j.number}</td><td>${SOURCE[j.source]}</td><td>${j.date}</td><td>${esc(j.description)}</td><td>${money(j.amount)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">لا توجد سندات</div>';
    $$('[data-j]', $('#v-list')).forEach(tr => tr.onclick = () => showJournal(+tr.dataset.j, load));
  };
  $('#v-save').onclick = e => run(e.target, async () => {
    const j = await post('/api/vouchers', { kind, date: $('#v-date').value, amount: +$('#v-amount').value,
      account_id: +$('#v-acc').value, cash_account_id: +$('#v-cash').value, description: $('#v-desc').value, ...getCur(),
      allocations: openInv.filter(i => i.alloc > 0).map(i => ({ invoice_id: i.id, amount: i.alloc })) });
    toast(`حُفظ ${SOURCE[kind]} رقم ${j.number}`);
    $('#v-amount').value = ''; $('#v-desc').value = ''; showBase();
    await loadOpen();
    load();
  });
  fill();
  await load();
};

// ═════════════════════ قيود اليومية ═════════════════════
async function showJournal(id, after) {
  const j = await api(`/api/journals/${id}`);
  const isVoucher = j.source === 'receipt' || j.source === 'payment';
  const allocs = isVoucher && can('vouchers') ? await api(`/api/vouchers/${id}/allocations`) : [];
  const deletable = !(j.source in KIND) && !j.source.startsWith('opening') && j.source !== 'closing' && can('delete');
  const editable = can('edit') && (j.source === 'manual' || isVoucher);
  const m = modal(`${SOURCE[j.source] || 'قيد'} — قيد رقم ${j.number}`, `
    <p class="small muted">التاريخ: ${j.date} — ${esc(j.description || '')}${j.user_name ? ` — بواسطة ${esc(j.user_name)}` : ''}</p>
    <table><thead><tr><th>الحساب</th><th>مدين</th><th>دائن</th><th>البيان</th></tr></thead><tbody>
    ${j.lines.map(l => `<tr><td>${esc(l.code)} — ${esc(l.account_name)}</td><td>${l.debit ? money(l.debit) : ''}</td><td>${l.credit ? money(l.credit) : ''}</td>
      <td class="small">${esc(l.memo || '')}${l.currency ? ` <span class="muted">(${fmt(l.fx_amount)} ${esc(curOf(l.currency).symbol)})</span>` : ''}</td></tr>`).join('')}</tbody></table>
    ${allocs.length ? `<p class="small">مخصَّص على الفواتير: ${allocs.map(a => `<span class="badge">${KIND[a.kind]} ${a.number}: ${fmt(a.amount)}</span>`).join(' ')}</p>` : ''}
    <div class="foot">${j.source in KIND ? `<button class="btn" data-inv>عرض الفاتورة</button>` : ''}
      ${editable && j.source === 'manual' ? '<button class="btn" data-edit><i class="i i-edit"></i> تعديل القيد</button>' : ''}
      ${deletable ? `<button class="btn danger" data-del><i class="i i-trash"></i> حذف القيد</button>` : ''}<button class="btn" data-close>إغلاق</button></div>`);
  if ($('[data-edit]', m.el)) $('[data-edit]', m.el).onclick = () => { m.close(); go('#/journal/' + id); };
  if ($('[data-inv]', m.el)) $('[data-inv]', m.el).onclick = () => { m.close(); showInvoice(j.source_id, after); };
  if ($('[data-del]', m.el)) $('[data-del]', m.el).onclick = async e => {
    if (!await confirmBox('حذف هذا القيد نهائياً؟')) return;
    await run(e.target, async () => { await del(`/api/journals/${id}`); toast('حُذف القيد'); m.close(); after ? after() : route(); });
  };
}

PAGES.journal = async (app, editId = null) => {
  const accs = await api('/api/accounts');
  const opts = accountOptions(accs);
  const editing = editId ? await api(`/api/journals/${editId}`) : null;
  app.innerHTML = `<div class="card"><h3>${editing ? `تعديل قيد رقم ${editing.number}` : 'قيد يومية جديد'}<span class="sp"></span>${can('ai') ? `<button class="btn sm ai" onclick="go('#/assistant')">صِف العملية للمساعد وسيقترح القيد</button>` : ''}</h3>
      <div class="form-grid" style="margin-bottom:12px"><label class="f">التاريخ<input type="date" id="j-date" value="${today()}"></label>
      <label class="f" style="grid-column:span 3">البيان<input id="j-desc" placeholder="وصف العملية"></label></div>
      <div class="table-wrap"><table class="lines-table"><thead><tr><th>الحساب</th><th style="width:140px">مدين</th><th style="width:140px">دائن</th><th id="j-cc-head" style="width:150px;display:none">مركز الكلفة</th><th>ملاحظة</th><th style="width:40px"></th></tr></thead>
      <tbody id="j-lines"></tbody><tfoot><tr><td>المجموع <span id="j-diff" class="small"></span></td><td id="j-td" class="num"></td><td id="j-tc" class="num"></td><td colspan="3"></td></tr></tfoot></table></div>
      <div class="row" style="margin-top:12px"><button class="btn" id="j-add"><i class="i i-plus"></i> سطر</button><span class="sp"></span>
        ${editing ? '<button class="btn" onclick="go(\'#/journal\')">إلغاء التعديل</button>' : ''}
        <button class="btn primary" id="j-save"><i class="i i-save"></i> ${editing ? 'حفظ التعديل' : 'ترحيل القيد'}</button></div></div>
    <div class="card"><h3><i class="i i-journal"></i> دفتر اليومية<span class="sp"></span><input type="date" id="jl-from" value="${monthStart()}"><input type="date" id="jl-to" value="${today()}"><button class="btn sm" id="jl-go">عرض</button></h3><div id="j-list"></div></div>`;
  const centers = (await api('/api/cost-centers').catch(() => [])).filter(c => c.active);
  const ccCol = centers.length;
  const row = () => `<tr><td><select data-f="account_id"><option value="">— اختر الحساب —</option>${opts}</select></td>
    <td><input type="number" step="any" min="0" data-f="debit"></td><td><input type="number" step="any" min="0" data-f="credit"></td>
    ${ccCol ? `<td><select data-f="cost_center_id"><option value="">—</option>${centers.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></td>` : ''}
    <td><input data-f="memo"></td><td><button class="btn sm ghost" data-rm><i class="i i-x"></i></button></td></tr>`;
  const sums = () => {
    let d = 0, c = 0;
    $$('#j-lines tr').forEach(tr => { d += +$('[data-f=debit]', tr).value || 0; c += +$('[data-f=credit]', tr).value || 0; });
    $('#j-td').textContent = fmt(d); $('#j-tc').textContent = fmt(c);
    const diff = Math.round((d - c) * 100) / 100;
    $('#j-diff').innerHTML = diff ? `<span class="neg">(الفرق ${fmt(Math.abs(diff))})</span>` : d ? '<span class="pos"><i class="i i-check"></i> متوازن</span>' : '';
  };
  if (ccCol) $('#j-cc-head').style.display = '';
  if (editing) {
    $('#j-date').value = editing.date;
    $('#j-desc').value = editing.description || '';
    $('#j-lines').innerHTML = editing.lines.map(() => row()).join('');
    $$('#j-lines tr').forEach((tr, i) => {
      const l = editing.lines[i];
      $('[data-f=account_id]', tr).value = l.account_id;
      $('[data-f=debit]', tr).value = l.debit || '';
      $('[data-f=credit]', tr).value = l.credit || '';
      $('[data-f=memo]', tr).value = l.memo || '';
      if ($('[data-f=cost_center_id]', tr) && l.cost_center_id) $('[data-f=cost_center_id]', tr).value = l.cost_center_id;
    });
  } else $('#j-lines').innerHTML = row() + row();
  $('#j-lines').oninput = sums;
  $('#j-lines').onclick = e => { if (e.target.closest('[data-rm]') && $$('#j-lines tr').length > 2) { e.target.closest('tr').remove(); sums(); } };
  $('#j-add').onclick = () => $('#j-lines').insertAdjacentHTML('beforeend', row());
  let jlimit = 300;
  const load = async () => {
    const { data: js, total: jtotal } = await api(`/api/journals?date_from=${$('#jl-from').value}&date_to=${$('#jl-to').value}&limit=${jlimit}`, { withTotal: true });
    $('#j-list').innerHTML = js.length ? `<div class="table-wrap"><table><thead><tr><th>رقم القيد</th><th>التاريخ</th><th>المصدر</th><th>البيان</th><th>المستخدم</th><th>المبلغ</th></tr></thead><tbody>
      ${js.map(j => `<tr class="clickable" data-j="${j.id}"><td>${j.number}</td><td>${j.date}</td><td><span class="badge ${j.source}">${SOURCE[j.source] || j.source}</span></td><td>${esc(j.description)}</td><td class="small muted">${esc(j.user_name || '')}</td><td>${money(j.amount)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">لا توجد قيود في هذه الفترة</div>';
    $('#j-list').insertAdjacentHTML('beforeend', moreBar(js.length, jtotal));
    if ($('#j-list [data-more]')) $('#j-list [data-more]').onclick = () => { jlimit += 700; load(); };
    $$('[data-j]', $('#j-list')).forEach(tr => tr.onclick = () => showJournal(+tr.dataset.j, load));
  };
  $('#jl-go').onclick = load;
  $('#j-save').onclick = e => run(e.target, async () => {
    const lines = $$('#j-lines tr').map(tr => Object.fromEntries($$('[data-f]', tr).map(i => [i.dataset.f, i.value]))).filter(l => l.account_id);
    const body = { date: $('#j-date').value, description: $('#j-desc').value, lines };
    const j = editing ? await put(`/api/journals/${editing.id}`, body) : await post('/api/journals', body);
    toast(editing ? `عُدِّل القيد رقم ${j.number}` : `رُحِّل القيد رقم ${j.number}`);
    if (editing) return go('#/journal');
    $('#j-lines').innerHTML = row() + row(); $('#j-desc').value = ''; sums();
    load();
  });
  await load();
};

// ═════════════════════ دليل الحسابات ═════════════════════
PAGES.accounts = async app => {
  const accs = await api('/api/accounts');
  const edit = can('accounts');
  const depth = a => { let d = 0, p = a; while (p && p.parent_id) { d++; p = accs.find(x => x.id === p.parent_id); } return d; };
  app.innerHTML = `<div class="card"><h3><i class="i i-tree"></i> شجرة الحسابات<span class="sp"></span><input id="a-search" placeholder="بحث…" style="width:200px"></h3>
    <div class="table-wrap"><table class="tree"><thead><tr><th>الحساب</th><th>النوع</th><th>مدين</th><th>دائن</th><th>الرصيد</th><th></th></tr></thead><tbody>
    ${accs.map(a => `<tr data-name="${esc(a.code + ' ' + a.name)}" class="${a.is_group ? 'grp' : ''}">
      <td><span class="ind" style="width:${depth(a) * 22}px"></span>${a.is_group ? '<i class="i i-folder"></i>' : a.is_cash ? '<i class="i i-cash"></i>' : '<i class="i i-file"></i>'} <span class="muted">${esc(a.code)}</span> ${esc(a.name)}</td>
      <td class="small muted">${TYPE[a.type]}</td><td>${money(a.debit)}</td><td>${money(a.credit)}</td><td><b>${money(a.balance)}</b></td>
      <td class="act">${a.is_group && edit ? `<button class="btn sm" data-add="${a.id}" title="حساب فرعي"><i class="i i-plus"></i></button>` : ''}
        ${can('reports') ? `<button class="btn sm" data-st="${a.id}" title="كشف حساب"><i class="i i-file"></i></button>` : ''}
        ${edit ? `<button class="btn sm" data-ren="${a.id}" title="تعديل"><i class="i i-edit"></i></button>` : ''}
        ${a.system || !edit || !can('delete') ? '' : `<button class="btn sm danger" data-del="${a.id}" title="حذف"><i class="i i-trash"></i></button>`}</td></tr>`).join('')}
    </tbody></table></div><p class="small muted"><i class="i i-cash"></i> = حساب نقدي (صندوق أو بنك) يظهر في الفواتير والسندات.</p></div>`;
  filterRows($('#a-search'), app);
  app.querySelector('tbody').onclick = async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.st) return go(`#/reports/statement:${b.dataset.st}`);
    if (b.dataset.add) {
      const parent = accs.find(a => a.id == b.dataset.add);
      const m = modal(`حساب جديد تحت «${esc(parent.name)}»`, `<div class="form-grid"><label class="f">اسم الحساب<input id="na-name"></label>
        <label class="f">النوع<select id="na-grp"><option value="0">حساب تفصيلي (تُرحَّل عليه الحركات)</option><option value="1">حساب رئيسي (مجموعة)</option></select></label></div>
        ${parent.type === 'asset' ? '<label class="row small" style="margin-top:10px"><input type="checkbox" id="na-cash"> حساب نقدي (صندوق أو بنك، مثل «صندوق الدولار»)</label>' : ''}
        <div class="foot"><button class="btn primary" id="na-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#na-save').onclick = ev => run(ev.target, async () => { await post('/api/accounts', { name: $('#na-name').value, parent_id: parent.id, is_group: $('#na-grp').value === '1', is_cash: !!$('#na-cash')?.checked }); m.close(); toast('أُضيف الحساب'); route(); });
    }
    if (b.dataset.ren) {
      const a = accs.find(x => x.id == b.dataset.ren);
      const m = modal('تعديل الحساب', `<label class="f">الاسم<input id="ra-name" value="${esc(a.name)}"></label>
        ${a.type === 'asset' && !a.is_group ? `<label class="row small" style="margin-top:10px"><input type="checkbox" id="ra-cash" ${a.is_cash ? 'checked' : ''}> حساب نقدي (صندوق أو بنك)</label>` : ''}
        <div class="foot"><button class="btn primary" id="ra-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#ra-save').onclick = ev => run(ev.target, async () => { await put(`/api/accounts/${a.id}`, { name: $('#ra-name').value, is_cash: $('#ra-cash') ? $('#ra-cash').checked : null }); m.close(); route(); });
    }
    if (b.dataset.del && await confirmBox('حذف هذا الحساب؟')) run(b, async () => { await del(`/api/accounts/${b.dataset.del}`); toast('حُذف الحساب'); route(); });
  };
};

// ═════════════════════ العملاء والموردون ═════════════════════
async function partyForm(kind, p, done) {
  const lists = kind === 'customer' ? await api('/api/price-lists').catch(() => []) : [];
  const m = modal(p ? 'تعديل' : kind === 'customer' ? 'عميل جديد' : 'مورد جديد', `<div class="form-grid">
      <label class="f">الاسم<input id="p-name" value="${esc(p?.name)}"></label><label class="f">الهاتف<input id="p-phone" value="${esc(p?.phone)}"></label>
      <label class="f" style="grid-column:1/-1">العنوان<input id="p-addr" value="${esc(p?.address)}"></label>
      ${kind === 'customer' ? `<label class="f">حد الائتمان (0 = بلا حد)<input type="number" step="any" min="0" id="p-limit" value="${p?.credit_limit ?? 0}"></label>` : ''}
      ${lists.length ? `<label class="f" style="grid-column:1/-1">قائمة الأسعار<select id="p-pl"><option value="">السعر العادي</option>
        ${lists.map(l => `<option value="${l.id}" ${p && p.price_list_id === l.id ? 'selected' : ''}>${esc(l.name)}</option>`).join('')}</select></label>` : ''}</div>
    ${p ? '' : `<p class="small muted">لإدخال رصيد سابق لهذا ${kind === 'customer' ? 'العميل' : 'المورد'} استخدم شاشة «الأرصدة الافتتاحية».</p>`}
    <div class="foot"><button class="btn primary" id="p-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
  $('#p-save').onclick = e => run(e.target, async () => {
    const body = { name: $('#p-name').value, phone: $('#p-phone').value, address: $('#p-addr').value, kind,
                   price_list_id: $('#p-pl') ? (+$('#p-pl').value || null) : (p ? p.price_list_id : null),
                   credit_limit: $('#p-limit') ? (+$('#p-limit').value || 0) : (p ? p.credit_limit : 0) };
    const r = p ? await put(`/api/parties/${p.id}`, body) : await post('/api/parties', body);
    m.close(); toast('تم الحفظ'); done(r);
  });
}

PAGES.parties = async (app, kind = 'customer') => {
  const list = await api(`/api/parties?kind=${kind}`);
  const label = kind === 'customer' ? 'عميل' : 'مورد';
  const total = list.reduce((s, p) => s + p.balance, 0);
  app.innerHTML = `<div class="card"><h3>${kind === 'customer' ? 'العملاء' : 'الموردون'} <span class="muted small">(${list.length})</span><span class="sp"></span>
      <input id="p-search" placeholder="بحث بالاسم أو الهاتف…" style="width:220px"><button class="btn primary" id="p-new"><i class="i i-plus"></i> ${label} جديد</button></h3>
    ${list.length ? `<div class="table-wrap"><table><thead><tr><th>الاسم</th><th>رقم الحساب</th><th>الهاتف</th><th>${kind === 'customer' ? 'الرصيد (مستحق لنا)' : 'الرصيد (مستحق علينا)'}</th><th></th></tr></thead><tbody>
      ${list.map(p => `<tr data-name="${esc(p.name + ' ' + (p.phone || ''))}"><td>${esc(p.name)}</td><td class="muted">${esc(p.code)}</td><td>${esc(p.phone || '')}</td>
      <td><b>${money(p.balance)}</b>${p.credit_limit > 0 ? `<br><span class="small ${p.balance > p.credit_limit ? 'neg' : 'muted'}">الحد ${fmt(p.credit_limit)}</span>` : ''}</td>
      <td class="act"><button class="btn sm" data-st="${p.account_id}"><i class="i i-file"></i> كشف حساب</button><button class="btn sm" data-ed="${p.id}"><i class="i i-edit"></i></button>${can('delete') ? `<button class="btn sm danger" data-del="${p.id}"><i class="i i-trash"></i></button>` : ''}</td></tr>`).join('')}
      </tbody><tfoot><tr><td colspan="3">الإجمالي</td><td>${money(total)}</td><td></td></tr></tfoot></table></div>` : `<div class="empty">لا يوجد ${label}ون بعد</div>`}</div>`;
  $('#p-new').onclick = () => partyForm(kind, null, route);
  filterRows($('#p-search'), app);
  app.querySelector('tbody')?.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.st) statementModal(+b.dataset.st);
    if (b.dataset.ed) partyForm(kind, list.find(p => p.id == b.dataset.ed), route);
    if (b.dataset.del && await confirmBox(`حذف هذا ال${label}؟`)) run(b, async () => { await del(`/api/parties/${b.dataset.del}`); toast('تم الحذف'); route(); });
  });
};

// ═════════════════════ كشف الحساب ═════════════════════
function statementTable(d, from, to) {
  const hasFx = d.lines.some(l => l.currency);
  return `<h3>كشف حساب: ${esc(d.account.code)} — ${esc(d.account.name)} <span class="muted small">من ${from} إلى ${to}</span></h3>
    <div class="table-wrap"><table><thead><tr><th>التاريخ</th><th>رقم القيد</th><th>البيان</th><th>مدين</th><th>دائن</th>${hasFx ? '<th>بالعملة الأصلية</th>' : ''}<th>الرصيد</th></tr></thead><tbody>
    <tr><td colspan="${hasFx ? 6 : 5}" class="muted">رصيد أول المدة</td><td>${money(d.opening)}</td></tr>
    ${d.lines.map(l => `<tr class="clickable" data-j="${l.journal_id}"><td>${l.date}</td><td>${l.number}</td><td>${esc(l.description)}${l.account_name !== d.account.name ? ` <span class="muted small">(${esc(l.account_name)})</span>` : ''}</td>
      <td>${l.debit ? money(l.debit) : ''}</td><td>${l.credit ? money(l.credit) : ''}</td>${hasFx ? `<td class="small">${l.currency ? fx(l.fx_amount, curOf(l.currency).symbol) : ''}</td>` : ''}<td>${money(l.balance)}</td></tr>`).join('')}
    </tbody><tfoot><tr><td colspan="3">الإجمالي / الرصيد الختامي</td><td>${money(d.total_debit)}</td><td>${money(d.total_credit)}</td>${hasFx ? '<td></td>' : ''}<td>${money(d.closing)}</td></tr></tfoot></table></div>`;
}

async function statementModal(accountId) {
  if (can('reports')) return go(`#/reports/statement:${accountId}`);
  const from = yearStart(), to = today();
  const d = await api(`/api/reports/statement/${accountId}?date_from=${from}&date_to=${to}`);
  const m = modal('كشف حساب', `<div id="st-out">${statementTable(d, from, to)}</div><div class="foot"><button class="btn primary" data-p><i class="i i-print"></i> طباعة</button><button class="btn" data-close>إغلاق</button></div>`, { wide: true });
  $('[data-p]', m.el).onclick = () => printHTML('كشف حساب', $('#st-out', m.el).innerHTML);
}

// ═════════════════════ المواد ═════════════════════
async function itemForm(it, done) {
  const units = it ? (await api(`/api/items/${it.id}/units`)).filter(u => !u.base) : [];
  const m = modal(it ? 'تعديل مادة' : 'مادة جديدة', `<div class="form-grid">
      <label class="f">الاسم<input id="it-name" value="${esc(it?.name)}"></label>
      <label class="f">الرمز ${it ? '' : '(اتركه فارغاً للتوليد التلقائي)'}<input id="it-code" value="${esc(it?.code)}" ${it ? 'disabled' : ''}></label>
      <label class="f">الوحدة<input id="it-unit" value="${esc(it?.unit || 'قطعة')}"></label>
      <label class="f">سعر البيع (${esc(baseCur().symbol)})<input type="number" step="any" id="it-price" value="${it?.sale_price ?? ''}"></label>
      <label class="f">حد الطلب (تنبيه النقص)<input type="number" step="any" id="it-min" value="${it?.min_qty ?? 0}"></label>
      <label class="f">الباركود<input id="it-bar" value="${esc(it?.barcode)}"></label></div>
    ${it ? '' : `<label class="row small" style="margin-top:8px"><input type="checkbox" id="it-hasrec">
      منتج يُحضَّر من مكوّنات (وجبة، عصير، تحضيرة) — يفتح محرر المكوّنات بعد الحفظ</label>`}
    <label class="row small" style="margin-top:8px"><input type="checkbox" id="it-service" ${it?.is_service ? 'checked' : ''}>
      صنف غير مخزني (خدمة أو صنف يُباع بلا جرد — لا كمية ولا تكلفة)</label>
    <label class="row small" style="margin-top:8px"><input type="checkbox" id="it-batch" ${it?.track_batches ? 'checked' : ''}>
      تتبّع الدفعات وتواريخ الصلاحية (للمواد الغذائية والدوائية)</label>
    <p class="small muted">الكمية والتكلفة تتحدّث تلقائياً من الفواتير. الكميات الموجودة قبل البدء تُدخل من «الأرصدة الافتتاحية ← بضاعة أول المدة».</p>
    ${it ? `<h3 style="margin-top:14px"><i class="i i-box"></i> وحدات إضافية (كرتونة، دزينة…)</h3>
      <table class="lines-table small"><thead><tr><th>اسم الوحدة</th><th style="width:110px">تحتوي كم ${esc(it.unit)}</th><th style="width:120px">سعر بيعها</th><th>باركود</th><th style="width:36px"></th></tr></thead>
      <tbody id="iu-rows"></tbody></table>
      <button class="btn sm" id="iu-add" style="margin-top:6px"><i class="i i-plus"></i> وحدة</button>
      <p class="small muted">مثال: كرتونة = 12 ${esc(it.unit)}. عند البيع تختار الوحدة فيُخصم من المخزون ما يعادلها بالوحدة الأساسية.</p>` : ''}
    <div class="foot"><button class="btn primary" id="it-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
  if (it) {
    const rows = units.map(u => ({ ...u }));
    const drawU = () => {
      $('#iu-rows').innerHTML = rows.map((u, i) => `<tr>
        <td><input data-u="${i}" data-f="name" value="${esc(u.name)}"></td>
        <td><input type="number" step="any" min="0" data-u="${i}" data-f="factor" value="${u.factor}"></td>
        <td><input type="number" step="any" min="0" data-u="${i}" data-f="price" value="${u.price || ''}"></td>
        <td><input data-u="${i}" data-f="barcode" value="${esc(u.barcode || '')}"></td>
        <td><button class="btn sm ghost" data-rm="${i}"><i class="i i-x"></i></button></td></tr>`).join('');
    };
    $('#iu-add').onclick = () => { rows.push({ name: '', factor: '', price: '', barcode: '' }); drawU(); };
    $('#iu-rows').oninput = e => {
      const inp = e.target.closest('[data-u]');
      if (inp) rows[+inp.dataset.u][inp.dataset.f] = inp.dataset.f === 'name' || inp.dataset.f === 'barcode' ? inp.value : +inp.value || 0;
    };
    $('#iu-rows').onclick = e => { const b = e.target.closest('[data-rm]'); if (b) { rows.splice(+b.dataset.rm, 1); drawU(); } };
    drawU();
    m.units = rows;
  }
  $('#it-save').onclick = e => run(e.target, async () => {
    const body = { name: $('#it-name').value, code: $('#it-code').value, unit: $('#it-unit').value,
      sale_price: +$('#it-price').value || 0, min_qty: +$('#it-min').value || 0, barcode: $('#it-bar').value,
      track_batches: $('#it-batch').checked, is_service: $('#it-service').checked };
    if (it) {
      await put(`/api/items/${it.id}`, body);
      await put(`/api/items/${it.id}/units`, { units: (m.units || []).filter(u => u.name && u.factor > 0) });
      m.close(); toast('تم الحفظ'); return done();
    }
    const created = await post('/api/items', body);
    const wantsRecipe = $('#it-hasrec')?.checked && !body.is_service;
    m.close(); toast('تم الحفظ');
    if (wantsRecipe) return recipeModal(created, await api('/api/items'), done);
    done();
  });
}

PAGES.items = async app => {
  const list = await api('/api/items');
  const value = list.reduce((s, i) => s + i.value, 0);
  const edit = can('items');
  const showCost = can('reports') || can('purchases');
  app.innerHTML = `<div class="card"><h3><i class="i i-box"></i> المواد <span class="muted small">(${list.length})${showCost ? ` — قيمة المخزون ${fmt(value)}` : ''}</span><span class="sp"></span>
      <input id="it-search" placeholder="بحث…" style="width:200px">${edit ? '<button class="btn primary" id="it-new"><i class="i i-plus"></i> مادة جديدة</button>' : ''}</h3>
    ${list.length ? `<div class="table-wrap"><table id="it-table"><thead><tr><th>الرمز</th><th>المادة</th><th>الكمية</th>${showCost ? '<th>متوسط التكلفة</th>' : ''}<th>سعر البيع</th>${showCost ? '<th>هامش الربح</th><th>قيمة المخزون</th>' : ''}<th></th></tr></thead><tbody>
      
      </tbody></table></div><div id="it-more" class="small muted" style="margin-top:8px"></div>` : '<div class="empty">لا توجد مواد بعد.</div>'}</div>`;
  const MAX_ROWS = 300, rowHtml = i => `<tr data-name="${esc(i.code + ' ' + i.name + ' ' + (i.barcode || ''))}"><td class="muted">${esc(i.code)}</td><td>${esc(i.name)}</td>
        <td>${i.is_service ? '<span class="badge">غير مخزني</span>' : i.is_recipe ? '<span class="badge sale">وصفة</span>'
          : `${i.low ? `<span class="badge low">${fmt(i.qty)}</span>` : fmt(i.qty)} <span class="muted small">${esc(i.unit)}</span>`}</td>
        ${showCost ? `<td>${money(i.avg_cost)}</td>` : ''}<td>${money(i.sale_price)}</td>
        ${showCost ? `<td class="small">${i.avg_cost > 0 ? `<span class="${i.sale_price < i.avg_cost ? 'neg' : 'pos'}">${fmt((i.sale_price / i.avg_cost - 1) * 100)}%</span>` : '—'}</td><td>${money(i.value)}</td>` : ''}
        <td class="act">${edit ? `${i.is_service ? '' : `<button class="btn sm" data-rec="${i.id}" title="مكوّنات المنتج (وصفة)"><i class="i i-receipt"></i></button>`}<button class="btn sm" data-var="${i.id}" title="متغيرات (لون/قياس)"><i class="i i-layers"></i></button><button class="btn sm" data-ed="${i.id}"><i class="i i-edit"></i></button>` : ''}${edit && can('delete') ? `<button class="btn sm danger" data-del="${i.id}"><i class="i i-trash"></i></button>` : ''}</td></tr>`;
  const drawRows = () => {
    if (!$('#it-table')) return;
    const q = ($('#it-search').value || '').trim();
    const hit = q ? list.filter(i => i.name.includes(q) || i.code.includes(q) || (i.barcode || '').includes(q)) : list;
    $('#it-table tbody').innerHTML = hit.slice(0, MAX_ROWS).map(rowHtml).join('');
    $('#it-more').textContent = hit.length > MAX_ROWS ? `عُرض ${MAX_ROWS} من ${fmt(hit.length)} مادة — اكتب في البحث للوصول إلى مادة محددة` : '';
  };
  drawRows();

  if ($('#it-new')) $('#it-new').onclick = () => itemForm(null, route);
  $('#it-search').oninput = drawRows;
  app.querySelector('tbody')?.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.ed) itemForm(list.find(i => i.id == b.dataset.ed), route);
    if (b.dataset.var) variantsModal(list.find(i => i.id == b.dataset.var));
    if (b.dataset.rec) recipeModal(list.find(i => i.id == b.dataset.rec), list, route);
    if (b.dataset.del && await confirmBox('حذف هذه المادة؟')) run(b, async () => { await del(`/api/items/${b.dataset.del}`); toast('تم الحذف'); route(); });
  });
};

/** محرر مكوّنات المنتج: عدد غير محدود من المكوّنات، مع نسبة هدر، والمكوّن قد يكون تحضيرة لها مكوّناتها */
async function recipeModal(it, items, done) {
  const [rec, unitMap] = await Promise.all([api(`/api/items/${it.id}/recipe`), api('/api/recipe-units')]);
  const rows = rec.lines.map(l => ({ ingredient_id: l.ingredient_id, qty: l.unit_qty, unit_name: l.unit_name, waste_pct: l.waste_pct || 0 }));
  // وحدات المكوّن: الأساسية أولاً ثم ما يتيحه الخادم (غرام من كيلو، مل من لتر، وحدات المادة)
  const unitsOf = ing => unitMap[ing.id] || [{ name: ing.unit, factor: 1 }];
  const factorOf = (ing, r) => (unitsOf(ing).find(u => u.name === r.unit_name) || unitsOf(ing)[0]).factor;
  const costs = Object.fromEntries(rec.lines.map(l => [l.ingredient_id, l.unit_cost]));
  const ingredients = items.filter(i => !i.is_service && i.id !== it.id);
  const m = modal(`مكوّنات «${esc(it.name)}»`, `<datalist id="rc-dl">${ingredients.map(i => `<option value="${esc(i.code)} - ${esc(i.name)}">`).join('')}</datalist>
    <p class="small muted">أضف ما تشاء من المكوّنات بلا حد. الكمية لكل وحدة واحدة من المنتج، <b>واختر وحدتها</b>:
      لحمة مخزّنة بالكيلو تُدخلها بالغرام، وزيت باللتر تُدخله بالمل، والخبز بالرغيف.
      المكوّن يمكن أن يكون مادة خام أو <b>تحضيرة</b> لها مكوّناتها (صلصة، عجينة…) فتُخصم موادها الخام تلقائياً.</p>
    <div class="table-wrap" style="max-height:48vh;overflow-y:auto"><table class="lines-table small"><thead><tr><th style="width:34px">#</th><th>المكوّن</th>
      <th style="width:100px">الكمية</th><th style="width:104px">الوحدة</th><th style="width:84px">هدر %</th><th style="width:96px">التكلفة</th><th style="width:36px"></th></tr></thead>
      <tbody id="rc-rows"></tbody><tfoot><tr><td colspan="5">تكلفة المنتج (<span id="rc-n">0</span> مكوّن)</td><td id="rc-cost" class="num"></td><td></td></tr></tfoot></table></div>
    <div class="row" style="margin-top:8px"><button class="btn sm" id="rc-add"><i class="i i-plus"></i> مكوّن</button><button class="btn sm" id="rc-add5"><i class="i i-plus"></i> ٥ أسطر</button>
      <span class="sp"></span><span class="small" id="rc-margin"></span></div>
    <div class="foot"><button class="btn primary" id="rc-save">حفظ المكوّنات</button>
      ${rec.lines.length ? '<button class="btn danger" id="rc-clear">حذف كل المكوّنات</button>' : ''}<button class="btn" data-close>إلغاء</button></div>`, { wide: true });
  const find = t => ingredients.find(i => `${i.code} - ${i.name}` === t.trim() || i.code === t.trim() || i.name === t.trim());
  const unitCost = ing => costs[ing.id] ?? ing.avg_cost ?? 0;
  const totals = () => {
    let cost = 0, n = 0;
    rows.forEach((r, i) => {
      const ing = items.find(x => x.id === r.ingredient_id);
      const c = ing ? r.qty * factorOf(ing, r) * (1 + (r.waste_pct || 0) / 100) * unitCost(ing) : 0;
      if (ing && r.qty > 0) { cost += c; n++; }
      const cell = $(`#rc-c${i}`); if (cell) cell.textContent = fmt(c);
    });
    $('#rc-cost').textContent = fmt(cost); $('#rc-n').textContent = n;
    const pct = it.sale_price ? cost / it.sale_price * 100 : 0;
    $('#rc-margin').innerHTML = it.sale_price ? `سعر البيع ${fmt(it.sale_price)} — الهامش <b>${fmt(it.sale_price - cost)}</b> — نسبة التكلفة <b class="${pct > 40 ? 'neg' : 'pos'}">${fmt(pct)}%</b>` : '';
  };
  const draw = (extra = 0) => {
    for (let k = 0; k < extra; k++) rows.push({ ingredient_id: null, qty: 0, waste_pct: 0 });
    if (!rows.length || rows[rows.length - 1].ingredient_id) rows.push({ ingredient_id: null, qty: 0, waste_pct: 0 });
    $('#rc-rows').innerHTML = rows.map((r, i) => {
      const ing = items.find(x => x.id === r.ingredient_id);
      return `<tr><td class="muted">${i + 1}</td>
        <td><input list="rc-dl" data-i="${i}" data-f="ing" value="${ing ? esc(`${ing.code} - ${ing.name}`) : ''}">${ing && ing.is_recipe ? ' <span class="badge sale">تحضيرة</span>' : ''}</td>
        <td><input type="number" step="any" min="0" data-i="${i}" data-f="qty" value="${r.qty || ''}"></td>
        <td>${!ing ? '' : unitsOf(ing).length < 2 ? `<span class="small muted">${esc(ing.unit)}</span>`
          : `<select data-i="${i}" data-f="unit_name">${unitsOf(ing).map(u => `<option ${u.name === r.unit_name ? 'selected' : ''}>${esc(u.name)}</option>`).join('')}</select>`}</td>
        <td><input type="number" step="any" min="0" data-i="${i}" data-f="waste_pct" value="${r.waste_pct || ''}"></td>
        <td class="num" id="rc-c${i}"></td>
        <td>${r.ingredient_id ? `<button class="btn sm ghost" data-rm="${i}"><i class="i i-x"></i></button>` : ''}</td></tr>`;
    }).join('');
    totals();
  };
  $('#rc-add').onclick = () => draw(1);
  $('#rc-add5').onclick = () => draw(5);
  $('#rc-rows').onchange = ev => {
    const inp = ev.target.closest('[data-f]'); if (!inp) return;
    const r = rows[+inp.dataset.i];
    if (inp.dataset.f === 'unit_name') { r.unit_name = inp.value; totals(); }
    else if (inp.dataset.f === 'ing') { r.ingredient_id = find(inp.value)?.id || null; r.unit_name = null; draw(); const q = $(`[data-i="${inp.dataset.i}"][data-f="qty"]`); if (r.ingredient_id && q) q.focus(); }
    else { r[inp.dataset.f] = +inp.value || 0; totals(); }
  };
  $('#rc-rows').oninput = ev => {
    const inp = ev.target.closest('[data-f=qty],[data-f=waste_pct]');
    if (inp) { rows[+inp.dataset.i][inp.dataset.f] = +inp.value || 0; totals(); }
  };
  $('#rc-rows').onclick = ev => { const x = ev.target.closest('[data-rm]'); if (x) { rows.splice(+x.dataset.rm, 1); draw(); } };
  draw();
  const save = lines => run($('#rc-save'), async () => {
    const r = await put(`/api/items/${it.id}/recipe`, { lines });
    m.close(); toast(lines.length ? `حُفظ ${r.count} مكوّن — تكلفة المنتج ${fmt(r.cost)}` : 'حُذفت المكوّنات'); done && done();
  });
  $('#rc-save').onclick = () => save(rows.filter(r => r.ingredient_id && r.qty > 0));
  if ($('#rc-clear')) $('#rc-clear').onclick = async () => { if (await confirmBox('حذف كل مكوّنات هذا المنتج؟')) save([]); };
}

async function variantsModal(item) {
  const variants = await api(`/api/items/${item.id}/variants`);
  const m = modal(`متغيرات «${esc(item.name)}»`, `
    ${variants.length ? `<table class="small"><thead><tr><th>المتغير</th><th>الرمز</th><th>الكمية</th><th>سعر البيع</th></tr></thead><tbody>
      ${variants.map(v => `<tr><td>${esc(v.variant || v.name)}</td><td class="muted">${esc(v.code)}</td><td class="num">${fmt(v.qty)}</td><td class="num">${fmt(v.sale_price)}</td></tr>`).join('')}
    </tbody></table>` : '<p class="small muted">لا توجد متغيرات بعد. المتغير مادة مستقلة في المخزون (لون أو قياس) مرتبطة بهذه المادة.</p>'}
    <h3 style="margin-top:12px">إضافة متغير</h3>
    <div class="form-grid"><label class="f">اسم المتغير<input id="vr-name" placeholder="أحمر / مقاس 42"></label>
      <label class="f">سعر البيع<input type="number" step="any" id="vr-price" value="${item.sale_price}"></label>
      <label class="f">الباركود<input id="vr-bar"></label></div>
    <div class="foot"><button class="btn primary" id="vr-add"><i class="i i-plus"></i> إضافة</button><button class="btn" data-close>إغلاق</button></div>`);
  $('#vr-add').onclick = e => run(e.target, async () => {
    await post(`/api/items/${item.id}/variants`, { variant: $('#vr-name').value, sale_price: +$('#vr-price').value || 0,
      barcode: $('#vr-bar').value });
    m.close(); toast('أُضيف المتغير'); route();
  });
}

// ═════════════════════ المستودعات والتحويل ═════════════════════
PAGES.warehouses = async app => {
  const [whs, transfers] = await Promise.all([api('/api/warehouses'), api('/api/transfers')]);
  let items = await api(`/api/items?warehouse_id=${whs.find(w => w.active)?.id || ''}`);
  const active = whs.filter(w => w.active);
  app.innerHTML = `<div class="grid g2">
    <div class="card"><h3><i class="i i-warehouse"></i> المستودعات<span class="sp"></span><button class="btn primary sm" id="w-new"><i class="i i-plus"></i> مستودع</button></h3>
      <table><thead><tr><th>المستودع</th><th>عدد المواد</th><th>قيمة البضاعة</th><th></th></tr></thead><tbody>
      ${whs.map(w => `<tr><td>${esc(w.name)} ${w.active ? '' : '<span class="badge">موقوف</span>'}</td><td>${w.items}</td><td>${money(w.value)}</td>
        <td class="act"><button class="btn sm" data-ed="${w.id}"><i class="i i-edit"></i></button>${can('delete') ? `<button class="btn sm danger" data-del="${w.id}"><i class="i i-trash"></i></button>` : ''}</td></tr>`).join('')}
      </tbody></table></div>
    <div class="card"><h3><i class="i i-transfer"></i> تحويل بضاعة بين المستودعات</h3>
      ${active.length < 2 ? '<div class="empty">أضف مستودعاً ثانياً لتتمكن من التحويل.</div>' : `
      <div class="form-grid"><label class="f">من<select id="t-from">${whOptions(active[0].id)}</select></label>
        <label class="f">إلى<select id="t-to">${whOptions(active[1].id)}</select></label>
        <label class="f">التاريخ<input type="date" id="t-date" value="${today()}"></label></div>
      <datalist id="t-dl"></datalist>
      <table class="lines-table" style="margin-top:10px"><thead><tr><th>المادة</th><th style="width:90px">المتوفر</th><th style="width:110px">الكمية</th><th style="width:40px"></th></tr></thead><tbody id="t-lines"></tbody></table>
      <div class="row" style="margin-top:10px"><button class="btn" id="t-add"><i class="i i-plus"></i> سطر</button><span class="sp"></span><button class="btn primary" id="t-save"><i class="i i-transfer"></i> تنفيذ التحويل</button></div>`}
    </div></div>
    <div class="card"><h3><i class="i i-list"></i> سجل التحويلات</h3>${transfers.length ? `<table><thead><tr><th>#</th><th>التاريخ</th><th>من</th><th>إلى</th><th>المواد</th><th>الكمية</th></tr></thead><tbody>
      ${transfers.map(t => `<tr class="clickable" data-t="${t.id}"><td>${t.number}</td><td>${t.date}</td><td>${esc(t.from_name)}</td><td>${esc(t.to_name)}</td><td>${t.n}</td><td>${fmt(t.total_qty)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">لا توجد تحويلات</div>'}</div>`;

  const whForm = w => {
    const m = modal(w ? 'تعديل مستودع' : 'مستودع جديد', `<label class="f">الاسم<input id="w-name" value="${esc(w?.name)}"></label>
      ${w ? `<label class="row small" style="margin-top:10px"><input type="checkbox" id="w-active" ${w.active ? 'checked' : ''}> فعّال</label>` : ''}
      <div class="foot"><button class="btn primary" id="w-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#w-save').onclick = e => run(e.target, async () => {
      w ? await put(`/api/warehouses/${w.id}`, { name: $('#w-name').value, active: $('#w-active').checked }) : await post('/api/warehouses', { name: $('#w-name').value });
      m.close(); await loadSettings(); route();
    });
  };
  $('#w-new').onclick = () => whForm(null);
  app.querySelector('tbody').onclick = async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.ed) whForm(whs.find(w => w.id == b.dataset.ed));
    if (b.dataset.del && await confirmBox('حذف هذا المستودع؟')) run(b, async () => { await del(`/api/warehouses/${b.dataset.del}`); await loadSettings(); route(); });
  };
  $$('[data-t]', app).forEach(tr => tr.onclick = async () => {
    const t = await api(`/api/transfers/${tr.dataset.t}`);
    const m = modal(`تحويل رقم ${t.number}`, `<p class="small muted">${t.date} — من «${esc(t.from_name)}» إلى «${esc(t.to_name)}»${t.user_name ? ` — بواسطة ${esc(t.user_name)}` : ''}</p>
      <table><thead><tr><th>المادة</th><th>الكمية</th></tr></thead><tbody>${t.lines.map(l => `<tr><td>${esc(l.item_name)}</td><td>${fmt(l.qty)} ${esc(l.unit)}</td></tr>`).join('')}</tbody></table>
      <div class="foot">${can('delete') ? '<button class="btn danger" data-d><i class="i i-trash"></i> إلغاء التحويل</button>' : ''}<button class="btn" data-close>إغلاق</button></div>`);
    if ($('[data-d]', m.el)) $('[data-d]', m.el).onclick = e => run(e.target, async () => { await del(`/api/transfers/${t.id}`); toast('أُلغي التحويل'); m.close(); await loadSettings(); route(); });
  });

  if (active.length < 2) return;
  const lines = [];
  const find = t => items.find(i => `${i.code} - ${i.name}` === t.trim() || i.code === t.trim() || i.barcode === t.trim());
  const dl = () => { $('#t-dl').innerHTML = items.filter(i => i.qty > 0).map(i => `<option value="${esc(i.code)} - ${esc(i.name)}">`).join(''); };
  const draw = () => {
    if (!lines.length || lines[lines.length - 1].item_id) lines.push({ item_id: null, text: '', qty: 1 });
    $('#t-lines').innerHTML = lines.map((l, i) => {
      const it = items.find(x => x.id === l.item_id);
      return `<tr><td><input list="t-dl" data-i="${i}" data-f="text" value="${esc(it ? `${it.code} - ${it.name}` : l.text)}"></td>
        <td class="small muted">${it ? fmt(it.qty) : ''}</td><td><input type="number" min="0" step="any" data-i="${i}" data-f="qty" value="${l.qty}"></td>
        <td>${l.item_id ? `<button class="btn sm ghost" data-rm="${i}"><i class="i i-x"></i></button>` : ''}</td></tr>`;
    }).join('');
  };
  $('#t-lines').onchange = e => {
    const inp = e.target.closest('[data-f]'); if (!inp) return;
    const l = lines[+inp.dataset.i];
    if (inp.dataset.f === 'text') { l.text = inp.value; l.item_id = find(inp.value)?.id || null; draw(); } else l.qty = +inp.value || 0;
  };
  $('#t-lines').onclick = e => { const b = e.target.closest('[data-rm]'); if (b) { lines.splice(+b.dataset.rm, 1); draw(); } };
  $('#t-add').onclick = draw;
  $('#t-from').onchange = () => run(null, async () => { items = await api(`/api/items?warehouse_id=${$('#t-from').value}`); dl(); draw(); });
  $('#t-save').onclick = e => run(e.target, async () => {
    const t = await post('/api/transfers', { from_wh: +$('#t-from').value, to_wh: +$('#t-to').value, date: $('#t-date').value,
      lines: lines.filter(l => l.item_id).map(l => ({ item_id: l.item_id, qty: l.qty })) });
    toast(`نُفّذ التحويل رقم ${t.number}`); await loadSettings(); route();
  });
  dl(); draw();
};

// ═════════════════════ الأرصدة الافتتاحية ═════════════════════
PAGES.opening = async (app, tab = 'balances') => {
  const [op, accs, items] = await Promise.all([api('/api/opening'), api('/api/accounts'), api('/api/items')]);
  const plugOpts = accountOptions(accs, { selected: op.plug_account_id, filter: a => a.type === 'equity' });
  app.innerHTML = `<div class="tabs"><button data-t="balances" class="${tab === 'balances' ? 'on' : ''}">أرصدة الحسابات والعملاء والموردين</button>
      <button data-t="stock" class="${tab === 'stock' ? 'on' : ''}">بضاعة أول المدة</button></div>
    <div class="card"><div class="form-grid">
      <label class="f">تاريخ الأرصدة الافتتاحية<input type="date" id="o-date" value="${tab === 'stock' ? (op.stock_date || op.date) : op.date}"></label>
      <label class="f">حساب الفرق (يُقفل فيه الفرق بين المدين والدائن)<select id="o-plug">${plugOpts}</select></label></div>
      <p class="small muted">أدخل أرصدة الحسابات كما كانت عند بدء استخدام البرنامج. الحفظ يستبدل الأرصدة الافتتاحية السابقة بالكامل، ويمكنك العودة وتعديلها في أي وقت.</p></div>
    <div class="card" id="o-body"></div>`;
  $('.tabs', app).onclick = e => { const b = e.target.closest('button'); if (b) go(`#/opening/${b.dataset.t}`); };

  if (tab === 'stock') {
    const whs = S.settings.warehouses || [];
    const lines = op.stock.map(s => ({ item_id: s.item_id, warehouse_id: s.warehouse_id, qty: s.qty, unit_cost: s.unit_cost }));
    $('#o-body').innerHTML = `<datalist id="o-dl">${items.map(i => `<option value="${esc(i.code)} - ${esc(i.name)}">`).join('')}</datalist>
      <div class="table-wrap"><table class="lines-table"><thead><tr><th>المادة</th><th style="width:170px">المستودع</th><th style="width:110px">الكمية</th><th style="width:130px">تكلفة الوحدة</th><th style="width:130px">القيمة</th><th style="width:40px"></th></tr></thead>
      <tbody id="os-lines"></tbody><tfoot><tr><td colspan="4">إجمالي بضاعة أول المدة</td><td id="os-total" class="num"></td><td></td></tr></tfoot></table></div>
      <div class="row" style="margin-top:12px"><span class="muted small">القيمة تُرحَّل: من حـ/ المخزون إلى حـ/ الفرق المختار.</span><span class="sp"></span><button class="btn primary" id="os-save"><i class="i i-save"></i> حفظ بضاعة أول المدة</button></div>`;
    const find = t => items.find(i => `${i.code} - ${i.name}` === t.trim() || i.code === t.trim());
    const draw = () => {
      if (!lines.length || lines[lines.length - 1].item_id) lines.push({ item_id: null, warehouse_id: whs[0]?.id, qty: 0, unit_cost: 0 });
      $('#os-lines').innerHTML = lines.map((l, i) => {
        const it = items.find(x => x.id === l.item_id);
        return `<tr><td><input list="o-dl" data-i="${i}" data-f="text" value="${it ? esc(`${it.code} - ${it.name}`) : ''}"></td>
          <td><select data-i="${i}" data-f="warehouse_id">${whOptions(l.warehouse_id, false)}</select></td>
          <td><input type="number" step="any" min="0" data-i="${i}" data-f="qty" value="${l.qty}"></td>
          <td><input type="number" step="any" min="0" data-i="${i}" data-f="unit_cost" value="${l.unit_cost}"></td>
          <td class="num" id="os-v${i}">${fmt(l.qty * l.unit_cost)}</td><td>${l.item_id ? `<button class="btn sm ghost" data-rm="${i}"><i class="i i-x"></i></button>` : ''}</td></tr>`;
      }).join('');
      tot();
    };
    const tot = () => { $('#os-total').textContent = fmt(lines.reduce((s, l) => s + (l.item_id ? l.qty * l.unit_cost : 0), 0)); lines.forEach((l, i) => { const c = $(`#os-v${i}`); if (c) c.textContent = fmt(l.qty * l.unit_cost); }); };
    $('#os-lines').onchange = e => {
      const inp = e.target.closest('[data-f]'); if (!inp) return;
      const l = lines[+inp.dataset.i];
      if (inp.dataset.f === 'text') { const it = find(inp.value); l.item_id = it?.id || null; if (it && !l.unit_cost) l.unit_cost = +it.avg_cost.toFixed(2); draw(); }
      else { l[inp.dataset.f] = +inp.value || 0; tot(); }
    };
    $('#os-lines').oninput = e => { const inp = e.target.closest('[data-f=qty],[data-f=unit_cost]'); if (inp) { lines[+inp.dataset.i][inp.dataset.f] = +inp.value || 0; tot(); } };
    $('#os-lines').onclick = e => { const b = e.target.closest('[data-rm]'); if (b) { lines.splice(+b.dataset.rm, 1); draw(); } };
    $('#os-save').onclick = e => run(e.target, async () => {
      const r = await post('/api/opening/stock', { date: $('#o-date').value, plug_account_id: +$('#o-plug').value, lines: lines.filter(l => l.item_id) });
      toast(`حُفظت بضاعة أول المدة بقيمة ${fmt(r.total)}`); await loadSettings(); route();
    });
    return draw();
  }

  const existing = Object.fromEntries(op.lines.map(l => [l.account_id, l]));
  const leaf = accs.filter(a => !a.is_group && a.code !== '114' && a.id !== op.plug_account_id && a.type !== 'revenue' && a.type !== 'expense');
  $('#o-body').innerHTML = `<div class="row" style="margin-bottom:10px"><input id="o-search" placeholder="بحث عن حساب أو عميل أو مورد…" style="width:280px"><span class="sp"></span>
      <span class="small">مدين: <b id="o-td" class="num">0</b> — دائن: <b id="o-tc" class="num">0</b> — الفرق إلى حساب الفرق: <b id="o-diff" class="num">0</b></span></div>
    <div class="table-wrap" style="max-height:60vh;overflow-y:auto"><table class="lines-table"><thead><tr><th>الحساب</th><th>النوع</th><th style="width:150px">مدين</th><th style="width:150px">دائن</th></tr></thead><tbody>
    ${leaf.map(a => `<tr data-name="${esc(a.code + ' ' + a.name)}"><td><span class="muted">${esc(a.code)}</span> ${esc(a.name)}</td><td class="small muted">${TYPE[a.type]}</td>
      <td><input type="number" step="any" min="0" data-a="${a.id}" data-f="debit" value="${existing[a.id]?.debit || ''}"></td>
      <td><input type="number" step="any" min="0" data-a="${a.id}" data-f="credit" value="${existing[a.id]?.credit || ''}"></td></tr>`).join('')}
    </tbody></table></div>
    <p class="small muted">تلميح: العميل الذي عليه دين لك ← مدين. المورد الذي تدين له ← دائن. الصندوق والبنك ← مدين. رصيد المخزون يُدخل من تبويب «بضاعة أول المدة».</p>
    <div class="row"><span class="sp"></span><button class="btn primary" id="o-save"><i class="i i-save"></i> حفظ الأرصدة الافتتاحية</button></div>`;
  filterRows($('#o-search'), $('#o-body'));
  const tot = () => {
    let d = 0, c = 0;
    $$('#o-body input[data-f=debit]').forEach(i => d += +i.value || 0);
    $$('#o-body input[data-f=credit]').forEach(i => c += +i.value || 0);
    $('#o-td').textContent = fmt(d); $('#o-tc').textContent = fmt(c); $('#o-diff').textContent = fmt(d - c);
  };
  $('#o-body').oninput = tot;
  tot();
  $('#o-save').onclick = e => run(e.target, async () => {
    const byAcc = {};
    $$('#o-body input[data-a]').forEach(i => { (byAcc[i.dataset.a] ||= { account_id: +i.dataset.a })[i.dataset.f] = +i.value || 0; });
    const r = await post('/api/opening/balances', { date: $('#o-date').value, plug_account_id: +$('#o-plug').value, lines: Object.values(byAcc) });
    toast(r.journal_id ? `حُفظ القيد الافتتاحي${r.difference ? ` (الفرق ${fmt(r.difference)} إلى حساب الفرق)` : ''}` : 'حُذفت الأرصدة الافتتاحية');
    route();
  });
};

// ═════════════════════ التقارير ═════════════════════
PAGES.reports = async (app, arg = 'trial') => {
  const [tab, param] = arg.split(':');
  const accs = await api('/api/accounts');
  const tabs = { trial: 'ميزان المراجعة', statement: 'كشف حساب', income: 'قائمة الدخل', balance: 'الميزانية العمومية',
                 aging: 'أعمار الديون', installments: 'الأقساط المستحقة', cashflow: 'التدفقات النقدية',
                 budget: 'الموازنة التقديرية', vat: 'ضريبة القيمة المضافة', stock: 'جرد المخزون',
                 expiry: 'الصلاحية والدفعات' };
  if (S.settings.restaurant_mode === '1') tabs.restaurant = 'مبيعات المطعم';
  app.innerHTML = `<div class="tabs">${Object.entries(tabs).map(([k, v]) => `<button data-t="${k}" class="${k === tab ? 'on' : ''}">${v}</button>`).join('')}</div>
    <div class="card no-print"><div class="row" id="r-filters"></div></div><div class="card" id="r-out"></div>`;
  $('.tabs', app).onclick = e => { const b = e.target.closest('button'); if (b) go(`#/reports/${b.dataset.t}`); };
  const F = $('#r-filters'), O = $('#r-out');
  const printBtn = '<button class="btn" id="r-print"><i class="i i-print"></i> طباعة</button>';
  const bindPrint = title => { $('#r-print').onclick = () => printHTML(title, O.innerHTML); };
  const dates = (from, to) => `${from !== null ? `<label class="row small">من <input type="date" id="r-from" value="${from}"></label>` : ''}<label class="row small">${from !== null ? 'إلى' : 'حتى تاريخ'} <input type="date" id="r-to" value="${to}"></label>`;

  const R = {
    async trial() {
      F.innerHTML = dates(null, today()) + '<button class="btn primary" id="r-go">عرض</button><span class="sp"></span>' + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/trial-balance?to_date=${$('#r-to').value}`);
        O.innerHTML = `<div class="table-wrap"><table><thead><tr><th>الرمز</th><th>الحساب</th><th>مجموع مدين</th><th>مجموع دائن</th><th>رصيد مدين</th><th>رصيد دائن</th></tr></thead><tbody>
          ${d.rows.map(r => `<tr class="clickable" data-st="${r.id}"><td class="muted">${esc(r.code)}</td><td>${esc(r.name)}</td><td>${money(r.debit)}</td><td>${money(r.credit)}</td><td>${r.bal_debit ? money(r.bal_debit) : ''}</td><td>${r.bal_credit ? money(r.bal_credit) : ''}</td></tr>`).join('')}
          </tbody><tfoot><tr><td colspan="2">الإجمالي ${d.balanced ? '<span class="pos"><i class="i i-check"></i> متوازن</span>' : '<span class="neg"><i class="i i-x"></i> غير متوازن</span>'}</td>
          <td>${money(d.totals.debit)}</td><td>${money(d.totals.credit)}</td><td>${money(d.totals.bal_debit)}</td><td>${money(d.totals.bal_credit)}</td></tr></tfoot></table></div>`;
        $$('[data-st]', O).forEach(tr => tr.onclick = () => go(`#/reports/statement:${tr.dataset.st}`));
      };
      $('#r-go').onclick = load; bindPrint('ميزان المراجعة'); await load();
    },
    async statement() {
      F.innerHTML = `<select id="r-acc" style="min-width:260px">${accountOptions(accs, { leafOnly: false, selected: param })}</select>` + dates(yearStart(), today()) + '<button class="btn primary" id="r-go">عرض</button><span class="sp"></span>' + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/statement/${$('#r-acc').value}?date_from=${$('#r-from').value}&date_to=${$('#r-to').value}`);
        O.innerHTML = statementTable(d, $('#r-from').value, $('#r-to').value);
        $$('[data-j]', O).forEach(tr => tr.onclick = () => showJournal(+tr.dataset.j, load));
      };
      $('#r-go').onclick = load; $('#r-acc').onchange = load; bindPrint('كشف حساب'); await load();
    },
    async income() {
      F.innerHTML = dates(monthStart(), today()) + '<button class="btn primary" id="r-go">عرض</button><span class="sp"></span>' + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/income?date_from=${$('#r-from').value}&date_to=${$('#r-to').value}`);
        const sec = (t, list, tot) => `<tr><td colspan="2"><b>${t}</b></td></tr>${list.map(x => `<tr><td style="padding-right:26px">${esc(x.name)}</td><td>${money(x.amount)}</td></tr>`).join('')}<tr><td><b>إجمالي ${t}</b></td><td><b>${money(tot)}</b></td></tr>`;
        O.innerHTML = `<h3>قائمة الدخل من ${$('#r-from').value} إلى ${$('#r-to').value}</h3><table style="max-width:640px"><tbody>
          ${sec('الإيرادات', d.revenue, d.total_revenue)}${sec('المصروفات', d.expense, d.total_expense)}</tbody><tfoot>
          <tr><td>مجمل الربح (الإيرادات − تكلفة المبيعات)</td><td>${money(d.gross_profit)}</td></tr>
          <tr><td>${d.net_profit >= 0 ? 'صافي الربح' : 'صافي الخسارة'}</td><td class="${d.net_profit >= 0 ? 'pos' : 'neg'}" style="font-size:17px">${money(d.net_profit)}</td></tr></tfoot></table>`;
      };
      $('#r-go').onclick = load; bindPrint('قائمة الدخل'); await load();
    },
    async balance() {
      F.innerHTML = dates(null, today()) + '<button class="btn primary" id="r-go">عرض</button><span class="sp"></span>' + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/balance-sheet?as_of=${$('#r-to').value}`);
        const depthOf = a => { let n = 0, p = a; while (p?.parent_id) { n++; p = accs.find(x => x.id === p.parent_id); } return n; };
        const sec = list => list.map(a => `<tr><td style="padding-right:${depthOf(a) * 16}px">${a.is_group ? '<b>' : ''}${esc(a.name)}${a.is_group ? '</b>' : ''}</td><td>${money(a.balance)}</td></tr>`).join('');
        O.innerHTML = `<h3>الميزانية العمومية في ${$('#r-to').value} ${d.balanced ? '<span class="badge sale"><i class="i i-check"></i> متوازنة</span>' : '<span class="badge low">غير متوازنة</span>'}</h3>
          <div class="grid g2"><table><thead><tr><th>الأصول</th><th></th></tr></thead><tbody>${sec(d.assets)}</tbody><tfoot><tr><td>إجمالي الأصول</td><td>${money(d.total_assets)}</td></tr></tfoot></table>
          <table><thead><tr><th>الخصوم وحقوق الملكية</th><th></th></tr></thead><tbody>${sec(d.liabilities)}
          <tr><td><b>إجمالي الخصوم</b></td><td><b>${money(d.total_liabilities)}</b></td></tr>${sec(d.equity)}
          <tr><td>أرباح (خسائر) الفترة غير المدوّرة</td><td>${money(d.period_profit)}</td></tr></tbody>
          <tfoot><tr><td>إجمالي الخصوم وحقوق الملكية</td><td>${money(d.total_liabilities + d.total_equity)}</td></tr></tfoot></table></div>`;
      };
      $('#r-go').onclick = load; bindPrint('الميزانية العمومية'); await load();
    },
    async vat() {
      F.innerHTML = dates(monthStart(), today()) + '<button class="btn primary" id="r-go">عرض</button><span class="sp"></span>' + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/vat?date_from=${$('#r-from').value}&date_to=${$('#r-to').value}`);
        O.innerHTML = `<h3>إقرار ضريبة القيمة المضافة من ${$('#r-from').value} إلى ${$('#r-to').value}</h3>
          ${S.settings.vat_enabled ? '' : '<p class="small muted">ضريبة القيمة المضافة غير مفعّلة حالياً — يمكن تفعيلها من الإعدادات.</p>'}
          <div class="grid g4" style="margin-bottom:14px">
            <div class="card kpi"><span class="t">المبيعات الخاضعة</span><span class="v">${money(d.taxable_sales)}</span></div>
            <div class="card kpi"><span class="t">ضريبة المخرجات (على المبيعات)</span><span class="v">${money(d.output_tax)}</span></div>
            <div class="card kpi"><span class="t">ضريبة المدخلات (على المشتريات)</span><span class="v">${money(d.input_tax)}</span></div>
            <div class="card kpi hl"><span class="t">${d.payable >= 0 ? 'الصافي المستحق للدفع' : 'رصيد دائن قابل للاسترداد'}</span><span class="v">${money(Math.abs(d.payable))}</span></div></div>
          ${d.invoices.length ? `<table><thead><tr><th>التاريخ</th><th>الفاتورة</th><th>الطرف</th><th>النسبة</th><th>الصافي</th><th>الضريبة</th></tr></thead><tbody>
            ${d.invoices.map(i => `<tr class="clickable" data-inv="${i.id}"><td>${i.date}</td><td><span class="badge ${i.kind}">${i.kind_label}</span> ${i.number}</td><td>${esc(i.party_name || 'نقدي')}</td><td>${fmt(i.tax_rate)}%</td><td>${money(i.net_base)}</td><td>${money(i.tax_base)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">لا توجد فواتير خاضعة للضريبة في هذه الفترة</div>'}`;
        bindInvoiceRows(O, load);
      };
      $('#r-go').onclick = load; bindPrint('إقرار ضريبة القيمة المضافة'); await load();
    },
    async aging() {
      F.innerHTML = `<select id="r-kind"><option value="customer">ذمم العملاء (لنا)</option><option value="supplier">ذمم الموردين (علينا)</option></select>`
        + dates(null, today()) + '<button class="btn primary" id="r-go">عرض</button><span class="sp"></span>' + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/aging?kind=${$('#r-kind').value}&as_of=${$('#r-to').value}`);
        O.innerHTML = `<h3>أعمار الديون — ${$('#r-kind').value === 'customer' ? 'العملاء' : 'الموردون'} في ${d.as_of}</h3>
          ${d.parties.length ? `<div class="table-wrap"><table><thead><tr><th>الاسم</th><th>الهاتف</th>
            ${d.buckets.map(b => `<th>${b}</th>`).join('')}<th>الإجمالي</th><th>أقدم دين</th></tr></thead><tbody>
            ${d.parties.map(p => `<tr><td>${esc(p.name)}</td><td class="small muted">${esc(p.phone || '')}</td>
              ${d.buckets.map(b => `<td>${p[b] ? money(p[b]) : '<span class="muted">—</span>'}</td>`).join('')}
              <td><b>${money(p.total)}</b></td><td class="small ${p.oldest_days > 90 ? 'neg' : 'muted'}">${p.oldest_days} يوم</td></tr>`).join('')}
            </tbody><tfoot><tr><td colspan="2">الإجمالي</td>${d.buckets.map(b => `<td>${money(d.totals[b])}</td>`).join('')}
            <td>${money(d.grand_total)}</td><td></td></tr></tfoot></table></div>` : '<div class="empty">لا توجد ذمم غير مسدَّدة </div>'}
          ${d.unallocated.length ? `<p class="small muted">دفعات غير مخصَّصة على فواتير: ${d.unallocated.map(u => `${esc(u.name)} (${fmt(u.amount)})`).join('، ')} — خصّصها من شاشة السندات ليصبح التقرير أدق.</p>` : ''}`;
      };
      $('#r-go').onclick = load; $('#r-kind').onchange = load; bindPrint('أعمار الديون'); await load();
    },
    async restaurant() {
      F.innerHTML = dates(monthStart(), today()) + '<button class="btn primary" id="r-go">عرض</button><span class="sp"></span>' + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/restaurant?date_from=${$('#r-from').value}&date_to=${$('#r-to').value}`);
        const guests = d.by_kind.reduce((a, k) => a + k.guests, 0);
        O.innerHTML = `<div class="grid g4" style="margin-bottom:14px">
            <div class="card kpi hl"><span class="t">إجمالي المبيعات</span><span class="v">${money(d.total)}</span><span class="s">${d.orders} طلب</span></div>
            <div class="card kpi"><span class="t">متوسط الطلب</span><span class="v">${money(d.orders ? d.total / d.orders : 0)}</span></div>
            <div class="card kpi"><span class="t">عدد الزبائن</span><span class="v">${fmt(guests)}</span><span class="s">متوسط الفرد ${fmt(guests ? d.total / guests : 0)}</span></div>
            <div class="card kpi"><span class="t">طلبات ملغاة</span><span class="v ${d.canceled ? 'neg' : ''}">${d.canceled}</span></div></div>
          <div class="grid g2">
            <div><h3>حسب نوع الطلب</h3><table><thead><tr><th>النوع</th><th>الطلبات</th><th>المبيعات</th><th>المتوسط</th></tr></thead><tbody>
              ${d.by_kind.map(k => `<tr><td>${k.kind_label}</td><td class="num">${k.n}</td><td>${money(k.total)}</td><td>${money(k.avg)}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">لا بيانات</td></tr>'}</tbody></table>
              <h3 style="margin-top:14px">حسب النادل</h3><table><tbody>
              ${d.by_waiter.map(w => `<tr><td>${esc(w.name)}</td><td class="num">${w.n} طلب</td><td>${money(w.total)}</td></tr>`).join('') || '<tr><td class="empty">لا بيانات</td></tr>'}</tbody></table></div>
            <div><h3>حسب التصنيف</h3><table><thead><tr><th>التصنيف</th><th>المبيعات</th><th>الربح</th></tr></thead><tbody>
              ${d.by_category.map(c => `<tr><td>${esc(c.category)}</td><td>${money(c.total)}</td><td>${money(c.profit)}</td></tr>`).join('') || '<tr><td colspan="3" class="empty">لا بيانات</td></tr>'}</tbody></table></div></div>
          <h3 style="margin-top:14px">الأصناف الأكثر مبيعاً</h3>
          <div class="table-wrap"><table><thead><tr><th>الصنف</th><th>التصنيف</th><th>الكمية</th><th>المبيعات</th><th>التكلفة</th><th>الربح</th></tr></thead><tbody>
            ${d.by_item.map(i => `<tr><td>${esc(i.name)}</td><td class="small muted">${esc(i.category)}</td><td class="num">${fmt(i.qty)}</td>
              <td>${money(i.total)}</td><td>${money(i.cost)}</td><td><b>${money(i.profit)}</b></td></tr>`).join('') || '<tr><td colspan="6" class="empty">لا مبيعات في هذه الفترة</td></tr>'}</tbody></table></div>`;
      };
      $('#r-go').onclick = load; bindPrint('مبيعات المطعم'); await load();
    },
    async installments() {
      F.innerHTML = dates(null, today()) + `<label class="row small"><input type="checkbox" id="r-late"> المتأخرة فقط</label>
        <button class="btn primary" id="r-go">عرض</button><span class="sp"></span>` + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/installments?as_of=${$('#r-to').value}&only_late=${$('#r-late').checked}`);
        O.innerHTML = `<div class="grid g2" style="margin-bottom:14px">
            <div class="card kpi"><span class="t">إجمالي الأقساط غير المسدَّدة</span><span class="v">${money(d.total)}</span></div>
            <div class="card kpi"><span class="t">منها متأخرة</span><span class="v neg">${money(d.late)}</span></div></div>
          ${d.rows.length ? `<div class="table-wrap"><table><thead><tr><th>الاستحقاق</th><th>العميل</th><th>الفاتورة</th><th>القسط</th>
            <th>قيمة القسط</th><th>المسدَّد</th><th>المتبقي</th><th>الحالة</th></tr></thead><tbody>
            ${d.rows.map(r => `<tr class="${r.days_late > 0 ? 'neg' : ''}"><td class="num">${r.due_date}</td><td>${esc(r.party_name)}</td>
              <td class="clickable" data-inv="${r.invoice_id}">#${r.invoice_number}</td><td class="num">${r.seq}</td>
              <td>${money(r.amount)}</td><td>${money(r.paid)}</td><td><b>${money(r.remaining)}</b></td>
              <td>${r.days_late > 0 ? `<span class="badge low">متأخر ${r.days_late} يوم</span>` : '<span class="badge">قادم</span>'}</td></tr>`).join('')}
            </tbody></table></div>` : '<div class="empty">لا توجد أقساط مستحقة</div>'}
          <p class="small muted">تُنشأ خطة الأقساط من نافذة الفاتورة الآجلة، ويُوزَّع المسدَّد على الأقساط بالترتيب.</p>`;
        bindInvoiceRows(O, load);
      };
      $('#r-go').onclick = load; $('#r-late').onchange = load; bindPrint('الأقساط المستحقة'); await load();
    },
    async cashflow() {
      F.innerHTML = dates(yearStart(), today()) + '<button class="btn primary" id="r-go">عرض</button><span class="sp"></span>' + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/cash-flow?date_from=${$('#r-from').value}&date_to=${$('#r-to').value}`);
        const sec = (title, list, total, hint) => `<tr><td colspan="2"><b>${title}</b> <span class="muted small">${hint}</span></td></tr>
          ${list.map(x => `<tr><td style="padding-right:26px">${esc(x.name)}</td><td>${money(x.amount)}</td></tr>`).join('') || '<tr><td colspan="2" class="muted" style="padding-right:26px">—</td></tr>'}
          <tr><td><b>صافي ${title}</b></td><td><b>${money(total)}</b></td></tr>`;
        O.innerHTML = `<h3>قائمة التدفقات النقدية من ${$('#r-from').value} إلى ${$('#r-to').value}</h3>
          <table style="max-width:680px"><tbody>
            <tr><td>النقد في بداية الفترة</td><td>${money(d.opening)}</td></tr>
            ${sec('الأنشطة التشغيلية', d.operating, d.totals.operating, '(مبيعات، مشتريات، مصاريف، ذمم)')}
            ${sec('الأنشطة الاستثمارية', d.investing, d.totals.investing, '(شراء وبيع الأصول الثابتة)')}
            ${sec('الأنشطة التمويلية', d.financing, d.totals.financing, '(رأس المال، القروض، المسحوبات)')}
          </tbody><tfoot>
            <tr><td>صافي التغير في النقد</td><td class="${d.net < 0 ? 'neg' : 'pos'}">${money(d.net)}</td></tr>
            <tr><td>النقد في نهاية الفترة</td><td style="font-size:17px">${money(d.closing)}</td></tr></tfoot></table>`;
      };
      $('#r-go').onclick = load; bindPrint('التدفقات النقدية'); await load();
    },
    async budget() {
      F.innerHTML = `<label class="row small">السنة <input id="r-year" value="${today().slice(0, 4)}" style="width:90px"></label>
        <button class="btn primary" id="r-go">عرض</button><button class="btn" id="r-edit"><i class="i i-edit"></i> تعديل الموازنة</button><span class="sp"></span>` + printBtn;
      let data = null;
      const load = async () => {
        data = await api(`/api/budgets?year=${$('#r-year').value}`);
        const sec = (title, list) => `<tr><td colspan="5"><b>${title}</b></td></tr>
          ${list.map(x => `<tr><td style="padding-right:26px">${esc(x.name)}</td><td>${money(x.budget)}</td><td>${money(x.actual)}</td>
            <td class="${x.variance < 0 ? 'neg' : 'pos'}">${money(x.variance)}</td>
            <td>${x.pct == null ? '—' : `<div class="bar"><span style="width:${Math.min(100, x.pct)}%"></span></div> ${fmt(x.pct)}%`}</td></tr>`).join('')
            || '<tr><td colspan="5" class="muted" style="padding-right:26px">لا بيانات</td></tr>'}`;
        const t = data.totals;
        O.innerHTML = `<h3>الموازنة التقديرية مقابل الفعلي — ${data.year}</h3>
          <div class="table-wrap"><table><thead><tr><th>الحساب</th><th>الموازنة</th><th>الفعلي</th><th>الفرق</th><th>نسبة التحقق</th></tr></thead><tbody>
            ${sec('الإيرادات', data.revenue)}${sec('المصروفات', data.expense)}</tbody>
          <tfoot><tr><td>صافي الموازنة المتوقع</td><td>${money(t.budget_revenue - t.budget_expense)}</td>
            <td>${money(t.actual_revenue - t.actual_expense)}</td><td colspan="2"></td></tr></tfoot></table></div>`;
      };
      $('#r-go').onclick = load;
      $('#r-edit').onclick = async () => {
        const accs = (await api('/api/accounts')).filter(a => !a.is_group && (a.type === 'revenue' || a.type === 'expense'));
        const cur = Object.fromEntries([...data.revenue, ...data.expense].map(x => [x.account_id, x.budget]));
        const m = modal(`تعديل موازنة ${data.year}`, `<div class="table-wrap" style="max-height:55vh;overflow-y:auto">
            <table class="lines-table"><thead><tr><th>الحساب</th><th>النوع</th><th style="width:150px">مبلغ الموازنة</th></tr></thead><tbody>
            ${accs.map(a => `<tr><td>${esc(a.code)} — ${esc(a.name)}</td><td class="small muted">${TYPE[a.type]}</td>
              <td><input type="number" step="any" min="0" data-a="${a.id}" value="${cur[a.id] || ''}"></td></tr>`).join('')}
            </tbody></table></div>
          <div class="foot"><button class="btn primary" id="bd-save">حفظ الموازنة</button><button class="btn" data-close>إلغاء</button></div>`, { wide: true });
        $('#bd-save').onclick = e => run(e.target, async () => {
          const items = $$('[data-a]', m.el).map(i => ({ account_id: +i.dataset.a, amount: +i.value || 0 })).filter(x => x.amount);
          await put('/api/budgets', { year: data.year, items });
          m.close(); toast('حُفظت الموازنة'); load();
        });
      };
      bindPrint('الموازنة التقديرية'); await load();
    },
    async expiry() {
      F.innerHTML = `<label class="row small">المواد التي تنتهي خلال <input type="number" id="r-days" value="${S.settings.expiry_alert_days || 30}" style="width:80px"> يوماً</label>
        <button class="btn primary" id="r-go">عرض</button><span class="sp"></span>` + printBtn;
      const load = async () => {
        const d = await api(`/api/reports/expiry?days=${+$('#r-days').value || 30}`);
        O.innerHTML = `<div class="grid g3" style="margin-bottom:14px">
            <div class="card kpi"><span class="t">دفعات منتهية</span><span class="v neg">${money(d.expired_value)}</span><span class="s">قيمتها بالتكلفة</span></div>
            <div class="card kpi"><span class="t">قاربت على الانتهاء</span><span class="v">${money(d.soon_value)}</span><span class="s">خلال ${d.days} يوماً</span></div>
            <div class="card kpi"><span class="t">عدد الدفعات</span><span class="v">${d.rows.length}</span></div></div>
          ${d.rows.length ? `<div class="table-wrap"><table><thead><tr><th>المادة</th><th>المستودع</th><th>رقم الدفعة</th><th>الصلاحية</th><th>المتبقي</th><th>الكمية</th><th>القيمة</th></tr></thead><tbody>
            ${d.rows.map(b => `<tr class="${b.expired ? 'neg' : ''}"><td>${esc(b.item_name)}</td><td class="small">${esc(b.warehouse_name)}</td>
              <td>${esc(b.code || '—')}</td><td class="num">${b.expiry}</td>
              <td class="${b.expired ? 'neg' : b.days_left <= 7 ? 'neg' : ''}">${b.expired ? 'منتهية' : b.days_left + ' يوم'}</td>
              <td class="num">${fmt(b.qty)} ${esc(b.unit)}</td><td>${money(b.value)}</td></tr>`).join('')}
            </tbody></table></div>` : '<div class="empty">لا توجد دفعات قاربت على الانتهاء </div>'}
          <p class="small muted">تظهر هنا المواد المفعّل عليها «تتبّع الدفعات» فقط. البيع يخرج دائماً من الدفعة الأقرب انتهاءً.</p>`;
      };
      $('#r-go').onclick = load; bindPrint('تقرير الصلاحية'); await load();
    },
    async stock() {
      F.innerHTML = '<input id="r-search" placeholder="بحث عن مادة…" style="width:220px"><span class="sp"></span>' + printBtn;
      const d = await api('/api/reports/stock');
      const multi = d.warehouses.length > 1;
      O.innerHTML = `<div class="table-wrap"><table><thead><tr><th>الرمز</th><th>المادة</th>${multi ? d.warehouses.map(w => `<th>${esc(w.name)}</th>`).join('') : ''}<th>إجمالي الكمية</th><th>متوسط التكلفة</th><th>القيمة</th><th>سعر البيع</th></tr></thead><tbody>
        ${d.items.map(i => `<tr data-name="${esc(i.code + ' ' + i.name)}"><td>${esc(i.code)}</td><td>${esc(i.name)} ${i.low ? '<span class="badge low">ناقص</span>' : ''}</td>
          ${multi ? d.warehouses.map(w => `<td>${i.stock[w.id] ? fmt(i.stock[w.id]) : '<span class="muted">—</span>'}</td>`).join('') : ''}
          <td>${fmt(i.qty)} ${esc(i.unit)}</td><td>${money(i.avg_cost)}</td><td>${money(i.value)}</td><td>${money(i.sale_price)}</td></tr>`).join('')}
        </tbody><tfoot><tr><td colspan="2">القيمة الإجمالية</td>${multi ? d.warehouses.map(w => `<td>${money(d.totals[w.id])}</td>`).join('') : ''}<td></td><td></td><td>${money(d.total_value)}</td><td></td></tr></tfoot></table></div>`;
      filterRows($('#r-search'), O);
      bindPrint('جرد المخزون');
    },
  };
  await (R[tab] || R.trial)();
};

// ═════════════════════ المساعد الذكي ═════════════════════
const SUGGESTIONS = [
  'كيف وضعي المالي هذا الشهر؟',
  'من هم العملاء الذين عليهم أكبر ديون؟',
  'دفعت فاتورة كهرباء 150 نقداً اليوم',
  'ما هي المواد الأكثر ربحاً؟',
  'كم ضريبة القيمة المضافة المستحقة هذا الشهر؟',
  'أين توجد أكبر كمية بضاعة بين المستودعات؟',
];

PAGES.assistant = async app => {
  app.innerHTML = `<div class="card chat">
    <div class="chat-log" id="log"></div>
    <div style="padding:0 12px 8px" class="chips" id="chips">${SUGGESTIONS.map(s => `<button class="chip">${esc(s)}</button>`).join('')}</div>
    <div class="chat-input"><textarea id="msg" placeholder="اسأل عن أرقامك أو صِف عملية مالية ليقترح لك القيد… (Enter للإرسال)"></textarea>
      <button class="btn ai" id="send">إرسال</button><button class="btn ghost" id="reset" title="محادثة جديدة"><i class="i i-trash"></i></button></div></div>`;
  const log = $('#log');
  const render = () => {
    log.innerHTML = S.chat.length ? '' : `<div class="empty">أهلاً ${esc(S.settings.user.full_name)}! أنا المحاسب الذكي. أستطيع قراءة حساباتك، تحليل الأرباح والمبيعات، ومتابعة الديون والضريبة والمخزون،
      وإذا وصفت لي عملية مالية بكلامك سأقترح لك القيد المحاسبي الصحيح لترحّله بضغطة زر.
      ${S.settings.ai_ready ? '' : `<br><br>لم يتم ضبط مفتاح الذكاء الاصطناعي بعد${can('settings') ? ' — <a href="#/settings">اضبطه من الإعدادات</a>' : '، راجع المدير'}`}</div>`;
    S.chat.forEach((m, idx) => {
      const div = document.createElement('div');
      div.className = `msg ${m.role}${m.err ? ' err' : ''}`;
      if (m.role === 'user') div.textContent = m.text;
      else div.innerHTML = `<div class="md">${md(m.text)}</div>` + (m.proposals || []).map((p, k) => proposalCard(p, idx, k)).join('');
      log.append(div);
    });
    log.scrollTop = log.scrollHeight;
  };
  const send = async text => {
    text = (text || '').trim();
    if (!text) return;
    S.chat.push({ role: 'user', text });
    render();
    $('#msg').value = '';
    const typing = document.createElement('div');
    typing.className = 'msg bot';
    typing.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
    log.append(typing); log.scrollTop = log.scrollHeight;
    $('#send').disabled = true;
    try {
      const r = await post('/api/ai/chat', { session_id: S.chatSession, message: text });
      S.chatSession = r.session_id;
      S.chat.push({ role: 'bot', text: r.reply, proposals: r.proposals });
    } catch (e) { S.chat.push({ role: 'bot', text: e.message, err: true }); }
    if ($('#send')) $('#send').disabled = false;
    if ($('#log') === log) render();
  };
  $('#send').onclick = () => send($('#msg').value);
  $('#msg').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send($('#msg').value); } };
  $('#chips').onclick = e => { const c = e.target.closest('.chip'); if (c) send(c.textContent); };
  $('#reset').onclick = async () => { if (S.chatSession) await post('/api/ai/reset', { session_id: S.chatSession }).catch(() => {}); S.chat = []; S.chatSession = null; render(); };
  log.onclick = e => {
    const b = e.target.closest('[data-post]');
    if (!b) return;
    const [idx, k] = b.dataset.post.split(',').map(Number);
    const p = S.chat[idx].proposals[k];
    run(b, async () => {
      const j = await post('/api/journals', { date: p.date, description: p.description, lines: p.lines.map(l => ({ account_id: l.account_id, debit: l.debit, credit: l.credit, memo: l.memo })) });
      p.posted = j.number;
      toast(`رُحِّل القيد رقم ${j.number}`);
      render();
    });
  };
  render();
  $('#msg').focus();
};

function proposalCard(p, idx, k) {
  return `<div class="proposal"><div class="small"><b><i class="i i-journal"></i> قيد مقترح:</b> ${esc(p.description)} <span class="muted">(${p.date})</span></div>
    <table class="small"><thead><tr><th>الحساب</th><th>مدين</th><th>دائن</th></tr></thead><tbody>
    ${p.lines.map(l => `<tr><td>${esc(l.code)} — ${esc(l.account_name)}</td><td>${l.debit ? money(l.debit) : ''}</td><td>${l.credit ? money(l.credit) : ''}</td></tr>`).join('')}</tbody></table>
    <div class="row" style="margin-top:8px">${p.posted ? `<span class="badge sale"><i class="i i-check"></i> رُحِّل برقم ${p.posted}</span>`
      : can('journal') ? `<button class="btn sm primary" data-post="${idx},${k}">ترحيل القيد</button><span class="muted small">راجع القيد قبل الترحيل</span>`
      : '<span class="muted small">ليست لديك صلاحية ترحيل القيود — أرسله لمحاسب</span>'}</div></div>`;
}

// ═════════════════════ الجرد الفعلي ═════════════════════
PAGES.stocktake = async app => {
  const whs = (S.settings.warehouses || []).filter(w => w.active);
  const history = await api('/api/stocktakes');
  app.innerHTML = `<div class="card no-print"><h3><i class="i i-clipcheck"></i> جرد فعلي جديد</h3>
      <div class="row"><label class="row small">المستودع <select id="st-wh">${whOptions(whs[0]?.id)}</select></label>
        <label class="row small">التاريخ <input type="date" id="st-date" value="${today()}"></label>
        <input id="st-search" placeholder="بحث عن مادة…" style="width:200px">
        <label class="row small"><input type="checkbox" id="st-only"> إظهار المواد المعدودة فقط</label>
        <span class="sp"></span><button class="btn" id="st-print"><i class="i i-print"></i> طباعة ورقة الجرد</button></div>
      <p class="small muted">اترك المادة فارغة إن لم تعدّها — تُعدّل الكميات المُدخلة فقط، ويُرحَّل فرق القيمة إلى حساب «فروقات الجرد وتلف المخزون».</p></div>
    <div class="card"><div class="table-wrap" style="max-height:58vh;overflow-y:auto"><table><thead><tr>
      <th>الرمز</th><th>المادة</th><th>كمية النظام</th><th style="width:130px">الكمية المعدودة</th><th>الفرق</th><th>قيمة الفرق</th></tr></thead>
      <tbody id="st-rows"></tbody></table></div>
      <div class="row" style="margin-top:12px"><label class="f" style="flex:1;max-width:320px">ملاحظات<input id="st-notes"></label>
        <span class="sp"></span><span id="st-sum" class="small"></span>
        <button class="btn primary" id="st-save"><i class="i i-save"></i> ترحيل الجرد</button></div></div>
    <div class="card"><h3><i class="i i-list"></i> عمليات الجرد السابقة</h3>
      ${history.length ? `<table><thead><tr><th>#</th><th>التاريخ</th><th>المستودع</th><th>مواد معدّلة</th><th>قيمة الفرق</th><th>المستخدم</th></tr></thead><tbody>
        ${history.map(h => `<tr class="clickable" data-st="${h.id}"><td>${h.number}</td><td>${h.date}</td><td>${esc(h.warehouse_name)}</td>
          <td>${h.changed} من ${h.lines_count}</td><td class="${h.diff_value < 0 ? 'neg' : 'pos'}">${money(h.diff_value)}</td>
          <td class="small muted">${esc(h.user_name || '')}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">لم يُنفَّذ جرد بعد</div>'}</div>`;

  let sheet = [];
  const counted = {};
  const load = async () => {
    sheet = await api(`/api/stocktakes/sheet?warehouse_id=${$('#st-wh').value}`);
    draw();
  };
  const draw = () => {
    const q = ($('#st-search').value || '').trim();
    const onlyCounted = $('#st-only').checked;
    const list = sheet.filter(r => (!q || r.name.includes(q) || r.code.includes(q)) && (!onlyCounted || counted[r.item_id] !== undefined));
    $('#st-rows').innerHTML = list.map(r => {
      const cq = counted[r.item_id];
      const diff = cq === undefined ? null : +(cq - r.system_qty).toFixed(3);
      return `<tr><td class="muted">${esc(r.code)}</td><td>${esc(r.name)} <span class="muted small">${esc(r.unit)}</span></td>
        <td class="num">${fmt(r.system_qty)}</td>
        <td><input type="number" step="any" min="0" data-i="${r.item_id}" value="${cq === undefined ? '' : cq}"></td>
        <td class="num ${diff < 0 ? 'neg' : diff > 0 ? 'pos' : ''}">${diff === null ? '' : fmt(diff)}</td>
        <td class="num">${diff === null ? '' : fmt(diff * r.unit_cost)}</td></tr>`;
    }).join('') || '<tr><td colspan="6" class="empty">لا مواد مطابقة</td></tr>';
    sum();
  };
  const sum = () => {
    let n = 0, v = 0;
    sheet.forEach(r => {
      const cq = counted[r.item_id];
      if (cq === undefined) return;
      const d = cq - r.system_qty;
      if (Math.abs(d) > 1e-9) { n++; v += d * r.unit_cost; }
    });
    $('#st-sum').innerHTML = `المعدود: <b>${Object.keys(counted).length}</b> مادة — فروقات: <b>${n}</b> بقيمة <b class="${v < 0 ? 'neg' : 'pos'}">${fmt(v)}</b>`;
  };
  $('#st-rows').oninput = e => {
    const inp = e.target.closest('[data-i]');
    if (!inp) return;
    if (inp.value === '') delete counted[+inp.dataset.i];
    else counted[+inp.dataset.i] = +inp.value || 0;
    const r = sheet.find(x => x.item_id == inp.dataset.i);
    const tr = inp.closest('tr');
    const cq = counted[r.item_id];
    const diff = cq === undefined ? null : +(cq - r.system_qty).toFixed(3);
    tr.children[4].textContent = diff === null ? '' : fmt(diff);
    tr.children[4].className = 'num ' + (diff < 0 ? 'neg' : diff > 0 ? 'pos' : '');
    tr.children[5].textContent = diff === null ? '' : fmt(diff * r.unit_cost);
    sum();
  };
  $('#st-wh').onchange = () => run(null, load);
  $('#st-search').oninput = draw;
  $('#st-only').onchange = draw;
  $('#st-print').onclick = () => printHTML('ورقة الجرد',
    `<table><thead><tr><th>الرمز</th><th>المادة</th><th>الوحدة</th><th>الكمية المعدودة</th></tr></thead><tbody>
      ${sheet.map(r => `<tr><td>${esc(r.code)}</td><td>${esc(r.name)}</td><td>${esc(r.unit)}</td><td style="width:120px">&nbsp;</td></tr>`).join('')}
    </tbody></table>`);
  $('#st-save').onclick = async e => {
    const lines = Object.entries(counted).map(([item_id, counted_qty]) => ({ item_id: +item_id, counted_qty }));
    if (!lines.length) return toast('أدخل الكميات المعدودة أولاً', true);
    if (!await confirmBox(`سيتم تعديل كميات ${lines.length} مادة وترحيل فرق القيمة محاسبياً. متابعة؟`)) return;
    run(e.target, async () => {
      const st = await post('/api/stocktakes', { warehouse_id: +$('#st-wh').value, date: $('#st-date').value,
        notes: $('#st-notes').value, lines });
      toast(`رُحِّل الجرد رقم ${st.number} — فرق ${fmt(st.diff_value)}`);
      route();
    });
  };
  $$('[data-st]', app).forEach(tr => tr.onclick = async () => {
    const st = await api(`/api/stocktakes/${tr.dataset.st}`);
    modal(`جرد رقم ${st.number} — ${esc(st.warehouse_name)}`, `
      <p class="small muted">${st.date}${st.user_name ? ` — بواسطة ${esc(st.user_name)}` : ''}${st.notes ? ` — ${esc(st.notes)}` : ''}</p>
      <div class="table-wrap" style="max-height:50vh;overflow-y:auto"><table><thead><tr><th>المادة</th><th>النظام</th><th>المعدود</th><th>الفرق</th><th>القيمة</th></tr></thead><tbody>
        ${st.lines.filter(l => Math.abs(l.diff) > 1e-9).map(l => `<tr><td>${esc(l.item_name)}</td><td class="num">${fmt(l.system_qty)}</td>
          <td class="num">${fmt(l.counted_qty)}</td><td class="num ${l.diff < 0 ? 'neg' : 'pos'}">${fmt(l.diff)}</td><td class="num">${fmt(l.diff_value)}</td></tr>`).join('')
        || '<tr><td colspan="5" class="empty">لا فروقات — الجرد مطابق</td></tr>'}
      </tbody><tfoot><tr><td colspan="4">إجمالي فرق القيمة</td><td class="num">${fmt(st.diff_value)}</td></tr></tfoot></table></div>
      <div class="foot">${st.journal_id ? `<button class="btn" data-j>عرض القيد</button>` : ''}<button class="btn" data-close>إغلاق</button></div>`, { wide: true });
    const jb = $('[data-j]');
    if (jb) jb.onclick = () => showJournal(st.journal_id);
  });
  await load();
};

// ═════════════════════ أوامر الشراء ═════════════════════
PAGES.orders = async (app, tab = 'list') => {
  const [orders, items, suppliers] = await Promise.all([api('/api/purchase-orders'), api('/api/items'), api('/api/parties?kind=supplier')]);
  app.innerHTML = `<div class="tabs"><button data-t="list" class="${tab === 'list' ? 'on' : ''}">الأوامر</button>
      <button data-t="suggest" class="${tab === 'suggest' ? 'on' : ''}">اقتراح إعادة الطلب</button></div><div id="po-body"></div>`;
  $('.tabs', app).onclick = e => { const b = e.target.closest('button'); if (b) go(`#/orders/${b.dataset.t}`); };

  const poForm = (po, prefill) => {
    const rows = po ? po.lines.map(l => ({ ...l })) : (prefill || []);
    const m = modal(po ? `تعديل أمر شراء رقم ${po.number}` : 'أمر شراء جديد', `
      <div class="form-grid"><label class="f">المورد<select id="po-sup"><option value="">بدون</option>
          ${suppliers.map(sp => `<option value="${sp.id}" ${po && po.supplier_id === sp.id ? 'selected' : ''}>${esc(sp.name)}</option>`).join('')}</select></label>
        <label class="f">المستودع<select id="po-wh">${whOptions(po?.warehouse_id)}</select></label>
        <label class="f">التاريخ<input type="date" id="po-date" value="${po?.date || today()}"></label>
        <label class="f">التسليم المتوقع<input type="date" id="po-exp" value="${po?.expected || ''}"></label></div>
      <datalist id="po-dl">${items.map(i => `<option value="${esc(i.code)} - ${esc(i.name)}">`).join('')}</datalist>
      <table class="lines-table small" style="margin-top:10px"><thead><tr><th>المادة</th><th style="width:100px">الكمية</th><th style="width:110px">السعر</th><th style="width:100px">الإجمالي</th><th style="width:36px"></th></tr></thead>
        <tbody id="po-rows"></tbody><tfoot><tr><td colspan="3">الإجمالي</td><td id="po-total" class="num"></td><td></td></tr></tfoot></table>
      <button class="btn sm" id="po-add" style="margin-top:6px"><i class="i i-plus"></i> مادة</button>
      <label class="f" style="margin-top:10px">ملاحظات<input id="po-notes" value="${esc(po?.notes)}"></label>
      <div class="foot"><button class="btn primary" id="po-save">حفظ كمسودة</button>
        <button class="btn" id="po-send">حفظ وإرسال للمورد</button><button class="btn" data-close>إلغاء</button></div>`, { wide: true });
    const find = t => items.find(i => `${i.code} - ${i.name}` === t.trim() || i.code === t.trim());
    const total = () => $('#po-total').textContent = fmt(rows.reduce((a, r) => a + (r.qty || 0) * (r.price || 0), 0));
    const draw = () => {
      if (!rows.length || rows[rows.length - 1].item_id) rows.push({ item_id: null, qty: 1, price: 0 });
      $('#po-rows').innerHTML = rows.map((r, i) => {
        const it = items.find(x => x.id === r.item_id);
        return `<tr><td><input list="po-dl" data-i="${i}" data-f="item" value="${it ? esc(`${it.code} - ${it.name}`) : ''}"></td>
          <td><input type="number" step="any" min="0" data-i="${i}" data-f="qty" value="${r.qty}"></td>
          <td><input type="number" step="any" min="0" data-i="${i}" data-f="price" value="${r.price}"></td>
          <td class="num">${fmt((r.qty || 0) * (r.price || 0))}</td>
          <td>${r.item_id ? `<button class="btn sm ghost" data-rm="${i}"><i class="i i-x"></i></button>` : ''}</td></tr>`;
      }).join('');
      total();
    };
    $('#po-add').onclick = draw;
    $('#po-rows').onchange = e => {
      const inp = e.target.closest('[data-f]'); if (!inp) return;
      const r = rows[+inp.dataset.i];
      if (inp.dataset.f === 'item') {
        const it = find(inp.value);
        r.item_id = it?.id || null;
        if (it && !r.price) r.price = +it.avg_cost.toFixed(2);
        draw();
      } else { r[inp.dataset.f] = +inp.value || 0; draw(); }
    };
    $('#po-rows').onclick = e => { const b = e.target.closest('[data-rm]'); if (b) { rows.splice(+b.dataset.rm, 1); draw(); } };
    draw();
    const save = status => run($('#po-save'), async () => {
      const body = { supplier_id: +$('#po-sup').value || null, warehouse_id: +$('#po-wh').value, date: $('#po-date').value,
        expected: $('#po-exp').value || null, notes: $('#po-notes').value, status,
        lines: rows.filter(r => r.item_id && r.qty > 0) };
      const saved = po ? await put(`/api/purchase-orders/${po.id}`, body) : await post('/api/purchase-orders', body);
      m.close(); toast(`أمر شراء رقم ${saved.number}`); route();
    });
    $('#po-save').onclick = () => save('draft');
    $('#po-send').onclick = () => save('sent');
  };

  if (tab === 'suggest') {
    const sug = await api('/api/purchase-orders/suggestions');
    $('#po-body').innerHTML = `<div class="card"><h3><i class="i i-chart"></i> مواد تحتاج إعادة طلب<span class="sp"></span>
        <button class="btn primary sm" id="sg-po" ${sug.length ? '' : 'disabled'}>إنشاء أمر شراء بالمحدد</button></h3>
      ${sug.length ? `<div class="table-wrap"><table><thead><tr><th style="width:36px"><input type="checkbox" id="sg-all" checked></th>
        <th>المادة</th><th>المتوفر</th><th>حد الطلب</th><th>مبيعات 30 يوماً</th><th>الكمية المقترحة</th><th>آخر سعر شراء</th><th>آخر مورد</th></tr></thead><tbody>
        ${sug.map(x => `<tr><td><input type="checkbox" data-s="${x.item_id}" checked></td><td>${esc(x.name)}</td>
          <td class="num ${x.qty <= 0 ? 'neg' : ''}">${fmt(x.qty)}</td><td class="num">${fmt(x.min_qty)}</td><td class="num">${fmt(x.sold)}</td>
          <td><input type="number" step="any" min="0" data-q="${x.item_id}" value="${x.suggested}" style="width:90px"></td>
          <td class="num">${fmt(x.last_price)}</td><td class="small muted">${esc(x.supplier || '')}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">لا توجد مواد تحت حد الطلب — حدّد «حد الطلب» في بطاقة المادة ليعمل الاقتراح.</div>'}
      <p class="small muted">الكمية المقترحة = ما يكفي أسبوعين حسب سرعة البيع، أو ضعف حد الطلب — أيهما أكبر.</p></div>`;
    if ($('#sg-all')) $('#sg-all').onchange = e => $$('[data-s]').forEach(c => c.checked = e.target.checked);
    if ($('#sg-po')) $('#sg-po').onclick = () => {
      const chosen = $$('[data-s]:checked').map(c => +c.dataset.s);
      const prefill = sug.filter(x => chosen.includes(x.item_id))
        .map(x => ({ item_id: x.item_id, qty: +($(`[data-q="${x.item_id}"]`).value) || x.suggested, price: x.last_price }));
      if (!prefill.length) return toast('اختر مادة واحدة على الأقل', true);
      poForm(null, prefill);
    };
    return;
  }

  $('#po-body').innerHTML = `<div class="card"><h3><i class="i i-clipboard"></i> أوامر الشراء<span class="sp"></span><button class="btn primary sm" id="po-new"><i class="i i-plus"></i> أمر شراء</button></h3>
    ${orders.length ? `<div class="table-wrap"><table><thead><tr><th>#</th><th>التاريخ</th><th>المورد</th><th>المستودع</th><th>المواد</th><th>الإجمالي</th><th>الحالة</th><th></th></tr></thead><tbody>
      ${orders.map(o => `<tr class="clickable" data-po="${o.id}"><td>${o.number}</td><td>${o.date}</td><td>${esc(o.supplier_name || '—')}</td>
        <td class="small">${esc(o.warehouse_name || '')}</td><td>${o.lines_count}</td><td>${money(o.total)}</td>
        <td><span class="badge ${o.status === 'received' ? 'sale' : o.status === 'canceled' ? 'low' : 'purchase'}">${o.status_label}</span></td>
        <td class="act">${o.status !== 'received' ? `<button class="btn sm primary" data-rcv="${o.id}">استلام</button>` : ''}</td></tr>`).join('')}
      </tbody></table></div>` : '<div class="empty">لا توجد أوامر شراء</div>'}</div>`;
  $('#po-new').onclick = () => poForm(null);
  $('#po-body').onclick = async e => {
    const rcv = e.target.closest('[data-rcv]');
    if (rcv) {
      e.stopPropagation();
      const po = await api(`/api/purchase-orders/${rcv.dataset.rcv}`);
      const accs = await api('/api/accounts');
      const m = modal(`استلام أمر شراء رقم ${po.number}`, `
        <p class="small muted">تُنشأ فاتورة شراء بالمواد التالية ويمكنك تعديل الكميات والأسعار قبل التأكيد.</p>
        <table class="lines-table small"><thead><tr><th>المادة</th><th style="width:100px">الكمية</th><th style="width:110px">السعر</th></tr></thead><tbody>
          ${po.lines.map((l, i) => `<tr><td>${esc(l.item_name)}</td>
            <td><input type="number" step="any" min="0" data-i="${i}" data-f="qty" value="${l.qty}"></td>
            <td><input type="number" step="any" min="0" data-i="${i}" data-f="price" value="${l.price}"></td></tr>`).join('')}
        </tbody></table>
        <div class="form-grid" style="margin-top:10px">
          <label class="f">المورد<select id="rc-sup"><option value="">بدون (نقدي)</option>
            ${suppliers.map(sp => `<option value="${sp.id}" ${po.supplier_id === sp.id ? 'selected' : ''}>${esc(sp.name)}</option>`).join('')}</select></label>
          <label class="f">الدفع<select id="rc-pay"><option value="credit">آجل</option><option value="cash">نقداً</option></select></label>
          <label class="f" id="rc-cash-wrap" style="display:none">الصندوق<select id="rc-cash">${accountOptions(cashAccounts(accs))}</select></label>
          <label class="f">التاريخ<input type="date" id="rc-date" value="${today()}"></label></div>
        <div class="foot"><button class="btn primary" id="rc-go">استلام وإنشاء الفاتورة</button><button class="btn" data-close>إلغاء</button></div>`, { wide: true });
      const lines = po.lines.map(l => ({ item_id: l.item_id, qty: l.qty, price: l.price, unit_name: l.unit_name, unit_factor: l.unit_factor }));
      $('.mb', m.el).addEventListener('input', ev => {
        const inp = ev.target.closest('[data-f]');
        if (inp) lines[+inp.dataset.i][inp.dataset.f] = +inp.value || 0;
      });
      const syncPay = () => $('#rc-cash-wrap').style.display = $('#rc-pay').value === 'cash' ? '' : 'none';
      $('#rc-pay').onchange = syncPay;
      $('#rc-sup').onchange = () => { if (!$('#rc-sup').value) { $('#rc-pay').value = 'cash'; syncPay(); } };
      if (!po.supplier_id) { $('#rc-pay').value = 'cash'; }
      syncPay();
      $('#rc-go').onclick = ev => run(ev.target, async () => {
        const inv = await post(`/api/purchase-orders/${po.id}/receive`, { lines, payment: $('#rc-pay').value,
          supplier_id: +$('#rc-sup').value || null,
          cash_account_id: $('#rc-pay').value === 'cash' ? +$('#rc-cash').value : null, date: $('#rc-date').value });
        m.close(); toast(`أُنشئت فاتورة شراء رقم ${inv.number}`); showInvoice(inv.id);
      });
      return;
    }
    const tr = e.target.closest('[data-po]');
    if (!tr) return;
    const po = await api(`/api/purchase-orders/${tr.dataset.po}`);
    const m = modal(`أمر شراء رقم ${po.number}`, `
      <p class="small muted">${po.date} — ${esc(po.supplier_name || 'بدون مورد')} — ${esc(po.warehouse_name || '')} — <b>${po.status_label}</b>
        ${po.expected ? ` — التسليم المتوقع ${po.expected}` : ''}</p>
      <table><thead><tr><th>المادة</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th></tr></thead><tbody>
        ${po.lines.map(l => `<tr><td>${esc(l.item_name)}</td><td class="num">${fmt(l.qty)} ${esc(l.unit_name || l.unit)}</td>
          <td class="num">${fmt(l.price)}</td><td class="num">${fmt(l.qty * l.price)}</td></tr>`).join('')}
      </tbody><tfoot><tr><td colspan="3">الإجمالي</td><td class="num">${fmt(po.total)}</td></tr></tfoot></table>
      <div class="foot">
        <button class="btn" data-print><i class="i i-print"></i> طباعة</button>
        ${po.status !== 'received' ? '<button class="btn" data-edit><i class="i i-edit"></i> تعديل</button>' : ''}
        ${po.status === 'draft' ? '<button class="btn" data-send><i class="i i-send"></i> إرسال للمورد</button>' : ''}
        ${po.status !== 'received' && po.status !== 'canceled' ? '<button class="btn" data-cancel>إلغاء الأمر</button>' : ''}
        ${po.status !== 'received' && can('delete') ? '<button class="btn danger" data-del><i class="i i-trash"></i> حذف</button>' : ''}
        <button class="btn" data-close>إغلاق</button></div>`, { wide: true });
    $('[data-print]', m.el).onclick = () => printHTML(`أمر شراء رقم ${po.number}`,
      `<p>المورد: ${esc(po.supplier_name || '—')} — التاريخ: ${po.date}${po.expected ? ` — التسليم: ${po.expected}` : ''}</p>` + $('table', m.el).outerHTML);
    if ($('[data-edit]', m.el)) $('[data-edit]', m.el).onclick = () => { m.close(); poForm(po); };
    const setStatus = st => run(null, async () => { await post(`/api/purchase-orders/${po.id}/status`, { status: st }); m.close(); route(); });
    if ($('[data-send]', m.el)) $('[data-send]', m.el).onclick = () => setStatus('sent');
    if ($('[data-cancel]', m.el)) $('[data-cancel]', m.el).onclick = () => setStatus('canceled');
    if ($('[data-del]', m.el)) $('[data-del]', m.el).onclick = async () => {
      if (!await confirmBox('حذف أمر الشراء؟')) return;
      run(null, async () => { await del(`/api/purchase-orders/${po.id}`); m.close(); toast('حُذف الأمر'); route(); });
    };
  };
};

// ═════════════════════ الأسعار والعروض ═════════════════════
PAGES.pricing = async (app, tab = 'lists') => {
  const [lists, promos, items] = await Promise.all([api('/api/price-lists'), api('/api/promotions'), api('/api/items')]);
  app.innerHTML = `<div class="tabs"><button data-t="lists" class="${tab === 'lists' ? 'on' : ''}">قوائم الأسعار</button>
      <button data-t="promos" class="${tab === 'promos' ? 'on' : ''}">العروض والحسومات</button></div><div id="pr-body"></div>`;
  $('.tabs', app).onclick = e => { const b = e.target.closest('button'); if (b) go(`#/pricing/${b.dataset.t}`); };

  if (tab === 'lists') {
    $('#pr-body').innerHTML = `<div class="card"><h3><i class="i i-tag"></i> قوائم الأسعار<span class="sp"></span><button class="btn primary sm" id="pl-new"><i class="i i-plus"></i> قائمة</button></h3>
      ${lists.length ? `<table><thead><tr><th>القائمة</th><th>عدد المواد</th><th>عدد العملاء</th><th></th></tr></thead><tbody>
        ${lists.map(l => `<tr><td>${esc(l.name)} ${l.active ? '' : '<span class="badge">موقوفة</span>'}</td><td>${l.items.length}</td><td>${l.parties}</td>
          <td class="act"><button class="btn sm" data-ed="${l.id}"><i class="i i-edit"></i> تعديل</button>${can('delete') ? `<button class="btn sm danger" data-del="${l.id}"><i class="i i-trash"></i></button>` : ''}</td></tr>`).join('')}
        </tbody></table>` : '<div class="empty">لا توجد قوائم أسعار. أنشئ قائمة «جملة» مثلاً وأسندها لعملاء الجملة.</div>'}
      <p class="small muted">سعر القائمة يُطبَّق تلقائياً في الفواتير ونقطة البيع عند اختيار العميل المرتبط بها.</p></div>`;
    const form = l => {
      const rows = l ? l.items.map(i => ({ ...i })) : [];
      const m = modal(l ? `تعديل «${esc(l.name)}»` : 'قائمة أسعار جديدة', `
        <div class="form-grid"><label class="f">اسم القائمة<input id="pl-name" value="${esc(l?.name)}"></label></div>
        <datalist id="pl-dl">${items.map(i => `<option value="${esc(i.code)} - ${esc(i.name)}">`).join('')}</datalist>
        <table class="lines-table small" style="margin-top:10px"><thead><tr><th>المادة</th><th style="width:120px">السعر العادي</th><th style="width:130px">سعر القائمة</th><th style="width:36px"></th></tr></thead>
        <tbody id="pl-rows"></tbody></table><button class="btn sm" id="pl-add" style="margin-top:6px"><i class="i i-plus"></i> مادة</button>
        <div class="foot"><button class="btn primary" id="pl-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`, { wide: true });
      const find = t => items.find(i => `${i.code} - ${i.name}` === t.trim() || i.code === t.trim());
      const draw = () => {
        $('#pl-rows').innerHTML = rows.map((r, i) => {
          const it = items.find(x => x.id === r.item_id);
          return `<tr><td><input list="pl-dl" data-i="${i}" data-f="item" value="${it ? esc(`${it.code} - ${it.name}`) : ''}"></td>
            <td class="muted small">${it ? fmt(it.sale_price) : ''}</td>
            <td><input type="number" step="any" min="0" data-i="${i}" data-f="price" value="${r.price || ''}"></td>
            <td><button class="btn sm ghost" data-rm="${i}"><i class="i i-x"></i></button></td></tr>`;
        }).join('');
      };
      $('#pl-add').onclick = () => { rows.push({ item_id: null, price: '' }); draw(); };
      $('#pl-rows').onchange = e => {
        const inp = e.target.closest('[data-f]'); if (!inp) return;
        const r = rows[+inp.dataset.i];
        if (inp.dataset.f === 'item') { r.item_id = find(inp.value)?.id || null; draw(); } else r.price = +inp.value || 0;
      };
      $('#pl-rows').onclick = e => { const b = e.target.closest('[data-rm]'); if (b) { rows.splice(+b.dataset.rm, 1); draw(); } };
      draw();
      $('#pl-save').onclick = e => run(e.target, async () => {
        const body = { name: $('#pl-name').value, items: rows.filter(r => r.item_id && r.price > 0) };
        l ? await put(`/api/price-lists/${l.id}`, body) : await post('/api/price-lists', body);
        m.close(); toast('حُفظت القائمة'); route();
      });
    };
    $('#pl-new').onclick = () => form(null);
    $('#pr-body').onclick = async e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.ed) form(lists.find(l => l.id == b.dataset.ed));
      if (b.dataset.del && await confirmBox('حذف قائمة الأسعار؟')) run(b, async () => { await del(`/api/price-lists/${b.dataset.del}`); route(); });
    };
    return;
  }

  $('#pr-body').innerHTML = `<div class="card"><h3><i class="i i-percent"></i> العروض<span class="sp"></span><button class="btn primary sm" id="pm-new"><i class="i i-plus"></i> عرض</button></h3>
    ${promos.length ? `<table><thead><tr><th>العرض</th><th>المادة</th><th>النوع</th><th>القيمة</th><th>أقل كمية</th><th>الفترة</th><th>الحالة</th><th></th></tr></thead><tbody>
      ${promos.map(p => `<tr><td>${esc(p.name)}</td><td>${p.item_name ? esc(p.item_name) : '<span class="muted">كل المواد</span>'}</td>
        <td>${p.kind === 'percent' ? 'حسم نسبة' : 'سعر ثابت'}</td><td>${fmt(p.value)}${p.kind === 'percent' ? '%' : ''}</td>
        <td>${p.min_qty || '—'}</td><td class="small">${p.date_from || '—'} → ${p.date_to || '—'}</td>
        <td>${p.active ? '<span class="badge sale">فعّال</span>' : '<span class="badge">موقوف</span>'}</td>
        <td class="act"><button class="btn sm" data-ed="${p.id}"><i class="i i-edit"></i></button>${can('delete') ? `<button class="btn sm danger" data-del="${p.id}"><i class="i i-trash"></i></button>` : ''}</td></tr>`).join('')}
      </tbody></table>` : '<div class="empty">لا توجد عروض</div>'}
    <p class="small muted">يُطبَّق العرض تلقائياً في نقطة البيع والفواتير، ويُختار دائماً السعر الأفضل للزبون.</p></div>`;
  const pform = p => {
    const m = modal(p ? 'تعديل عرض' : 'عرض جديد', `<div class="form-grid">
        <label class="f">اسم العرض<input id="pm-name" value="${esc(p?.name)}"></label>
        <label class="f">المادة<select id="pm-item"><option value="">كل المواد</option>
          ${items.map(i => `<option value="${i.id}" ${p && p.item_id === i.id ? 'selected' : ''}>${esc(i.name)}</option>`).join('')}</select></label>
        <label class="f">النوع<select id="pm-kind"><option value="percent" ${p?.kind === 'percent' ? 'selected' : ''}>حسم بنسبة %</option>
          <option value="price" ${p?.kind === 'price' ? 'selected' : ''}>سعر ثابت للمادة</option></select></label>
        <label class="f">القيمة<input type="number" step="any" min="0" id="pm-val" value="${p?.value ?? ''}"></label>
        <label class="f">أقل كمية لتفعيله<input type="number" step="any" min="0" id="pm-min" value="${p?.min_qty ?? 0}"></label>
        <label class="f">من تاريخ<input type="date" id="pm-from" value="${p?.date_from || ''}"></label>
        <label class="f">إلى تاريخ<input type="date" id="pm-to" value="${p?.date_to || ''}"></label></div>
      <label class="row small" style="margin-top:10px"><input type="checkbox" id="pm-active" ${!p || p.active ? 'checked' : ''}> العرض فعّال</label>
      <div class="foot"><button class="btn primary" id="pm-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#pm-save').onclick = e => run(e.target, async () => {
      const body = { name: $('#pm-name').value, item_id: +$('#pm-item').value || null, kind: $('#pm-kind').value,
        value: +$('#pm-val').value || 0, min_qty: +$('#pm-min').value || 0, date_from: $('#pm-from').value || null,
        date_to: $('#pm-to').value || null, active: $('#pm-active').checked };
      p ? await put(`/api/promotions/${p.id}`, body) : await post('/api/promotions', body);
      m.close(); toast('حُفظ العرض'); route();
    });
  };
  $('#pm-new').onclick = () => pform(null);
  $('#pr-body').onclick = async e => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.ed) pform(promos.find(p => p.id == b.dataset.ed));
    if (b.dataset.del && await confirmBox('حذف هذا العرض؟')) run(b, async () => { await del(`/api/promotions/${b.dataset.del}`); route(); });
  };
};

// ═════════════════════ ملصقات الباركود ═════════════════════
PAGES.labels = async app => {
  const items = await api('/api/items');
  app.innerHTML = `<div class="card no-print"><h3><i class="i i-tag"></i> طباعة ملصقات الباركود</h3>
      <div class="row"><input id="lb-search" placeholder="بحث…" style="width:200px">
        <label class="row small">عدد الملصقات لكل مادة <input type="number" id="lb-count" value="1" min="1" max="50" style="width:70px"></label>
        <label class="row small"><input type="checkbox" id="lb-price" checked> إظهار السعر</label>
        <span class="sp"></span><button class="btn primary" id="lb-print"><i class="i i-print"></i> طباعة المحدد</button></div>
      <div class="table-wrap" style="max-height:45vh;overflow-y:auto;margin-top:10px"><table><thead><tr><th style="width:36px"></th><th>الرمز</th><th>المادة</th><th>الباركود</th><th>السعر</th></tr></thead><tbody>
      ${items.map(i => `<tr data-name="${esc(i.code + ' ' + i.name)}"><td><input type="checkbox" data-i="${i.id}"></td><td>${esc(i.code)}</td><td>${esc(i.name)}</td>
        <td class="num">${esc(i.barcode || i.code)}</td><td>${money(i.sale_price)}</td></tr>`).join('')}
      </tbody></table></div>
      <p class="small muted">تُطبع الملصقات بمقاس 50×30 ملم. إن لم يكن للمادة باركود يُستخدم رمزها.</p></div>
    <div id="lb-preview"></div>`;
  filterRows($('#lb-search'), app);
  $('#lb-print').onclick = async () => {
    const ids = $$('#app input[data-i]:checked').map(c => +c.dataset.i);
    if (!ids.length) return toast('اختر مادة واحدة على الأقل', true);
    const count = Math.min(50, Math.max(1, +$('#lb-count').value || 1));
    const withPrice = $('#lb-price').checked;
    const lib = await loadBarcodeLib();
    const chosen = items.filter(i => ids.includes(i.id));
    const labels = [];
    chosen.forEach(i => { for (let k = 0; k < count; k++) labels.push(i); });
    const html = labels.map((i, idx) => `<div class="lbl"><div class="nm">${esc(i.name)}</div>
      ${lib ? `<svg id="bc${idx}"></svg>` : `<div class="cd">${esc(i.barcode || i.code)}</div>`}
      ${withPrice ? `<div class="pr">${fmt(i.sale_price)} ${esc(baseCur().symbol)}</div>` : ''}</div>`).join('');
    const f = document.createElement('iframe');
    f.style.cssText = 'position:fixed;width:0;height:0;border:0';
    document.body.append(f);
    const d = f.contentDocument;
    d.open();
    d.write(`<!doctype html><html dir="rtl"><head><meta charset="utf-8"><title>ملصقات</title><style>
      @page { size: 50mm 30mm; margin: 1mm; }
      body { font-family: Tahoma, sans-serif; margin: 0; }
      .lbl { width: 48mm; height: 28mm; page-break-after: always; display: flex; flex-direction: column;
             align-items: center; justify-content: center; text-align: center; overflow: hidden; }
      .nm { font-size: 9pt; font-weight: 700; } .pr { font-size: 11pt; font-weight: 800; }
      .cd { font-family: monospace; font-size: 10pt; letter-spacing: 1px; }
      svg { max-width: 46mm; height: 12mm; }
    </style></head><body>${html}</body></html>`);
    d.close();
    f.onload = () => {
      if (lib) labels.forEach((i, idx) => {
        try { f.contentWindow.JsBarcode ? f.contentWindow.JsBarcode(d.getElementById('bc' + idx), String(i.barcode || i.code), { displayValue: true, fontSize: 12, height: 40, margin: 0 })
          : window.JsBarcode(d.getElementById('bc' + idx), String(i.barcode || i.code), { displayValue: true, fontSize: 12, height: 40, margin: 0 }); }
        catch { d.getElementById('bc' + idx).outerHTML = `<div class="cd">${i.barcode || i.code}</div>`; }
      });
      setTimeout(() => { f.contentWindow.focus(); f.contentWindow.print(); setTimeout(() => f.remove(), 1500); }, 300);
    };
  };
};

/** مكتبة توليد الباركود تُحمَّل عند الحاجة فقط؛ إن تعذّر تحميلها يُطبع الرقم نصاً */
function loadBarcodeLib() {
  if (window.JsBarcode) return Promise.resolve(true);
  return new Promise(resolve => {
    const sc = document.createElement('script');
    sc.src = 'https://cdnjs.cloudflare.com/ajax/libs/jsbarcode/3.12.1/JsBarcode.all.min.js';
    sc.onload = () => resolve(true);
    sc.onerror = () => { toast('تعذّر تحميل مكتبة الباركود (تحتاج إنترنت) — سيُطبع الرقم نصاً', true); resolve(false); };
    document.head.append(sc);
  });
}


// ═════════════════════ الشيكات والأوراق التجارية ═════════════════════
const CHECK_STATUS = { pending: 'قيد التحصيل', settled: 'محصَّل/مدفوع', bounced: 'مرتجع', canceled: 'ملغى' };

PAGES.checks = async (app, tab = 'in') => {
  const [list, parties, accs] = await Promise.all([api(`/api/checks?kind=${tab}`), api('/api/parties'), api('/api/accounts')]);
  const isIn = tab === 'in';
  const pending = list.filter(c => c.status === 'pending');
  const sum = arr => arr.reduce((a, c) => a + c.base_amount, 0);
  app.innerHTML = `<div class="tabs"><button data-t="in" class="${isIn ? 'on' : ''}">شيكات واردة (من العملاء)</button>
      <button data-t="out" class="${!isIn ? 'on' : ''}">شيكات صادرة (للموردين)</button></div>
    <div class="grid g3" style="margin-bottom:16px">
      <div class="card kpi"><span class="t">${isIn ? 'قيد التحصيل' : 'قيد الدفع'}</span><span class="v">${money(sum(pending))}</span><span class="s">${pending.length} شيك</span></div>
      <div class="card kpi"><span class="t">مستحقة اليوم أو متأخرة</span><span class="v neg">${money(sum(pending.filter(c => c.days_left <= 0)))}</span>
        <span class="s">${pending.filter(c => c.days_left <= 0).length} شيك</span></div>
      <div class="card kpi"><span class="t">تستحق خلال ٧ أيام</span><span class="v">${money(sum(pending.filter(c => c.days_left > 0 && c.days_left <= 7)))}</span></div></div>
    <div class="card"><h3>${isIn ? 'الشيكات الواردة' : 'الشيكات الصادرة'}<span class="sp"></span>
        <button class="btn primary sm" id="ck-new"><i class="i i-plus"></i> ${isIn ? 'شيك وارد' : 'شيك صادر'}</button></h3>
      ${list.length ? `<div class="table-wrap"><table><thead><tr><th>#</th><th>رقم الشيك</th><th>${isIn ? 'العميل' : 'المورد'}</th><th>البنك</th>
        <th>الاستحقاق</th><th>المبلغ</th><th>الحالة</th><th></th></tr></thead><tbody>
        ${list.map(c => `<tr class="${c.overdue ? 'neg' : ''}"><td>${c.number}</td><td>${esc(c.check_number || '—')}</td>
          <td>${esc(c.party_name || '')}</td><td class="small">${esc(c.bank || '')}</td>
          <td class="num">${c.due_date}${c.status === 'pending' ? ` <span class="muted small">(${c.days_left < 0 ? 'متأخر ' + (-c.days_left) : c.days_left} يوم)</span>` : ''}</td>
          <td>${money(c.base_amount)}</td>
          <td><span class="badge ${c.status === 'settled' ? 'sale' : c.status === 'bounced' || c.status === 'canceled' ? 'low' : 'purchase'}">${CHECK_STATUS[c.status]}</span></td>
          <td class="act">${c.status === 'pending' ? `<button class="btn sm primary" data-settle="${c.id}">${isIn ? 'تحصيل' : 'صرف'}</button>` : ''}
            ${c.status !== 'canceled' && c.status !== 'bounced' ? `<button class="btn sm" data-bounce="${c.id}">ارتجاع</button>` : ''}
            ${c.status === 'pending' && can('delete') ? `<button class="btn sm danger" data-cancel="${c.id}">إلغاء</button>` : ''}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">لا توجد شيكات</div>'}
      <p class="small muted">الشيك الوارد يُقفل ذمة العميل وينتقل إلى حساب «أوراق القبض» حتى التحصيل. الصادر يُقفل ذمة المورد في «أوراق الدفع» حتى الصرف.</p></div>`;
  $('.tabs', app).onclick = e => { const b = e.target.closest('button'); if (b) go(`#/checks/${b.dataset.t}`); };

  $('#ck-new').onclick = () => {
    const side = parties.filter(p => p.kind === (isIn ? 'customer' : 'supplier'));
    const m = modal(isIn ? 'شيك وارد جديد' : 'شيك صادر جديد', `<div class="form-grid">
        <label class="f">${isIn ? 'العميل' : 'المورد'}<select id="ck-party">${side.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label>
        <label class="f">المبلغ<input type="number" step="any" min="0" id="ck-amt"></label>
        <label class="f">رقم الشيك<input id="ck-num"></label>
        <label class="f">البنك<input id="ck-bank"></label>
        <label class="f">تاريخ الاستحقاق<input type="date" id="ck-due" value="${today()}"></label>
        <label class="f">تاريخ التسجيل<input type="date" id="ck-date" value="${today()}"></label>
        ${(S.settings.currencies || []).length > 1 ? `<label class="f">العملة<select id="ck-cur">${curOptions()}</select></label>
          <label class="f">سعر الصرف<input type="number" step="any" id="ck-rate"></label>` : ''}</div>
      <label class="f" style="margin-top:10px">ملاحظات<input id="ck-notes"></label>
      <div class="foot"><button class="btn primary" id="ck-save">حفظ الشيك</button><button class="btn" data-close>إلغاء</button></div>`);
    const getCur = $('#ck-cur') ? bindCurrency($('#ck-cur'), $('#ck-rate'), () => {}) : () => ({});
    $('#ck-save').onclick = e => run(e.target, async () => {
      const ch = await post('/api/checks', { kind: tab, party_id: +$('#ck-party').value, amount: +$('#ck-amt').value,
        check_number: $('#ck-num').value, bank: $('#ck-bank').value, due_date: $('#ck-due').value,
        date: $('#ck-date').value, notes: $('#ck-notes').value, ...getCur() });
      m.close(); toast(`سُجّل الشيك رقم ${ch.number}`); route();
    });
  };

  app.querySelector('tbody')?.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.settle) {
      const m = modal(isIn ? 'تحصيل شيك' : 'صرف شيك', `<div class="form-grid">
          <label class="f">${isIn ? 'يُودع في' : 'يُدفع من'}<select id="st-cash">${accountOptions(cashAccounts(accs))}</select></label>
          <label class="f">التاريخ<input type="date" id="st-date" value="${today()}"></label></div>
        <div class="foot"><button class="btn primary" id="st-go">تأكيد</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#st-go').onclick = ev => run(ev.target, async () => {
        await post(`/api/checks/${b.dataset.settle}/settle`, { cash_account_id: +$('#st-cash').value, date: $('#st-date').value });
        m.close(); toast('تمت العملية'); route();
      });
    }
    if (b.dataset.bounce && await confirmBox('تسجيل الشيك كمرتجع؟ سيعود المبلغ على حساب الطرف.'))
      run(b, async () => { await post(`/api/checks/${b.dataset.bounce}/bounce`, {}); toast('سُجّل الارتجاع'); route(); });
    if (b.dataset.cancel && await confirmBox('إلغاء الشيك وحذف قيوده؟'))
      run(b, async () => { await del(`/api/checks/${b.dataset.cancel}`); toast('أُلغي الشيك'); route(); });
  });
};

// ═════════════════════ الأصول الثابتة ═════════════════════
PAGES.assets = async app => {
  const [list, accs] = await Promise.all([api('/api/assets'), api('/api/accounts')]);
  const tot = k => list.reduce((a, x) => a + x[k], 0);
  app.innerHTML = `<div class="grid g4" style="margin-bottom:16px">
      <div class="card kpi"><span class="t">تكلفة الأصول</span><span class="v">${money(tot('cost'))}</span><span class="s">${list.length} أصل</span></div>
      <div class="card kpi"><span class="t">مجمع الاهتلاك</span><span class="v">${money(tot('depreciated'))}</span></div>
      <div class="card kpi hl"><span class="t">القيمة الدفترية</span><span class="v">${money(tot('book_value'))}</span></div>
      <div class="card kpi"><span class="t">اهتلاك شهري</span><span class="v">${money(tot('monthly'))}</span></div></div>
    <div class="card"><h3><i class="i i-building"></i> الأصول الثابتة<span class="sp"></span>
        <button class="btn" id="as-depr"><i class="i i-clock"></i> ترحيل الاهتلاك</button><button class="btn primary sm" id="as-new"><i class="i i-plus"></i> أصل</button></h3>
      ${list.length ? `<div class="table-wrap"><table><thead><tr><th>الأصل</th><th>الحساب</th><th>تاريخ الشراء</th><th>التكلفة</th>
        <th>العمر (شهر)</th><th>اهتلاك شهري</th><th>المُهتلَك</th><th>القيمة الدفترية</th><th></th></tr></thead><tbody>
        ${list.map(a => `<tr><td>${esc(a.name)}</td><td class="small muted">${esc(a.account_name)}</td><td class="num">${a.purchase_date}</td>
          <td>${money(a.cost)}</td><td class="num">${a.months_done}/${a.life_months}</td><td>${money(a.monthly)}</td>
          <td>${money(a.depreciated)}</td><td><b>${money(a.book_value)}</b></td>
          <td class="act"><button class="btn sm" data-ed="${a.id}"><i class="i i-edit"></i></button>${can('delete') ? `<button class="btn sm danger" data-del="${a.id}"><i class="i i-trash"></i></button>` : ''}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">لا توجد أصول مسجلة</div>'}
      <p class="small muted">الاهتلاك بطريقة القسط الثابت: (التكلفة − القيمة التخريدية) ÷ العمر. الترحيل يحسب الأشهر المستحقة منذ آخر ترحيل ولا يكرّرها.</p></div>`;
  const form = a => {
    const m = modal(a ? 'تعديل أصل' : 'أصل ثابت جديد', `<div class="form-grid">
        <label class="f">اسم الأصل<input id="as-name" value="${esc(a?.name)}"></label>
        <label class="f">حساب الأصل<select id="as-acc">${accountOptions(accs, { selected: a?.account_id, filter: x => x.code.startsWith('12') && x.code !== '123' })}</select></label>
        <label class="f">تاريخ الشراء<input type="date" id="as-date" value="${a?.purchase_date || today()}"></label>
        <label class="f">التكلفة<input type="number" step="any" id="as-cost" value="${a?.cost ?? ''}"></label>
        <label class="f">القيمة التخريدية<input type="number" step="any" id="as-salv" value="${a?.salvage ?? 0}"></label>
        <label class="f">العمر الإنتاجي (شهر)<input type="number" id="as-life" value="${a?.life_months ?? 60}"></label></div>
      <label class="f" style="margin-top:10px">ملاحظات<input id="as-notes" value="${esc(a?.notes)}"></label>
      <p class="small muted">تسجيل الأصل هنا لا يُنشئ قيد شراء — سجّل شراءه بقيد أو فاتورة على حساب الأصل نفسه.</p>
      <div class="foot"><button class="btn primary" id="as-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#as-save').onclick = e => run(e.target, async () => {
      const body = { name: $('#as-name').value, account_id: +$('#as-acc').value, purchase_date: $('#as-date').value,
        cost: +$('#as-cost').value || 0, salvage: +$('#as-salv').value || 0, life_months: +$('#as-life').value || 0,
        notes: $('#as-notes').value };
      a ? await put(`/api/assets/${a.id}`, body) : await post('/api/assets', body);
      m.close(); toast('تم الحفظ'); route();
    });
  };
  $('#as-new').onclick = () => form(null);
  $('#as-depr').onclick = () => {
    const m = modal('ترحيل الاهتلاك', `<label class="f">حتى تاريخ<input type="date" id="dp-date" value="${today()}"></label>
      <p class="small muted">يُحسب لكل أصل عدد الأشهر غير المرحّلة ويُرحَّل قيد واحد: من حـ/ مصروف الاهتلاك إلى حـ/ مجمع الاهتلاك.</p>
      <div class="foot"><button class="btn primary" id="dp-go">ترحيل</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#dp-go').onclick = e => run(e.target, async () => {
      const r = await post('/api/assets/depreciate', { up_to: $('#dp-date').value });
      m.close();
      toast(`رُحِّل اهتلاك بمبلغ ${fmt(r.total)}`);
      modal('تفاصيل الاهتلاك المرحَّل', `<table><thead><tr><th>الأصل</th><th>أشهر</th><th>المبلغ</th></tr></thead><tbody>
        ${r.details.map(d => `<tr><td>${esc(d.asset)}</td><td class="num">${d.months}</td><td class="num">${fmt(d.amount)}</td></tr>`).join('')}
        </tbody><tfoot><tr><td colspan="2">الإجمالي</td><td class="num">${fmt(r.total)}</td></tr></tfoot></table>
        <div class="foot"><button class="btn" data-close>إغلاق</button></div>`);
      route();
    });
  };
  app.querySelector('tbody')?.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.ed) form(list.find(x => x.id == b.dataset.ed));
    if (b.dataset.del && await confirmBox('حذف هذا الأصل؟')) run(b, async () => { await del(`/api/assets/${b.dataset.del}`); route(); });
  });
};

// ═════════════════════ الموظفون والرواتب والعمولات ═════════════════════
PAGES.hr = async (app, tab = 'staff') => {
  const [emps, accs] = await Promise.all([api('/api/employees'), api('/api/accounts')]);
  const tabs = { staff: 'الموظفون', payroll: 'الرواتب', commissions: 'عمولات المندوبين' };
  app.innerHTML = `<div class="tabs">${Object.entries(tabs).map(([k, v]) => `<button data-t="${k}" class="${k === tab ? 'on' : ''}">${v}</button>`).join('')}</div>
    <div id="hr-body"></div>`;
  $('.tabs', app).onclick = e => { const b = e.target.closest('button'); if (b) go(`#/hr/${b.dataset.t}`); };
  const B = $('#hr-body');

  if (tab === 'staff') {
    B.innerHTML = `<div class="card"><h3><i class="i i-briefcase"></i> الموظفون<span class="sp"></span><button class="btn primary sm" id="em-new"><i class="i i-plus"></i> موظف</button></h3>
      ${emps.length ? `<table><thead><tr><th>الاسم</th><th>الوظيفة</th><th>الهاتف</th><th>الراتب</th><th>مندوب مبيعات</th><th>نسبة العمولة</th><th>الحالة</th><th></th></tr></thead><tbody>
        ${emps.map(e => `<tr><td>${esc(e.name)}</td><td class="small">${esc(e.job || '')}</td><td class="small muted">${esc(e.phone || '')}</td>
          <td>${money(e.salary)}</td><td>${e.is_salesperson ? '<i class="i i-check"></i>' : '—'}</td><td>${e.commission_rate ? fmt(e.commission_rate) + '%' : '—'}</td>
          <td>${e.active ? '<span class="badge sale">فعّال</span>' : '<span class="badge">موقوف</span>'}</td>
          <td class="act"><button class="btn sm" data-ed="${e.id}"><i class="i i-edit"></i></button>${can('delete') ? `<button class="btn sm danger" data-del="${e.id}"><i class="i i-trash"></i></button>` : ''}</td></tr>`).join('')}
        </tbody></table>` : '<div class="empty">لا يوجد موظفون</div>'}</div>`;
    const form = emp => {
      const m = modal(emp ? 'تعديل موظف' : 'موظف جديد', `<div class="form-grid">
          <label class="f">الاسم<input id="em-name" value="${esc(emp?.name)}"></label>
          <label class="f">الوظيفة<input id="em-job" value="${esc(emp?.job)}"></label>
          <label class="f">الهاتف<input id="em-phone" value="${esc(emp?.phone)}"></label>
          <label class="f">الراتب الشهري<input type="number" step="any" id="em-salary" value="${emp?.salary ?? 0}"></label>
          <label class="f">نسبة العمولة %<input type="number" step="any" id="em-rate" value="${emp?.commission_rate ?? 0}"></label></div>
        <label class="row small" style="margin-top:10px"><input type="checkbox" id="em-sales" ${emp?.is_salesperson ? 'checked' : ''}> مندوب مبيعات (يُحتسب له عمولة)</label>
        ${emp ? `<label class="row small"><input type="checkbox" id="em-active" ${emp.active ? 'checked' : ''}> فعّال</label>` : ''}
        <div class="foot"><button class="btn primary" id="em-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#em-save').onclick = e => run(e.target, async () => {
        const body = { name: $('#em-name').value, job: $('#em-job').value, phone: $('#em-phone').value,
          salary: +$('#em-salary').value || 0, commission_rate: +$('#em-rate').value || 0,
          is_salesperson: $('#em-sales').checked, active: $('#em-active') ? $('#em-active').checked : true };
        emp ? await put(`/api/employees/${emp.id}`, body) : await post('/api/employees', body);
        m.close(); toast('تم الحفظ'); route();
      });
    };
    $('#em-new').onclick = () => form(null);
    B.querySelector('tbody')?.addEventListener('click', async e => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.ed) form(emps.find(x => x.id == b.dataset.ed));
      if (b.dataset.del && await confirmBox('حذف الموظف؟')) run(b, async () => { await del(`/api/employees/${b.dataset.del}`); route(); });
    });
    return;
  }

  if (tab === 'payroll') {
    const runs = await api('/api/payrolls');
    B.innerHTML = `<div class="card"><h3><i class="i i-briefcase"></i> مسيّرات الرواتب<span class="sp"></span><button class="btn primary sm" id="pr-new"><i class="i i-plus"></i> مسيّر شهري</button></h3>
      ${runs.length ? `<table><thead><tr><th>#</th><th>الشهر</th><th>التاريخ</th><th>الموظفون</th><th>الإجمالي</th><th>الحالة</th></tr></thead><tbody>
        ${runs.map(r => `<tr class="clickable" data-run="${r.id}"><td>${r.number}</td><td>${r.month}</td><td>${r.date}</td><td>${r.count}</td>
          <td>${money(r.total)}</td><td><span class="badge ${r.paid ? 'sale' : 'purchase'}">${r.paid ? 'مدفوع' : 'مستحق'}</span></td></tr>`).join('')}
        </tbody></table>` : '<div class="empty">لا توجد مسيّرات رواتب</div>'}</div>`;
    $('#pr-new').onclick = () => {
      const active = emps.filter(e => e.active);
      if (!active.length) return toast('أضف موظفين أولاً', true);
      const lines = active.map(e => ({ employee_id: e.id, name: e.name, salary: e.salary, additions: 0, deductions: 0 }));
      const m = modal('مسيّر رواتب شهري', `<div class="form-grid">
          <label class="f">الشهر<input type="month" id="pr-month" value="${today().slice(0, 7)}"></label>
          <label class="f">تاريخ الصرف<input type="date" id="pr-date" value="${today()}"></label>
          <label class="f">الدفع<select id="pr-paid"><option value="1">مدفوع نقداً الآن</option><option value="0">مستحق (لم يُدفع)</option></select></label>
          <label class="f" id="pr-cash-wrap">الصندوق<select id="pr-cash">${accountOptions(cashAccounts(accs))}</select></label></div>
        <table class="lines-table small" style="margin-top:10px"><thead><tr><th>الموظف</th><th style="width:110px">الراتب</th><th style="width:110px">إضافات</th><th style="width:110px">حسومات</th><th style="width:110px">الصافي</th></tr></thead>
        <tbody id="pr-rows"></tbody><tfoot><tr><td colspan="4">الإجمالي</td><td id="pr-total" class="num"></td></tr></tfoot></table>
        <div class="foot"><button class="btn primary" id="pr-save"><i class="i i-save"></i> ترحيل الرواتب</button><button class="btn" data-close>إلغاء</button></div>`, { wide: true });
      const draw = () => {
        $('#pr-rows').innerHTML = lines.map((l, i) => `<tr><td>${esc(l.name)}</td>
          <td><input type="number" step="any" data-i="${i}" data-f="salary" value="${l.salary}"></td>
          <td><input type="number" step="any" data-i="${i}" data-f="additions" value="${l.additions}"></td>
          <td><input type="number" step="any" data-i="${i}" data-f="deductions" value="${l.deductions}"></td>
          <td class="num">${fmt(l.salary + l.additions - l.deductions)}</td></tr>`).join('');
        $('#pr-total').textContent = fmt(lines.reduce((a, l) => a + l.salary + l.additions - l.deductions, 0));
      };
      $('#pr-rows').oninput = e => {
        const inp = e.target.closest('[data-f]');
        if (inp) { lines[+inp.dataset.i][inp.dataset.f] = +inp.value || 0; draw(); }
      };
      $('#pr-paid').onchange = () => $('#pr-cash-wrap').style.display = $('#pr-paid').value === '1' ? '' : 'none';
      draw();
      $('#pr-save').onclick = e => run(e.target, async () => {
        const r = await post('/api/payrolls', { month: $('#pr-month').value, date: $('#pr-date').value,
          paid: $('#pr-paid').value === '1', cash_account_id: +$('#pr-cash').value,
          lines: lines.map(l => ({ employee_id: l.employee_id, salary: l.salary, additions: l.additions, deductions: l.deductions })) });
        m.close(); toast(`رُحِّلت رواتب ${r.month} بمبلغ ${fmt(r.total)}`); route();
      });
    };
    $$('[data-run]', B).forEach(tr => tr.onclick = async () => {
      const r = await api(`/api/payrolls/${tr.dataset.run}`);
      modal(`مسيّر رواتب ${r.month}`, `<p class="small muted">${r.date} — ${r.paid ? 'مدفوع نقداً' : 'مستحق على الشركة'}</p>
        <table><thead><tr><th>الموظف</th><th>الراتب</th><th>إضافات</th><th>حسومات</th><th>الصافي</th></tr></thead><tbody>
        ${r.lines.map(l => `<tr><td>${esc(l.employee_name)}</td><td class="num">${fmt(l.salary)}</td><td class="num">${fmt(l.additions)}</td>
          <td class="num">${fmt(l.deductions)}</td><td class="num"><b>${fmt(l.net)}</b></td></tr>`).join('')}
        </tbody><tfoot><tr><td colspan="4">الإجمالي</td><td class="num">${fmt(r.total)}</td></tr></tfoot></table>
        <div class="foot">${r.journal_id ? '<button class="btn" data-j>عرض القيد</button>' : ''}<button class="btn" data-close>إغلاق</button></div>`, { wide: true });
      if ($('[data-j]')) $('[data-j]').onclick = () => showJournal(r.journal_id);
    });
    return;
  }

  const from = monthStart(), to = today();
  B.innerHTML = `<div class="card no-print"><div class="row">
      <label class="row small">من <input type="date" id="cm-from" value="${from}"></label>
      <label class="row small">إلى <input type="date" id="cm-to" value="${to}"></label>
      <button class="btn primary" id="cm-go">عرض</button><span class="sp"></span><button class="btn" id="cm-print"><i class="i i-print"></i> طباعة</button></div></div>
    <div class="card" id="cm-out"></div>`;
  const load = async () => {
    const d = await api(`/api/reports/commissions?date_from=${$('#cm-from').value}&date_to=${$('#cm-to').value}`);
    $('#cm-out').innerHTML = d.rows.length ? `<table><thead><tr><th>المندوب</th><th>عدد الفواتير</th><th>صافي المبيعات</th><th>الربح</th><th>النسبة</th><th>العمولة</th><th></th></tr></thead><tbody>
        ${d.rows.map(r => `<tr><td>${esc(r.name)}</td><td class="num">${r.invoices}</td><td>${money(r.sales)}</td><td>${money(r.profit)}</td>
          <td class="num">${fmt(r.rate)}%</td><td><b>${money(r.commission)}</b></td>
          <td class="act"><button class="btn sm primary" data-pay="${r.id}" data-amt="${r.commission}">صرف العمولة</button></td></tr>`).join('')}
        </tbody><tfoot><tr><td colspan="2">الإجمالي</td><td>${money(d.total_sales)}</td><td></td><td></td><td>${money(d.total_commission)}</td><td></td></tr></tfoot></table>`
      : '<div class="empty">لا يوجد مندوبون — فعّل «مندوب مبيعات» في بطاقة الموظف واربط الفواتير به.</div>';
    $$('[data-pay]', $('#cm-out')).forEach(b => b.onclick = () => {
      const m = modal('صرف عمولة', `<div class="form-grid">
          <label class="f">المبلغ<input type="number" step="any" id="pc-amt" value="${b.dataset.amt}"></label>
          <label class="f">من الصندوق<select id="pc-cash">${accountOptions(cashAccounts(accs))}</select></label>
          <label class="f">التاريخ<input type="date" id="pc-date" value="${today()}"></label></div>
        <div class="foot"><button class="btn primary" id="pc-go">صرف</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#pc-go').onclick = e => run(e.target, async () => {
        await post('/api/commissions/pay', { employee_id: +b.dataset.pay, amount: +$('#pc-amt').value,
          cash_account_id: +$('#pc-cash').value, date: $('#pc-date').value });
        m.close(); toast('صُرفت العمولة'); route();
      });
    });
  };
  $('#cm-go').onclick = load;
  $('#cm-print').onclick = () => printHTML('عمولات المندوبين', $('#cm-out').innerHTML);
  await load();
};

// ═════════════════════ مراكز الكلفة ═════════════════════
PAGES.centers = async app => {
  const from = yearStart(), to = today();
  app.innerHTML = `<div class="card no-print"><div class="row">
      <label class="row small">من <input type="date" id="cc-from" value="${from}"></label>
      <label class="row small">إلى <input type="date" id="cc-to" value="${to}"></label>
      <button class="btn primary" id="cc-go">عرض</button><span class="sp"></span>
      ${can('accounts') ? '<button class="btn" id="cc-new"><i class="i i-plus"></i> مركز كلفة</button>' : ''}
      <button class="btn" id="cc-print"><i class="i i-print"></i> طباعة</button></div>
      <p class="small muted">اربط الفواتير والقيود بمركز كلفة (فرع، مشروع، قسم) لترى ربحية كل واحد على حدة.</p></div>
    <div class="card" id="cc-out"></div>`;
  const load = async () => {
    const list = await api(`/api/cost-centers?with_totals=true&date_from=${$('#cc-from').value}&date_to=${$('#cc-to').value}`);
    $('#cc-out').innerHTML = list.length ? `<table><thead><tr><th>مركز الكلفة</th><th>الإيرادات</th><th>المصروفات</th><th>الربح</th><th>الحالة</th><th></th></tr></thead><tbody>
      ${list.map(c => `<tr><td>${esc(c.name)}</td><td>${money(c.revenue)}</td><td>${money(c.expense)}</td>
        <td><b class="${c.profit < 0 ? 'neg' : 'pos'}">${money(c.profit)}</b></td>
        <td>${c.active ? '<span class="badge sale">فعّال</span>' : '<span class="badge">موقوف</span>'}</td>
        <td class="act">${can('accounts') ? `<button class="btn sm" data-ed="${c.id}"><i class="i i-edit"></i></button>` : ''}
          ${can('accounts') && can('delete') ? `<button class="btn sm danger" data-del="${c.id}"><i class="i i-trash"></i></button>` : ''}</td></tr>`).join('')}
      </tbody><tfoot><tr><td>الإجمالي</td><td>${money(list.reduce((a, c) => a + c.revenue, 0))}</td>
        <td>${money(list.reduce((a, c) => a + c.expense, 0))}</td><td>${money(list.reduce((a, c) => a + c.profit, 0))}</td><td colspan="2"></td></tr></tfoot></table>`
      : '<div class="empty">لا توجد مراكز كلفة</div>';
    $$('[data-ed],[data-del]', $('#cc-out')).forEach(b => b.onclick = async () => {
      if (b.dataset.ed) {
        const c = list.find(x => x.id == b.dataset.ed);
        const m = modal('تعديل مركز كلفة', `<label class="f">الاسم<input id="cc-name" value="${esc(c.name)}"></label>
          <label class="row small" style="margin-top:10px"><input type="checkbox" id="cc-active" ${c.active ? 'checked' : ''}> فعّال</label>
          <div class="foot"><button class="btn primary" id="cc-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
        $('#cc-save').onclick = e => run(e.target, async () => {
          await put(`/api/cost-centers/${c.id}`, { name: $('#cc-name').value, active: $('#cc-active').checked });
          m.close(); route();
        });
      }
      if (b.dataset.del && await confirmBox('حذف مركز الكلفة؟'))
        run(b, async () => { await del(`/api/cost-centers/${b.dataset.del}`); route(); });
    });
  };
  if ($('#cc-new')) $('#cc-new').onclick = () => {
    const m = modal('مركز كلفة جديد', `<label class="f">الاسم (فرع / مشروع / قسم)<input id="cc-name"></label>
      <div class="foot"><button class="btn primary" id="cc-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#cc-save').onclick = e => run(e.target, async () => {
      await post('/api/cost-centers', { name: $('#cc-name').value });
      m.close(); toast('أُضيف المركز'); route();
    });
  };
  $('#cc-go').onclick = load;
  $('#cc-print').onclick = () => printHTML('ربحية مراكز الكلفة', $('#cc-out').innerHTML);
  await load();
};


// ═════════════════════ المطعم: الصالة والطاولات ═════════════════════
const ORDER_KIND = { dine_in: 'صالة', takeaway: 'سفري', delivery: 'توصيل' };

/** يطبع تذاكر التحضير حسب وضع الطباعة لهذا الجهاز */
async function printKitchen(tickets, serverPrinted) {
  if (!tickets.length || serverPrinted) return;
  if (DEV.mode === 'agent') {
    for (const t of tickets) {
      const job = await post('/api/devices/kitchen', { ...t, return_job: true });
      await agentCall('/raw', { data: job.data, target: 'kitchen' });
    }
    return;
  }
  if (DEV.mode === 'browser') {
    printHTML('تذاكر التحضير', tickets.map(t => `<div style="page-break-after:always;border:2px solid #000;padding:12px;margin-bottom:12px">
      <h2 style="text-align:center;margin:0">${esc(t.station)}</h2>
      <p style="font-size:18px"><b>طلب ${t.number}</b>${t.table_name ? ' — ' + esc(t.table_name) : ''} — ${esc(t.order_type)}${t.waiter ? ' — ' + esc(t.waiter) : ''}</p>
      ${t.lines.map(l => `<div style="font-size:20px;font-weight:700">${esc(l.item_name)} × ${fmt(l.qty)}</div>
        ${l.notes.map(n => `<div style="padding-right:18px">← ${esc(n)}</div>`).join('')}`).join('')}
      ${t.notes ? `<p><b>ملاحظة:</b> ${esc(t.notes)}</p>` : ''}</div>`).join(''));
  }
}

PAGES.floor = async app => {
  const [tables, open] = await Promise.all([api('/api/restaurant/tables'), api('/api/orders?status=open')]);
  const areas = [...new Map(tables.map(t => [t.area_id || 0, t.area_name || 'بدون صالة'])).entries()];
  const others = open.filter(o => o.kind !== 'dine_in');
  const busy = tables.filter(t => t.busy);
  app.innerHTML = `<div class="row no-print" style="margin-bottom:14px">
      <button class="btn primary" id="fl-take"><i class="i i-bag"></i> طلب سفري</button>
      <button class="btn" id="fl-deliv"><i class="i i-truck"></i> طلب توصيل</button>
      <span class="sp"></span>
      <span class="small muted">مشغولة: <b>${busy.length}</b> من ${tables.length} — مبالغ مفتوحة: <b>${fmt(open.reduce((a, o) => a + o.total, 0))}</b></span>
      <button class="btn sm" onclick="route()"><i class="i i-refresh"></i></button></div>
    ${tables.length ? areas.map(([aid, name]) => `<div class="card"><h3>${esc(name)}</h3><div class="tbl-grid">
      ${tables.filter(t => (t.area_id || 0) === aid && t.active).map(t => `<button class="tbl ${t.busy ? 'busy' : ''}" data-table="${t.id}">
        <b>${esc(t.name)}</b>
        ${t.busy ? `<span class="amt">${fmt(t.total)}</span><span class="small">${t.orders[0].minutes} د · ${t.orders.reduce((a, o) => a + o.guests, 0)} ${t.orders.length > 1 ? ` · ${t.orders.length} فواتير` : ''}</span>`
          : `<span class="small muted">${t.seats} مقاعد</span><span class="small muted">فارغة</span>`}</button>`).join('')}
      </div></div>`).join('') : `<div class="card empty">لا توجد طاولات — أضفها من «إعداد المطعم».</div>`}
    <div class="card"><h3><i class="i i-bag"></i> طلبات السفري والتوصيل المفتوحة</h3>
      ${others.length ? `<div class="table-wrap"><table><thead><tr><th>#</th><th>النوع</th><th>الزبون</th><th>العنوان</th><th>السائق</th><th>الحالة</th><th>المبلغ</th></tr></thead><tbody>
        ${others.map(o => `<tr class="clickable" data-order="${o.id}"><td>${o.number}</td><td><span class="badge ${o.kind === 'delivery' ? 'purchase' : ''}">${ORDER_KIND[o.kind]}</span></td>
          <td>${esc(o.customer_name || o.party_name || '—')} <span class="muted small">${esc(o.customer_phone || '')}</span></td>
          <td class="small">${esc(o.address || '')}</td><td class="small">${esc(o.driver_name || '')}</td>
          <td class="small">${o.kind === 'delivery' ? ({ preparing: 'قيد التحضير', out: 'خرج للتوصيل', delivered: 'تم التسليم' }[o.delivery_status] || '') : ''}</td>
          <td>${money(o.total)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">لا توجد طلبات مفتوحة</div>'}</div>`;

  app.onclick = async e => {
    const tb = e.target.closest('[data-table]');
    if (tb) {
      const t = tables.find(x => x.id == tb.dataset.table);
      if (!t.busy) {
        const o = await run(null, () => post('/api/orders', { kind: 'dine_in', table_id: t.id }));
        return o && go(`#/order/${o.id}`);
      }
      if (t.orders.length === 1) return go(`#/order/${t.orders[0].id}`);
      const m = modal(`${esc(t.name)} — اختر الفاتورة`, t.orders.map(o => `<button class="btn" style="width:100%;margin-bottom:8px;justify-content:space-between" data-go="${o.id}">
        <span>طلب ${o.number}</span><b>${fmt(o.total)}</b></button>`).join('') + '<div class="foot"><button class="btn" data-close>إغلاق</button></div>');
      $$('[data-go]', m.el).forEach(b => b.onclick = () => { m.close(); go(`#/order/${b.dataset.go}`); });
    }
    const or = e.target.closest('[data-order]');
    if (or) go(`#/order/${or.dataset.order}`);
  };
  $('#fl-take').onclick = () => run(null, async () => { const o = await post('/api/orders', { kind: 'takeaway' }); go(`#/order/${o.id}`); });
  $('#fl-deliv').onclick = async () => {
    const menu = await api('/api/restaurant/menu');
    const m = modal('طلب توصيل جديد', `<div class="form-grid">
        <label class="f">اسم الزبون<input id="dl-name"></label><label class="f">الهاتف<input id="dl-phone"></label>
        <label class="f" style="grid-column:1/-1">العنوان<input id="dl-addr"></label>
        <label class="f">أجرة التوصيل<input type="number" step="any" id="dl-fee" value="${menu.settings.delivery_fee || 0}"></label>
        <label class="f">السائق<select id="dl-driver"><option value="">— لاحقاً —</option>${menu.drivers.map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join('')}</select></label></div>
      <div class="foot"><button class="btn primary" id="dl-go">فتح الطلب</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#dl-go').onclick = e => run(e.target, async () => {
      const o = await post('/api/orders', { kind: 'delivery', customer_name: $('#dl-name').value, customer_phone: $('#dl-phone').value,
        address: $('#dl-addr').value, delivery_fee: +$('#dl-fee').value || 0, driver_id: +$('#dl-driver').value || null });
      m.close(); go(`#/order/${o.id}`);
    });
  };
  S.timer = setInterval(() => { if (!$('.modal-bg')) route(); }, 30000);   // تحديث حالة الطاولات
};

// ═════════════════════ المطعم: شاشة الطلب ═════════════════════
PAGES.order = async (app, oid) => {
  if (!oid) return go('#/floor');
  let [o, menu] = await Promise.all([api(`/api/orders/${oid}`), api('/api/restaurant/menu')]);
  const closed = o.status !== 'open';
  $('#page-title').textContent = `طلب ${o.number} — ${ORDER_KIND[o.kind]}${o.table_name ? ' — ' + o.table_name : ''}`;
  const cats = ['الكل', ...new Set(menu.items.map(i => i.category || 'أخرى'))];
  let cat = 'الكل', dirty = false;
  const lines = o.lines.map(l => ({ id: l.id, item_id: l.item_id, name: l.item_name, qty: l.qty, price: l.price, notes: l.notes, sent_qty: l.sent_qty, ready: l.ready }));

  app.innerHTML = `<div class="pos-layout">
    <div class="pos-side">
      <div class="card pos-shift"><div class="row"><b>طلب ${o.number}</b><span class="badge ${closed ? '' : 'sale'}">${closed ? (o.status === 'paid' ? 'مدفوع' : 'ملغى') : 'مفتوح'}</span>
          <span class="sp"></span><button class="btn sm" onclick="go('#/floor')"><i class="i i-undo"></i> الصالة</button></div>
        <div class="row small" style="margin-top:6px">
          ${o.kind === 'dine_in' ? `<label class="row"><i class="i i-user"></i> <input type="number" min="1" id="or-guests" value="${o.guests}" style="width:60px" ${closed ? 'disabled' : ''}></label>` : ''}
          <select id="or-waiter" ${closed ? 'disabled' : ''}><option value="">— النادل —</option>${menu.waiters.map(w => `<option value="${w.id}" ${w.id === o.waiter_id ? 'selected' : ''}>${esc(w.name)}</option>`).join('')}</select>
          ${o.kind === 'delivery' ? `<select id="or-driver" ${closed ? 'disabled' : ''}><option value="">— السائق —</option>${menu.drivers.map(d => `<option value="${d.id}" ${d.id === o.driver_id ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}</select>
            <select id="or-dstatus" ${closed ? 'disabled' : ''}>${Object.entries({ preparing: 'قيد التحضير', out: 'خرج للتوصيل', delivered: 'تم التسليم' }).map(([k, v]) => `<option value="${k}" ${k === o.delivery_status ? 'selected' : ''}>${v}</option>`).join('')}</select>` : ''}
        </div>
        ${o.kind === 'delivery' ? `<div class="small muted" style="margin-top:4px">${esc(o.customer_name || '')} · ${esc(o.customer_phone || '')} · ${esc(o.address || '')}</div>` : ''}</div>
      <div class="card" style="flex:1;display:flex;flex-direction:column;min-height:0">
        <div class="table-wrap" style="flex:1;overflow-y:auto"><table class="pos-cart"><thead><tr><th>الصنف</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th><th></th></tr></thead>
          <tbody id="or-lines"></tbody></table></div>
        <div class="pos-totals" id="or-totals"></div>
        ${closed ? `<div class="row" style="margin-top:8px">${o.invoice_id ? `<button class="btn primary" id="or-inv"><i class="i i-receipt"></i> عرض الفاتورة</button>` : ''}</div>` : `
        <div class="row" style="margin-top:8px">
          <button class="btn ai" id="or-send" style="flex:1;justify-content:center"><i class="i i-kitchen"></i> إرسال للمطبخ</button>
          <button class="btn primary" id="or-pay" style="flex:1;justify-content:center"><i class="i i-cash"></i> دفع</button></div>
        <div class="row" style="margin-top:6px">
          <button class="btn sm" id="or-bill"><i class="i i-print"></i> كشف حساب</button>
          ${o.kind === 'dine_in' ? '<button class="btn sm" id="or-move"><i class="i i-move"></i> نقل</button><button class="btn sm" id="or-split"><i class="i i-scissors"></i> فصل</button><button class="btn sm" id="or-merge"><i class="i i-merge"></i> دمج</button>' : ''}
          <span class="sp"></span><button class="btn sm danger" id="or-cancel">إلغاء الطلب</button></div>`}
      </div>
    </div>
    <div class="pos-main">
      <div class="card"><div class="row"><input id="or-search" placeholder="بحث عن صنف…" style="flex:1;font-size:16px">
        </div><div class="chips" id="or-cats" style="margin-top:8px"></div></div>
      <div class="pos-grid" id="or-grid"></div>
    </div></div>`;

  const totals = () => {
    const sub = lines.reduce((a, l) => a + l.qty * l.price, 0);
    const net = sub - (o.discount || 0);
    const tax = Math.round(net * (o.tax_rate || 0)) / 100;
    const service = Math.round(net * (o.service_rate || 0)) / 100;
    return { sub, net, tax, service, total: net + tax + service + (o.delivery_fee || 0) };
  };
  const drawCats = () => { $('#or-cats').innerHTML = cats.map(c => `<button class="chip ${c === cat ? 'on' : ''}" data-cat="${esc(c)}">${esc(c)}</button>`).join(''); };
  const drawGrid = () => {
    const q = ($('#or-search').value || '').trim();
    const list = menu.items.filter(i => (cat === 'الكل' || (i.category || 'أخرى') === cat) && (!q || i.name.includes(q) || i.code.includes(q)));
    $('#or-grid').innerHTML = list.map(i => `<button class="pos-item" data-add="${i.id}" ${closed ? 'disabled' : ''}>
      <b>${esc(i.name)}</b><span class="muted small">${fmt(i.sale_price)}</span></button>`).join('') || '<div class="empty">لا أصناف</div>';
  };
  const draw = () => {
    $('#or-lines').innerHTML = lines.length ? lines.map((l, i) => `<tr>
      <td><b>${esc(l.name)}</b> ${l.sent_qty >= l.qty ? (l.ready ? '<span class="badge sale">جاهز</span>' : '<span class="badge purchase">في المطبخ</span>') : l.sent_qty > 0 ? '<span class="badge">جزء مُرسل</span>' : '<span class="badge low">جديد</span>'}
        ${l.notes.map(n => `<div class="small muted">← ${esc(n.name)}${n.price ? ` (+${fmt(n.price)})` : ''}</div>`).join('')}
        ${closed ? '' : `<button class="btn sm ghost" data-mod="${i}"><i class="i i-plus"></i> إضافة/ملاحظة</button>`}</td>
      <td>${closed ? fmt(l.qty) : `<div class="qty"><button data-q="${i},-1">−</button><input type="number" step="any" min="0" data-qty="${i}" value="${l.qty}"><button data-q="${i},1">+</button></div>`}</td>
      <td class="num">${fmt(l.price)}</td><td class="num">${fmt(l.qty * l.price)}</td>
      <td>${closed ? '' : `<button class="btn sm ghost" data-rm="${i}"><i class="i i-x"></i></button>`}</td></tr>`).join('')
      : '<tr><td colspan="5" class="empty">اختر الأصناف من القائمة</td></tr>';
    const t = totals();
    $('#or-totals').innerHTML = `<div class="r"><span>المجموع</span><span class="num">${fmt(t.sub)}</span></div>
      ${o.discount ? `<div class="r"><span>الحسم</span><span class="num">${fmt(o.discount)}</span></div>` : ''}
      ${t.tax ? `<div class="r"><span>الضريبة ${fmt(o.tax_rate)}%</span><span class="num">${fmt(t.tax)}</span></div>` : ''}
      ${t.service ? `<div class="r"><span>رسم الخدمة ${fmt(o.service_rate)}%</span><span class="num">${fmt(t.service)}</span></div>` : ''}
      ${o.delivery_fee ? `<div class="r"><span>أجرة التوصيل</span><span class="num">${fmt(o.delivery_fee)}</span></div>` : ''}
      <div class="r total"><span>الإجمالي</span><span class="num">${fmt(t.total)} ${esc(baseCur().symbol)}</span></div>`;
  };
  const save = async () => {
    const body = { lines: lines.map(l => ({ id: l.id, item_id: l.item_id, qty: l.qty, price: l.price, notes: l.notes })) };
    if ($('#or-guests')) body.guests = +$('#or-guests').value || 1;
    body.waiter_id = +$('#or-waiter').value || null;
    if ($('#or-driver')) { body.driver_id = +$('#or-driver').value || null; body.delivery_status = $('#or-dstatus').value; }
    o = await put(`/api/orders/${oid}`, body);
    lines.length = 0;
    o.lines.forEach(l => lines.push({ id: l.id, item_id: l.item_id, name: l.item_name, qty: l.qty, price: l.price, notes: l.notes, sent_qty: l.sent_qty, ready: l.ready }));
    dirty = false;
    draw();
  };
  const ensureSaved = async () => { if (dirty) await save(); };

  $('#or-cats').onclick = e => { const b = e.target.closest('[data-cat]'); if (b) { cat = b.dataset.cat; drawCats(); drawGrid(); } };
  $('#or-search').oninput = drawGrid;
  drawCats(); drawGrid(); draw();
  if (closed) {
    if ($('#or-inv')) $('#or-inv').onclick = () => showInvoice(o.invoice_id);
    return;
  }

  $('#or-grid').onclick = e => {
    const b = e.target.closest('[data-add]');
    if (!b) return;
    const it = menu.items.find(i => i.id == b.dataset.add);
    const same = lines.find(l => l.item_id === it.id && !l.notes.length && l.sent_qty < l.qty + 1e-9 && l.sent_qty === 0);
    if (same) same.qty += 1; else lines.push({ item_id: it.id, name: it.name, qty: 1, price: it.sale_price, notes: [], sent_qty: 0, ready: 0 });
    dirty = true; draw();
  };
  $('#or-lines').onclick = e => {
    const rm = e.target.closest('[data-rm]'), q = e.target.closest('[data-q]'), md = e.target.closest('[data-mod]');
    if (rm) { lines.splice(+rm.dataset.rm, 1); dirty = true; draw(); }
    if (q) {
      const [i, d] = q.dataset.q.split(',').map(Number);
      lines[i].qty = Math.max(0, +(lines[i].qty + d).toFixed(3));
      if (!lines[i].qty) lines.splice(i, 1);
      dirty = true; draw();
    }
    if (md) {
      const l = lines[+md.dataset.mod];
      const mods = menu.modifiers.filter(m => !m.item_id || m.item_id === l.item_id);
      const base = menu.items.find(i => i.id === l.item_id)?.sale_price ?? l.price;
      const m = modal(`إضافات «${esc(l.name)}»`, `<div class="perm-grid">${mods.map(x => `<label class="row small"><input type="checkbox" data-m="${x.id}" ${l.notes.some(n => n.name === x.name) ? 'checked' : ''}>
          ${esc(x.name)}${x.price ? ` <span class="muted">(+${fmt(x.price)})</span>` : ''}</label>`).join('') || '<span class="muted small">لا إضافات معرّفة — أضفها من إعداد المطعم.</span>'}</div>
        <label class="f" style="margin-top:10px">ملاحظة حرة للمطبخ<input id="md-note" value="${esc(l.notes.filter(n => n.free).map(n => n.name).join('، '))}"></label>
        <div class="foot"><button class="btn primary" id="md-ok">تم</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#md-ok').onclick = () => {
        const chosen = $$('[data-m]:checked', m.el).map(c => mods.find(x => x.id == c.dataset.m));
        l.notes = chosen.map(x => ({ name: x.name, price: x.price }));
        const free = $('#md-note').value.trim();
        if (free) l.notes.push({ name: free, price: 0, free: true });
        l.price = base + chosen.reduce((a, x) => a + (x.price || 0), 0);
        dirty = true; m.close(); draw();
      };
    }
  };
  $('#or-lines').oninput = e => {
    const inp = e.target.closest('[data-qty]');
    if (inp) { lines[+inp.dataset.qty].qty = +inp.value || 0; dirty = true; }
  };
  $('#or-lines').onchange = e => { if (e.target.closest('[data-qty]')) draw(); };
  ['#or-guests', '#or-waiter', '#or-driver', '#or-dstatus'].forEach(x => { if ($(x)) $(x).onchange = () => { dirty = true; }; });

  $('#or-send').onclick = e => run(e.target, async () => {
    await save();
    const r = await post(`/api/orders/${oid}/send`, { print: DEV.mode === 'server' });
    if (!r.tickets.length) return toast('لا أصناف جديدة لإرسالها');
    await printKitchen(r.tickets, r.printed).catch(err => toast('تعذرت طباعة تذكرة المطبخ: ' + err.message, true));
    if (r.print_error) toast('تعذرت طباعة تذكرة المطبخ: ' + r.print_error, true);
    toast(`أُرسل ${r.tickets.reduce((a, t) => a + t.lines.length, 0)} صنف للمطبخ`);
    o = r.order;
    lines.length = 0;
    o.lines.forEach(l => lines.push({ id: l.id, item_id: l.item_id, name: l.item_name, qty: l.qty, price: l.price, notes: l.notes, sent_qty: l.sent_qty, ready: l.ready }));
    draw();
  });

  $('#or-bill').onclick = e => run(e.target, async () => {
    await ensureSaved();
    const t = totals();
    printHTML(`كشف حساب — طلب ${o.number}${o.table_name ? ' — ' + o.table_name : ''}`,
      `<table><thead><tr><th>الصنف</th><th>الكمية</th><th>السعر</th><th>الإجمالي</th></tr></thead><tbody>
        ${lines.map(l => `<tr><td>${esc(l.name)}${l.notes.map(n => `<br><small>← ${esc(n.name)}</small>`).join('')}</td><td>${fmt(l.qty)}</td><td>${fmt(l.price)}</td><td>${fmt(l.qty * l.price)}</td></tr>`).join('')}
        </tbody><tfoot><tr><td colspan="3">المجموع</td><td>${fmt(t.sub)}</td></tr>
        ${t.tax ? `<tr><td colspan="3">الضريبة</td><td>${fmt(t.tax)}</td></tr>` : ''}
        ${t.service ? `<tr><td colspan="3">رسم الخدمة</td><td>${fmt(t.service)}</td></tr>` : ''}
        ${o.delivery_fee ? `<tr><td colspan="3">أجرة التوصيل</td><td>${fmt(o.delivery_fee)}</td></tr>` : ''}
        <tr><td colspan="3">الإجمالي</td><td>${fmt(t.total)}</td></tr></tfoot></table><p style="text-align:center">هذا كشف مبدئي وليس فاتورة</p>`);
  });

  $('#or-pay').onclick = async () => {
    if (!lines.length) return toast('الطلب فارغ', true);
    await run(null, ensureSaved);
    const t = totals();
    const m = modal('دفع الطلب', `<div class="pay-total">الإجمالي: <b>${fmt(t.total)}</b> ${esc(baseCur().symbol)}</div>
      <div class="tabs" id="pay-tabs"><button class="on" data-p="cash">نقداً</button><button data-p="credit">آجل (على حساب زبون)</button></div>
      <div id="pay-cash"><div class="form-grid">
        <label class="f">المدفوع<input type="number" id="pay-amt" step="any" value="${t.total}"></label>
        <label class="f">الصندوق<select id="pay-box">${menu.cash_accounts.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></label></div>
        <div class="pay-change" id="pay-change"></div></div>
      <div id="pay-credit" style="display:none"><label class="f">الزبون<select id="pay-party"><option value="">— اختر —</option>
        ${menu.customers.map(c => `<option value="${c.id}" ${c.id === o.party_id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label></div>
      <div class="foot"><button class="btn primary" id="pay-go">تأكيد الدفع</button><button class="btn" data-close>إلغاء</button></div>`);
    let method = 'cash';
    const change = () => { $('#pay-change').innerHTML = `الباقي للزبون: <b>${fmt(Math.max(0, (+$('#pay-amt').value || 0) - t.total))}</b>`; };
    $('#pay-amt').oninput = change; change(); $('#pay-amt').select();
    $('#pay-tabs').onclick = e => {
      const b = e.target.closest('button'); if (!b) return;
      method = b.dataset.p;
      $$('#pay-tabs button').forEach(x => x.classList.toggle('on', x === b));
      $('#pay-cash').style.display = method === 'cash' ? '' : 'none';
      $('#pay-credit').style.display = method === 'credit' ? '' : 'none';
    };
    $('#pay-go').onclick = e => run(e.target, async () => {
      if (method === 'credit' && !$('#pay-party').value) throw new Error('اختر الزبون للبيع الآجل');
      const inv = await post(`/api/orders/${oid}/pay`, { payment: method, party_id: +$('#pay-party').value || null,
        cash_account_id: method === 'cash' ? +$('#pay-box').value : null, delivered: o.kind === 'delivery' });
      const paid = method === 'cash' ? (+$('#pay-amt').value || t.total) : 0;
      m.close();
      toast(`فاتورة رقم ${inv.number} — ${fmt(inv.total)}`);
      if (menu.settings.pos_print === '1')
        printSale(inv, { paid, change: Math.max(0, paid - t.total), drawer: method === 'cash' }).catch(err => toast('تعذرت الطباعة: ' + err.message, true));
      go('#/floor');
    });
  };

  $('#or-cancel').onclick = async () => {
    const m = modal('إلغاء الطلب', `<label class="f">سبب الإلغاء<input id="cn-reason"></label>
      <p class="small muted">الإلغاء بعد الإرسال للمطبخ يحتاج صلاحية الحذف، ويُسجَّل في سجل التدقيق.</p>
      <div class="foot"><button class="btn danger" id="cn-go">إلغاء الطلب</button><button class="btn" data-close>تراجع</button></div>`);
    $('#cn-go').onclick = e => run(e.target, async () => {
      await post(`/api/orders/${oid}/cancel`, { reason: $('#cn-reason').value });
      m.close(); toast('أُلغي الطلب'); go('#/floor');
    });
  };

  if ($('#or-move')) $('#or-move').onclick = async () => {
    await run(null, ensureSaved);
    const free = (await api('/api/restaurant/tables')).filter(t => t.active && t.id !== o.table_id);
    const m = modal('نقل الطلب إلى طاولة', `<div class="tbl-grid">${free.map(t => `<button class="tbl ${t.busy ? 'busy' : ''}" data-t="${t.id}"><b>${esc(t.name)}</b>
      <span class="small">${t.busy ? 'مشغولة' : 'فارغة'}</span></button>`).join('')}</div><div class="foot"><button class="btn" data-close>إلغاء</button></div>`);
    $$('[data-t]', m.el).forEach(b => b.onclick = () => run(b, async () => {
      await post(`/api/orders/${oid}/move`, { table_id: +b.dataset.t });
      m.close(); toast('نُقل الطلب'); go('#/floor');
    }));
  };
  if ($('#or-merge')) $('#or-merge').onclick = async () => {
    await run(null, ensureSaved);
    const others = (await api('/api/orders?status=open&kind=dine_in')).filter(x => x.id !== o.id);
    if (!others.length) return toast('لا توجد طلبات أخرى مفتوحة');
    const m = modal('دمج هذا الطلب مع', others.map(x => `<button class="btn" style="width:100%;margin-bottom:8px;justify-content:space-between" data-into="${x.id}">
      <span>${esc(x.table_name || '')} — طلب ${x.number}</span><b>${fmt(x.total)}</b></button>`).join('') + '<div class="foot"><button class="btn" data-close>إلغاء</button></div>');
    $$('[data-into]', m.el).forEach(b => b.onclick = () => run(b, async () => {
      const merged = await post(`/api/orders/${oid}/merge`, { into: +b.dataset.into });
      m.close(); toast('دُمج الطلبان'); go(`#/order/${merged.id}`);
    }));
  };
  if ($('#or-split')) $('#or-split').onclick = async () => {
    await run(null, ensureSaved);
    if (!lines.length) return toast('الطلب فارغ', true);
    const m = modal('فصل أصناف إلى فاتورة مستقلة', `<table class="lines-table small"><thead><tr><th>الصنف</th><th>الكمية</th><th style="width:110px">يُفصل</th></tr></thead><tbody>
      ${lines.map(l => `<tr><td>${esc(l.name)}</td><td class="num">${fmt(l.qty)}</td><td><input type="number" step="any" min="0" max="${l.qty}" data-sp="${l.id}" value="0"></td></tr>`).join('')}
      </tbody></table><p class="small muted">الأصناف المفصولة تنتقل إلى طلب جديد على الطاولة نفسها ليُدفع منفرداً.</p>
      <div class="foot"><button class="btn primary" id="sp-go">فصل</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#sp-go').onclick = e => run(e.target, async () => {
      const parts = $$('[data-sp]', m.el).map(i => ({ line_id: +i.dataset.sp, qty: +i.value || 0 })).filter(x => x.qty > 0);
      const part = await post(`/api/orders/${oid}/split`, { parts });
      m.close(); toast(`فُصل إلى طلب ${part.number}`); go(`#/order/${part.id}`);
    });
  };
};

// ═════════════════════ المطعم: شاشة المطبخ ═════════════════════
PAGES.kitchen = async (app, stationArg) => {
  const load = async () => {
    const d = await api('/api/kitchen' + (stationArg ? `?station_id=${stationArg}` : ''));
    const byOrder = new Map();
    d.lines.forEach(l => { if (!byOrder.has(l.order_id)) byOrder.set(l.order_id, { ...l, items: [] }); byOrder.get(l.order_id).items.push(l); });
    app.innerHTML = `<div class="row no-print" style="margin-bottom:14px">
        <div class="chips"><button class="chip ${!stationArg ? 'on' : ''}" data-st="">كل الأقسام</button>
          ${d.stations.map(s => `<button class="chip ${s.id == stationArg ? 'on' : ''}" data-st="${s.id}">${esc(s.name)}</button>`).join('')}</div>
        <span class="sp"></span><span class="small muted">${d.lines.length} صنف بانتظار التحضير · يتحدّث كل 10 ثوانٍ</span></div>
      ${byOrder.size ? `<div class="kds">${[...byOrder.values()].map(o => `<div class="card kds-card ${o.items.some(i => i.minutes >= 15) ? 'late' : ''}">
          <h3>${o.table_name ? esc(o.table_name) : esc(o.kind_label)} <span class="muted small">طلب ${o.number}</span><span class="sp"></span>
            <span class="badge ${o.items[0].minutes >= 15 ? 'low' : ''}">${o.items[0].minutes} د</span></h3>
          ${o.items.map(i => `<div class="kds-line"><div><b>${esc(i.item_name)}</b> × ${fmt(i.sent_qty)}
              ${i.notes.map(n => `<div class="small neg">← ${esc(n)}</div>`).join('')}</div>
            <button class="btn sm primary" data-ready="${i.id}">جاهز</button></div>`).join('')}
          <button class="btn sm" style="width:100%;justify-content:center;margin-top:8px" data-all="${o.items.map(i => i.id).join(',')}">الطلب كله جاهز</button></div>`).join('')}</div>`
        : '<div class="card empty">لا توجد طلبات بانتظار التحضير </div>'}`;
  };
  app.onclick = async e => {
    const st = e.target.closest('[data-st]');
    if (st) return go(`#/kitchen${st.dataset.st ? '/' + st.dataset.st : ''}`);
    const b = e.target.closest('[data-ready],[data-all]');
    if (!b) return;
    const ids = (b.dataset.ready || b.dataset.all).split(',').map(Number);
    await run(b, async () => { await post('/api/kitchen/ready', { line_ids: ids }); await load(); });
  };
  await load();
  S.timer = setInterval(() => load().catch(() => {}), 10000);
};


// ═════════════════════ المطعم: الإعداد ═════════════════════
PAGES.rsetup = async (app, tab = 'general') => {
  const tabs = { general: 'عام', tables: 'الطاولات والصالات', menu: 'القائمة والوصفات', modifiers: 'الإضافات', stations: 'أقسام التحضير', drivers: 'السائقون' };
  app.innerHTML = `<div class="tabs">${Object.entries(tabs).map(([k, v]) => `<button data-t="${k}" class="${k === tab ? 'on' : ''}">${v}</button>`).join('')}</div><div id="rs-body"></div>`;
  $('.tabs', app).onclick = e => { const b = e.target.closest('button'); if (b) go(`#/rsetup/${b.dataset.t}`); };
  const B = $('#rs-body');

  /** قائمة بسيطة باسم واحد (صالات، أقسام) */
  const simpleList = async (path, title, label, extraCols = []) => {
    const list = await api(path);
    B.innerHTML = `<div class="card"><h3>${title}<span class="sp"></span><button class="btn primary sm" id="sl-new"><i class="i i-plus"></i> ${label}</button></h3>
      ${list.length ? `<table><thead><tr><th>الاسم</th>${extraCols.map(c => `<th>${c.title}</th>`).join('')}<th></th></tr></thead><tbody>
        ${list.map(x => `<tr><td>${esc(x.name)}</td>${extraCols.map(c => `<td>${c.show(x)}</td>`).join('')}
          <td class="act"><button class="btn sm" data-ed="${x.id}"><i class="i i-edit"></i></button><button class="btn sm danger" data-del="${x.id}"><i class="i i-trash"></i></button></td></tr>`).join('')}
        </tbody></table>` : `<div class="empty">لا يوجد ${label} بعد</div>`}</div>`;
    const form = x => {
      const m = modal(x ? `تعديل ${label}` : `${label} جديد`, `<div class="form-grid"><label class="f">الاسم<input id="sl-name" value="${esc(x?.name)}"></label>
        ${extraCols.map(c => c.input(x)).join('')}</div>
        <div class="foot"><button class="btn primary" id="sl-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#sl-save').onclick = e => run(e.target, async () => {
        const body = { name: $('#sl-name').value };
        extraCols.forEach(c => Object.assign(body, c.read()));
        x ? await put(`${path}/${x.id}`, body) : await post(path, body);
        m.close(); toast('تم الحفظ'); route();
      });
    };
    $('#sl-new').onclick = () => form(null);
    B.querySelector('tbody')?.addEventListener('click', async e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.ed) form(list.find(x => x.id == b.dataset.ed));
      if (b.dataset.del && await confirmBox('حذف؟')) run(b, async () => { await del(`${path}/${b.dataset.del}`); route(); });
    });
  };

  if (tab === 'general') {
    const st = S.settings;
    B.innerHTML = `<div class="card"><h3><i class="i i-table"></i> وضع المطعم</h3>
      <label class="row"><input type="checkbox" id="rs-mode" ${st.restaurant_mode === '1' ? 'checked' : ''}> تفعيل شاشات المطعم (الصالة، الطلبات، المطبخ)</label>
      <div class="form-grid" style="margin-top:12px">
        <label class="f">رسم الخدمة لطلبات الصالة %<input type="number" step="any" min="0" id="rs-service" value="${st.service_rate || 0}"></label>
        <label class="f">أجرة التوصيل الافتراضية<input type="number" step="any" min="0" id="rs-fee" value="${st.delivery_fee || 0}"></label></div>
      <label class="row small" style="margin-top:12px"><input type="checkbox" id="rs-neg" ${st.allow_negative_stock === '1' ? 'checked' : ''}>
        السماح بالبيع مع نقص المكوّنات (المخزون بالسالب) — مناسب للمطاعم التي لا تجرد مكوّناتها يومياً</label>
      <div class="row" style="margin-top:14px"><button class="btn primary" id="rs-save"><i class="i i-save"></i> حفظ</button></div>
      <p class="small muted">رسم الخدمة وأجرة التوصيل يُرحَّلان إلى حـ/ 44 «إيرادات رسم الخدمة والتوصيل» منفصلين عن المبيعات.
        الطبق ذو الوصفة يخصم مكوّناته من المخزون عند دفع الطلب، وتكلفته = مجموع تكلفة مكوّناته.</p></div>`;
    $('#rs-save').onclick = e => run(e.target, async () => {
      await put('/api/settings', { restaurant_mode: $('#rs-mode').checked ? '1' : '0', service_rate: String(+$('#rs-service').value || 0),
        delivery_fee: String(+$('#rs-fee').value || 0), allow_negative_stock: $('#rs-neg').checked ? '1' : '0' });
      await loadSettings(); toast('حُفظت إعدادات المطعم'); route();
    });
    return;
  }

  if (tab === 'tables') {
    const [areas, tables] = await Promise.all([api('/api/restaurant/areas'), api('/api/restaurant/tables')]);
    B.innerHTML = `<div class="grid g2"><div class="card"><h3><i class="i i-building"></i> الصالات<span class="sp"></span><button class="btn sm" id="ar-new"><i class="i i-plus"></i> صالة</button></h3>
        ${areas.length ? `<table><tbody>${areas.map(a => `<tr><td>${esc(a.name)}</td><td class="act"><button class="btn sm danger" data-adel="${a.id}"><i class="i i-trash"></i></button></td></tr>`).join('')}</tbody></table>` : '<div class="empty">لا صالات</div>'}</div>
      <div class="card"><h3><i class="i i-table"></i> الطاولات <span class="muted small">(${tables.length})</span><span class="sp"></span>
          <button class="btn sm" id="tb-bulk"><i class="i i-plus"></i> عدة طاولات</button><button class="btn primary sm" id="tb-new"><i class="i i-plus"></i> طاولة</button></h3>
        ${tables.length ? `<table><thead><tr><th>الطاولة</th><th>الصالة</th><th>المقاعد</th><th></th></tr></thead><tbody>
          ${tables.map(t => `<tr><td>${esc(t.name)} ${t.active ? '' : '<span class="badge">موقوفة</span>'}</td><td class="small">${esc(t.area_name || '—')}</td><td>${t.seats}</td>
            <td class="act"><button class="btn sm" data-ted="${t.id}"><i class="i i-edit"></i></button><button class="btn sm danger" data-tdel="${t.id}"><i class="i i-trash"></i></button></td></tr>`).join('')}
          </tbody></table>` : '<div class="empty">لا طاولات</div>'}</div></div>`;
    const areaOpts = sel => '<option value="">—</option>' + areas.map(a => `<option value="${a.id}" ${a.id === sel ? 'selected' : ''}>${esc(a.name)}</option>`).join('');
    $('#ar-new').onclick = () => {
      const m = modal('صالة جديدة', `<label class="f">الاسم<input id="ar-name" placeholder="الصالة الرئيسية / التراس"></label>
        <div class="foot"><button class="btn primary" id="ar-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#ar-save').onclick = e => run(e.target, async () => { await post('/api/restaurant/areas', { name: $('#ar-name').value }); m.close(); route(); });
    };
    const tform = t => {
      const m = modal(t ? 'تعديل طاولة' : 'طاولة جديدة', `<div class="form-grid"><label class="f">الاسم<input id="tb-name" value="${esc(t?.name)}"></label>
          <label class="f">الصالة<select id="tb-area">${areaOpts(t?.area_id)}</select></label>
          <label class="f">المقاعد<input type="number" min="1" id="tb-seats" value="${t?.seats ?? 4}"></label></div>
        ${t ? `<label class="row small" style="margin-top:10px"><input type="checkbox" id="tb-active" ${t.active ? 'checked' : ''}> فعّالة</label>` : ''}
        <div class="foot"><button class="btn primary" id="tb-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#tb-save').onclick = e => run(e.target, async () => {
        const body = { name: $('#tb-name').value, area_id: +$('#tb-area').value || null, seats: +$('#tb-seats').value || 4, active: $('#tb-active') ? $('#tb-active').checked : true };
        t ? await put(`/api/restaurant/tables/${t.id}`, body) : await post('/api/restaurant/tables', body);
        m.close(); route();
      });
    };
    $('#tb-new').onclick = () => tform(null);
    $('#tb-bulk').onclick = () => {
      const m = modal('إضافة عدة طاولات', `<div class="form-grid"><label class="f">العدد<input type="number" min="1" max="100" id="bk-n" value="10"></label>
          <label class="f">تبدأ من رقم<input type="number" min="1" id="bk-from" value="${tables.length + 1}"></label>
          <label class="f">الصالة<select id="bk-area">${areaOpts()}</select></label>
          <label class="f">المقاعد<input type="number" min="1" id="bk-seats" value="4"></label></div>
        <div class="foot"><button class="btn primary" id="bk-go">إضافة</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#bk-go').onclick = e => run(e.target, async () => {
        const n = Math.min(100, +$('#bk-n').value || 0), from = +$('#bk-from').value || 1;
        for (let i = 0; i < n; i++)
          await post('/api/restaurant/tables', { name: `طاولة ${from + i}`, area_id: +$('#bk-area').value || null, seats: +$('#bk-seats').value || 4 });
        m.close(); toast(`أُضيفت ${n} طاولة`); route();
      });
    };
    B.onclick = async e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.ted) tform(tables.find(t => t.id == b.dataset.ted));
      if (b.dataset.tdel && await confirmBox('حذف الطاولة؟')) run(b, async () => { await del(`/api/restaurant/tables/${b.dataset.tdel}`); route(); });
      if (b.dataset.adel && await confirmBox('حذف الصالة؟')) run(b, async () => { await del(`/api/restaurant/areas/${b.dataset.adel}`); route(); });
    };
    return;
  }

  if (tab === 'stations') return simpleList('/api/restaurant/stations', 'أقسام التحضير (لتوجيه تذاكر المطبخ)', 'قسم');
  if (tab === 'drivers') return simpleList('/api/restaurant/drivers', 'سائقو التوصيل', 'سائق', [
    { title: 'الهاتف', show: x => esc(x.phone || ''), input: x => `<label class="f">الهاتف<input id="dr-phone" value="${esc(x?.phone)}"></label>`,
      read: () => ({ phone: $('#dr-phone').value, active: 1 }) }]);

  if (tab === 'modifiers') {
    const [mods, items] = await Promise.all([api('/api/restaurant/modifiers'), api('/api/items')]);
    B.innerHTML = `<div class="card"><h3>الإضافات والخيارات<span class="sp"></span><button class="btn primary sm" id="md-new"><i class="i i-plus"></i> إضافة</button></h3>
      ${mods.length ? `<table><thead><tr><th>الإضافة</th><th>فرق السعر</th><th>خاصة بصنف</th><th></th></tr></thead><tbody>
        ${mods.map(m => `<tr><td>${esc(m.name)}</td><td>${m.price ? money(m.price) : '<span class="muted">مجاناً</span>'}</td>
          <td class="small">${m.item_id ? esc(items.find(i => i.id === m.item_id)?.name || '') : '<span class="muted">كل الأصناف</span>'}</td>
          <td class="act"><button class="btn sm" data-ed="${m.id}"><i class="i i-edit"></i></button><button class="btn sm danger" data-del="${m.id}"><i class="i i-trash"></i></button></td></tr>`).join('')}
        </tbody></table>` : '<div class="empty">لا إضافات — مثل «بدون بصل»، «إضافة جبنة +500»، «حجم كبير +1000».</div>'}</div>`;
    const form = x => {
      const m = modal(x ? 'تعديل إضافة' : 'إضافة جديدة', `<div class="form-grid"><label class="f">الاسم<input id="mo-name" value="${esc(x?.name)}"></label>
          <label class="f">فرق السعر (0 = مجاناً)<input type="number" step="any" min="0" id="mo-price" value="${x?.price ?? 0}"></label>
          <label class="f" style="grid-column:1/-1">خاصة بصنف<select id="mo-item"><option value="">كل الأصناف</option>
            ${items.filter(i => i.sale_price > 0).map(i => `<option value="${i.id}" ${x && x.item_id === i.id ? 'selected' : ''}>${esc(i.name)}</option>`).join('')}</select></label></div>
        <div class="foot"><button class="btn primary" id="mo-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#mo-save').onclick = e => run(e.target, async () => {
        const body = { name: $('#mo-name').value, price: +$('#mo-price').value || 0, item_id: +$('#mo-item').value || null, active: 1 };
        x ? await put(`/api/restaurant/modifiers/${x.id}`, body) : await post('/api/restaurant/modifiers', body);
        m.close(); route();
      });
    };
    $('#md-new').onclick = () => form(null);
    B.querySelector('tbody')?.addEventListener('click', async e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.ed) form(mods.find(x => x.id == b.dataset.ed));
      if (b.dataset.del && await confirmBox('حذف الإضافة؟')) run(b, async () => { await del(`/api/restaurant/modifiers/${b.dataset.del}`); route(); });
    });
    return;
  }

  // القائمة والوصفات
  const [items, stations, recipes] = await Promise.all([api('/api/items'), api('/api/restaurant/stations'), api('/api/reports/recipes')]);
  const rmap = Object.fromEntries(recipes.map(r => [r.item_id, r]));
  const sellable = items.filter(i => i.sale_price > 0);
  B.innerHTML = `<div class="card"><h3><i class="i i-clipboard"></i> أصناف القائمة <span class="muted small">(${sellable.length})</span><span class="sp"></span>
      <input id="mn-search" placeholder="بحث…" style="width:200px"></h3>
    ${sellable.length ? `<div class="table-wrap"><table><thead><tr><th>الصنف</th><th>التصنيف</th><th>قسم التحضير</th><th>السعر</th><th>تكلفة الوصفة</th><th>نسبة التكلفة</th><th></th></tr></thead><tbody>
      ${sellable.map(i => { const r = rmap[i.id]; return `<tr data-name="${esc(i.code + ' ' + i.name)}"><td>${esc(i.name)} ${i.is_recipe ? '<span class="badge sale">وصفة</span>' : ''}</td>
        <td class="small">${esc(i.category || '—')}</td><td class="small">${esc(stations.find(s => s.id === i.station_id)?.name || '—')}</td>
        <td>${money(i.sale_price)}</td><td>${r ? money(r.cost) : '<span class="muted">—</span>'}</td>
        <td>${r && r.food_cost_pct != null ? `<span class="${r.food_cost_pct > 40 ? 'neg' : 'pos'}">${fmt(r.food_cost_pct)}%</span>` : '—'}</td>
        <td class="act"><button class="btn sm" data-menu="${i.id}"><i class="i i-tag"></i> تصنيف</button><button class="btn sm primary" data-rec="${i.id}"><i class="i i-receipt"></i> الوصفة</button></td></tr>`; }).join('')}
      </tbody></table></div>` : '<div class="empty">أضف الأصناف من «المواد والمخزون» بسعر بيع لتظهر هنا.</div>'}
    <p class="small muted">نسبة تكلفة الطعام المعتادة ٢٥–٣٥٪؛ ما فوق ٤٠٪ يظهر بالأحمر. المكوّنات تُضاف كمواد عادية (بلا سعر بيع) وتُشترى بفواتير الشراء.</p></div>`;
  filterRows($('#mn-search'), B);
  B.querySelector('tbody')?.addEventListener('click', async e => {
    const b = e.target.closest('button'); if (!b) return;
    const it = items.find(i => i.id == (b.dataset.menu || b.dataset.rec));
    if (b.dataset.menu) {
      const catsList = [...new Set(items.map(i => i.category).filter(Boolean))];
      const m = modal(`تصنيف «${esc(it.name)}»`, `<datalist id="cat-dl">${catsList.map(c => `<option value="${esc(c)}">`).join('')}</datalist>
        <div class="form-grid"><label class="f">التصنيف في القائمة<input id="mn-cat" list="cat-dl" value="${esc(it.category)}" placeholder="مشاوي / مقبلات / مشروبات"></label>
          <label class="f">قسم التحضير<select id="mn-st"><option value="">المطبخ العام</option>${stations.map(s => `<option value="${s.id}" ${s.id === it.station_id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label></div>
        <div class="foot"><button class="btn primary" id="mn-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
      $('#mn-save').onclick = ev => run(ev.target, async () => {
        await put(`/api/items/${it.id}`, { name: it.name, unit: it.unit, sale_price: it.sale_price, min_qty: it.min_qty, barcode: it.barcode,
          track_batches: it.track_batches, category: $('#mn-cat').value, station_id: +$('#mn-st').value || null });
        m.close(); route();
      });
    }
    if (b.dataset.rec) recipeModal(it, items, route);
  });
};

// ═════════════════════ الإقفال المالي ═════════════════════
PAGES.closing = async app => {
  const d = await api('/api/period');
  app.innerHTML = `<div class="grid g2">
    <div class="card"><h3><i class="i i-lock"></i> قفل القيود</h3>
      <p class="small muted">أي فاتورة أو قيد أو سند بتاريخ <b>يسبق أو يساوي</b> هذا التاريخ لا يمكن إضافته أو تعديله أو حذفه. استخدمه بعد مراجعة كل شهر أو بعد تسليم الميزانية.</p>
      <label class="f" style="max-width:220px">القيود مقفلة حتى تاريخ<input type="date" id="cl-lock" value="${d.lock_before_date}"></label>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="cl-save">حفظ تاريخ القفل</button>
        ${d.lock_before_date ? '<button class="btn" id="cl-clear">إلغاء القفل</button>' : ''}</div>
      ${d.lock_before_date ? `<p class="small pos"><i class="i i-check"></i> القيود مقفلة حالياً حتى ${d.lock_before_date}</p>` : '<p class="small muted">لا يوجد قفل حالياً — كل الفترات قابلة للتعديل.</p>'}</div>
    <div class="card"><h3><i class="i i-calendar"></i> الإقفال السنوي</h3>
      <p class="small muted">يُرحّل أرصدة الإيرادات والمصروفات إلى حساب الأرباح المحتجزة (تصبح أصفاراً لتبدأ سنة جديدة)، ثم يقفل القيود حتى تاريخ الإقفال.</p>
      <div class="form-grid"><label class="f">نهاية السنة المالية<input type="date" id="cl-date" value="${today().slice(0, 4)}-12-31"></label>
        <label class="f">حساب الأرباح المحتجزة<select id="cl-acc">${d.equity_accounts.map(a => `<option value="${a.id}" ${a.code === '32' ? 'selected' : ''}>${esc(a.code)} — ${esc(a.name)}</option>`).join('')}</select></label></div>
      ${d.last_closing_date ? `<p class="small">آخر إقفال: <b>${d.last_closing_date}</b> — سيُقفل ما بعده فقط.</p>` : ''}
      <div class="row" style="margin-top:12px"><button class="btn primary" id="cl-close">تنفيذ الإقفال السنوي</button></div></div>
    <div class="card" style="grid-column:1/-1"><h3><i class="i i-list"></i> قيود الإقفال السابقة</h3>
      ${d.closings.length ? `<table><thead><tr><th>رقم القيد</th><th>التاريخ</th><th>البيان</th><th>المبلغ</th></tr></thead><tbody>
        ${d.closings.map(j => `<tr class="clickable" data-j="${j.id}"><td>${j.number}</td><td>${j.date}</td><td>${esc(j.description)}</td><td>${money(j.amount)}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">لم يُنفَّذ إقفال سنوي بعد</div>'}</div></div>`;
  $('#cl-save').onclick = e => run(e.target, async () => {
    await post('/api/period/lock', { lock_before_date: $('#cl-lock').value });
    toast('حُفظ تاريخ القفل'); route();
  });
  if ($('#cl-clear')) $('#cl-clear').onclick = e => run(e.target, async () => {
    await post('/api/period/lock', { lock_before_date: '' }); toast('أُلغي القفل'); route();
  });
  $('#cl-close').onclick = async e => {
    if (!await confirmBox(`سيُرحَّل صافي نتيجة الفترة إلى الأرباح المحتجزة وتُقفل القيود حتى ${$('#cl-date').value}. متابعة؟`)) return;
    run(e.target, async () => {
      const r = await post('/api/period/close-year', { date_to: $('#cl-date').value, retained_account_id: +$('#cl-acc').value });
      toast(`تم الإقفال — نتيجة الفترة ${fmt(r.net_profit)}`);
      route();
    });
  };
  $$('[data-j]', app).forEach(tr => tr.onclick = () => showJournal(+tr.dataset.j, route));
};

// ═════════════════════ سجل التدقيق ═════════════════════
const AUDIT_ENTITY = { invoice: 'فاتورة', journal: 'قيد', voucher: 'سند', transfer: 'تحويل', item: 'مادة', party: 'عميل/مورد',
  account: 'حساب', user: 'مستخدم', warehouse: 'مستودع', currency: 'عملة', opening: 'أرصدة افتتاحية',
  opening_stock: 'بضاعة أول المدة', period: 'إقفال', backup: 'نسخة احتياطية' };
const AUDIT_ACTION = { create: 'إضافة', update: 'تعديل', delete: 'حذف', login: 'دخول', login_failed: 'محاولة فاشلة', close: 'إقفال' };

PAGES.audit = async app => {
  app.innerHTML = `<div class="card no-print"><div class="row">
      <label class="row small">من <input type="date" id="a-from"></label>
      <label class="row small">إلى <input type="date" id="a-to" value="${today()}"></label>
      <select id="a-entity"><option value="">كل الأنواع</option>${Object.entries(AUDIT_ENTITY).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
      <select id="a-user"><option value="">كل المستخدمين</option></select>
      <input id="a-search" placeholder="بحث في الوصف…" style="width:200px">
      <button class="btn primary" id="a-go">عرض</button><span class="sp"></span><button class="btn" id="a-print"><i class="i i-print"></i> طباعة</button></div></div>
    <div class="card" id="a-out"></div>`;
  let alimit = 300;
  const load = async () => {
    const q = new URLSearchParams({ date_from: $('#a-from').value, date_to: $('#a-to').value,
      entity: $('#a-entity').value, user_id: $('#a-user').value, search: $('#a-search').value });
    q.set('limit', alimit);
    [...q.keys()].forEach(k => !q.get(k) && q.delete(k));
    const { data: d, total: atotal } = await api('/api/audit?' + q, { withTotal: true });
    if ($('#a-user').options.length === 1)
      $('#a-user').insertAdjacentHTML('beforeend', d.users.map(u => `<option value="${u.id}">${esc(u.full_name)}</option>`).join(''));
    $('#a-out').innerHTML = d.rows.length ? `<div class="table-wrap"><table><thead><tr><th>التاريخ والوقت</th><th>المستخدم</th><th>العملية</th><th>النوع</th><th>الوصف</th><th></th></tr></thead><tbody>
      ${d.rows.map(r => `<tr><td class="small num">${r.at}</td><td>${esc(r.user_name || '—')}</td>
        <td><span class="badge ${r.action === 'delete' || r.action === 'login_failed' ? 'low' : r.action === 'create' ? 'sale' : ''}">${AUDIT_ACTION[r.action] || r.action}</span></td>
        <td class="small muted">${AUDIT_ENTITY[r.entity] || r.entity}</td><td>${esc(r.summary)}</td>
        <td class="act">${r.details ? `<button class="btn sm" data-d='${esc(r.details).replace(/'/g, "&#39;")}'>التفاصيل</button>` : ''}</td></tr>`).join('')}
      </tbody></table></div>${moreBar(d.rows.length, atotal)}<p class="small muted">السجل لا يُحذف ولا يُعدَّل.</p>` : '<div class="empty">لا توجد عمليات مطابقة</div>';
    if ($('#a-out [data-more]')) $('#a-out [data-more]').onclick = () => { alimit += 700; load(); };
    $$('[data-d]', $('#a-out')).forEach(b => b.onclick = () => {
      let pretty;
      try { pretty = JSON.stringify(JSON.parse(b.dataset.d), null, 2); } catch { pretty = b.dataset.d; }
      modal('تفاصيل العملية', `<pre class="small" style="white-space:pre-wrap;direction:ltr;text-align:left;background:var(--panel-2);padding:12px;border-radius:8px">${esc(pretty)}</pre>
        <div class="foot"><button class="btn" data-close>إغلاق</button></div>`, { wide: true });
    });
  };
  $('#a-go').onclick = load;
  $('#a-entity').onchange = load;
  $('#a-user').onchange = load;
  $('#a-print').onclick = () => printHTML('سجل التدقيق', $('#a-out').innerHTML);
  await load();
};

// ═════════════════════ المستخدمون ═════════════════════
PAGES.users = async app => {
  const d = await api('/api/users');
  app.innerHTML = `<div class="card"><h3><i class="i i-users"></i> المستخدمون<span class="sp"></span><button class="btn primary" id="u-new"><i class="i i-plus"></i> مستخدم جديد</button></h3>
    <table><thead><tr><th>الاسم</th><th>اسم المستخدم</th><th>الدور</th><th>الصلاحيات</th><th>الحالة</th><th></th></tr></thead><tbody>
    ${d.users.map(u => `<tr><td>${esc(u.full_name)}</td><td class="muted">${esc(u.username)}</td><td>${esc(u.role_label)}</td>
      <td class="small">${u.role === 'admin' ? 'كل الصلاحيات' : u.permissions.map(p => esc(d.permissions[p])).join('، ') || '—'}</td>
      <td>${u.active ? '<span class="badge sale">فعّال</span>' : '<span class="badge low">موقوف</span>'}
        ${u.locked ? '<span class="badge low">مقفل مؤقتاً</span>' : ''}</td>
      <td class="act"><button class="btn sm" data-ed="${u.id}"><i class="i i-edit"></i></button>${u.id !== S.settings.user.id ? `<button class="btn sm danger" data-del="${u.id}"><i class="i i-trash"></i></button>` : ''}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="card small muted">الأدوار الجاهزة: <b>مدير</b> (كل شيء)، <b>محاسب</b> (كل شيء عدا الإعدادات والمستخدمين)، <b>كاشير</b> (المبيعات والسندات والعملاء)، <b>أمين مستودع</b> (المشتريات والمواد والمستودعات). يمكنك تعديل صلاحيات أي مستخدم بشكل مخصص.</div>`;
  const form = u => {
    const m = modal(u ? 'تعديل مستخدم' : 'مستخدم جديد', `<div class="form-grid">
        <label class="f">الاسم الكامل<input id="u-full" value="${esc(u?.full_name)}"></label>
        <label class="f">اسم المستخدم<input id="u-name" value="${esc(u?.username)}" autocomplete="off"></label>
        <label class="f">كلمة المرور ${u ? '(اتركها فارغة لعدم التغيير)' : ''}<input id="u-pass" type="password" autocomplete="new-password"></label>
        <label class="f">الدور<select id="u-role">${Object.entries(d.roles).map(([k, r]) => `<option value="${k}" ${k === (u?.role || 'cashier') ? 'selected' : ''}>${esc(r.label)}</option>`).join('')}</select></label></div>
      <h3 style="margin-top:14px">الصلاحيات</h3><div class="perm-grid" id="u-perms">${Object.entries(d.permissions).map(([k, l]) => `<label class="row small"><input type="checkbox" value="${k}"> ${esc(l)}</label>`).join('')}</div>
      ${u ? `<label class="row small" style="margin-top:10px"><input type="checkbox" id="u-active" ${u.active ? 'checked' : ''}> الحساب فعّال</label>` : ''}
      ${u && u.locked ? `<label class="row small"><input type="checkbox" id="u-unlock" checked> فكّ القفل المؤقت (بعد محاولات دخول فاشلة)</label>` : ''}
      <div class="foot"><button class="btn primary" id="u-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
    const setPerms = list => $$('#u-perms input').forEach(c => { c.checked = list.includes(c.value); c.disabled = $('#u-role').value === 'admin'; });
    setPerms(u ? u.permissions : d.roles[$('#u-role').value].perms);
    $('#u-role').onchange = () => setPerms(d.roles[$('#u-role').value].perms);
    $('#u-save').onclick = e => run(e.target, async () => {
      const body = { full_name: $('#u-full').value, username: $('#u-name').value, password: $('#u-pass').value || undefined, role: $('#u-role').value,
        permissions: $$('#u-perms input:checked').map(c => c.value), active: u ? $('#u-active').checked : true,
        unlock: $('#u-unlock') ? $('#u-unlock').checked : false };
      u ? await put(`/api/users/${u.id}`, body) : await post('/api/users', body);
      m.close(); toast('تم الحفظ'); route();
    });
  };
  $('#u-new').onclick = () => form(null);
  app.querySelector('tbody').onclick = async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.ed) form(d.users.find(u => u.id == b.dataset.ed));
    if (b.dataset.del && await confirmBox('حذف هذا المستخدم؟')) run(b, async () => { await del(`/api/users/${b.dataset.del}`); toast('حُذف المستخدم'); route(); });
  };
};

// ═════════════════════ الإعدادات ═════════════════════
PAGES.settings = async app => {
  await loadSettings();
  const s = S.settings;
  const f = (k, l) => `<label class="f">${l}<input id="s-${k}" value="${esc(s[k])}"></label>`;
  app.innerHTML = `<div class="grid g2">
    <div class="card"><h3><i class="i i-building"></i> بيانات المنشأة</h3><div class="form-grid">${f('company_name', 'اسم الشركة')}${f('tax_number', 'الرقم الضريبي')}${f('phone', 'الهاتف')}${f('address', 'العنوان')}</div></div>
    <div class="card"><h3><i class="i i-receipt"></i> ضريبة القيمة المضافة</h3>
      <label class="row"><input type="checkbox" id="s-vat" ${s.vat_enabled ? 'checked' : ''}> تفعيل ضريبة القيمة المضافة على الفواتير</label>
      <label class="f" style="margin-top:10px;max-width:200px">النسبة الافتراضية %<input type="number" id="s-vat-rate" min="0" max="100" step="any" value="${s.vat_rate}"></label>
      <p class="small muted">تُضاف الضريبة فوق سعر الفاتورة ويمكن تعديل نسبتها في كل فاتورة. تُرحَّل ضريبة المبيعات إلى حـ/ 24 وضريبة المشتريات إلى حـ/ 115، ويظهر الإقرار في التقارير.</p></div>
    <div class="card" style="grid-column:1/-1"><h3><i class="i i-transfer"></i> العملات وأسعار الصرف<span class="sp"></span><button class="btn sm" id="c-new"><i class="i i-plus"></i> عملة</button></h3>
      <table><thead><tr><th>الرمز</th><th>الاسم</th><th>الإشارة</th><th>سعر الصرف (قيمة الوحدة بالعملة الأساسية)</th><th></th></tr></thead><tbody id="c-list">
      ${s.currencies.map(c => `<tr><td>${esc(c.code)} ${c.is_base ? '<span class="badge sale">الأساسية</span>' : ''}</td><td>${esc(c.name)}</td><td>${esc(c.symbol)}</td><td>${c.is_base ? '1' : fmt(c.rate)}</td>
        <td class="act"><button class="btn sm" data-ed="${c.code}"><i class="i i-edit"></i></button>${c.is_base ? '' : `<button class="btn sm danger" data-del="${c.code}"><i class="i i-trash"></i></button>`}</td></tr>`).join('')}</tbody></table>
      <p class="small muted">كل القيود والتقارير بالعملة الأساسية. الفواتير والسندات بعملة أخرى تُحوَّل بسعر الصرف وقت الحركة (قابل للتعديل في كل حركة)، ويُحفظ المبلغ الأصلي ليظهر في كشف الحساب. لصندوق بعملة أجنبية أنشئ حساباً نقدياً مثل «صندوق الدولار» من دليل الحسابات.</p></div>
    <div class="card"><h3><i class="i i-cart"></i> نقطة البيع</h3>
      <div class="form-grid"><label class="f">بادئة باركود الموازين<input id="s-scale-prefix" value="${esc(s.scale_prefix || '')}"></label>
        <label class="f">الرقم المدمج في الباركود<select id="s-scale-mode">
          <option value="weight" ${s.scale_mode !== 'price' ? 'selected' : ''}>وزن بالغرام</option>
          <option value="price" ${s.scale_mode === 'price' ? 'selected' : ''}>مبلغ</option></select></label></div>
      <label class="row small" style="margin-top:10px"><input type="checkbox" id="s-pos-print" ${s.pos_print === '1' ? 'checked' : ''}> طباعة الإيصال تلقائياً بعد كل عملية بيع</label>
      <label class="f" style="margin-top:10px;max-width:220px">تنبيه انتهاء الصلاحية قبل (أيام)<input type="number" id="s-expiry-days" min="1" value="${s.expiry_alert_days || 30}"></label>
      <p class="small muted">ملصق الميزان عادةً 13 رقماً يبدأ بالبادئة، ثم رمز المادة، ثم الوزن أو المبلغ. درج النقود يُفتح مع الإيصال إذا كان موصولاً بالطابعة.</p></div>
    <div class="card" style="grid-column:1/-1"><h3><i class="i i-print"></i> الطابعات والأجهزة</h3>
      <div class="form-grid">
        <label class="f">طريقة الطباعة على <b>هذا الجهاز</b><select id="d-mode">
          <option value="browser">حوار طباعة المتصفح</option>
          <option value="server">مباشرة من الخادم (طابعة شبكة أو موصولة بالخادم)</option>
          <option value="agent">وكيل الطباعة المحلي (طابعة USB على هذا الجهاز)</option></select></label>
        <label class="f" id="d-agent-wrap">عنوان الوكيل<input id="d-agent" dir="ltr"></label>
        <label class="f">عرض الورق<select id="d-width"><option value="80">80 ملم</option><option value="58">58 ملم</option></select></label>
        <label class="f">تذييل الإيصال<input id="d-footer" placeholder="شكراً لزيارتكم"></label>
      </div>
      <div class="grid g2" style="margin-top:12px">
        <div><b class="small">طابعة الإيصالات (الكاشير)</b>
          <div class="form-grid" style="margin-top:6px">
            <label class="f">النوع<select id="d-pmode"><option value="">غير مضبوطة</option><option value="network">شبكة (IP)</option><option value="windows">طابعة ويندوز (USB)</option></select></label>
            <label class="f d-p-net">العنوان IP<input id="d-phost" dir="ltr" placeholder="192.168.1.50"></label>
            <label class="f d-p-net">المنفذ<input id="d-pport" dir="ltr" placeholder="9100"></label>
            <label class="f d-p-win">الطابعة<select id="d-pname"></select></label></div>
          <div class="row" style="margin-top:8px"><button class="btn sm" id="d-ptest">طباعة صفحة اختبار</button>
            <button class="btn sm" id="d-drawer">فتح الدرج</button></div></div>
        <div><b class="small">طابعة المطبخ (اختياري)</b>
          <div class="form-grid" style="margin-top:6px">
            <label class="f">النوع<select id="d-kmode"><option value="">نفس طابعة الكاشير</option><option value="network">شبكة (IP)</option><option value="windows">طابعة ويندوز (USB)</option></select></label>
            <label class="f d-k-net">العنوان IP<input id="d-khost" dir="ltr"></label>
            <label class="f d-k-net">المنفذ<input id="d-kport" dir="ltr" placeholder="9100"></label>
            <label class="f d-k-win">الطابعة<select id="d-kname"></select></label></div>
          <div class="row" style="margin-top:8px"><button class="btn sm" id="d-ktest">اختبار طابعة المطبخ</button></div></div>
      </div>
      <div class="form-grid" style="margin-top:12px">
        <label class="f">منفذ الميزان (مثل COM3)<input id="d-scale" dir="ltr"></label>
        <label class="f">سرعة الميزان<input id="d-baud" dir="ltr" placeholder="9600"></label></div>
      <label class="row small" style="margin-top:10px"><input type="checkbox" id="d-cash-drawer"> فتح درج النقود تلقائياً عند البيع النقدي</label>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="d-save"><i class="i i-save"></i> حفظ إعدادات الأجهزة</button>
        <span class="small muted" id="d-status"></span></div>
      <p class="small muted">الإيصال يُرسم كصورة فيخرج النص العربي صحيحاً على أي طابعة ESC/POS. إعدادات الطابعة أعلاه تخص الطباعة «من الخادم»؛
        في وضع «الوكيل المحلي» تُضبط الطابعة في ملف <code>agent.json</code> على جهاز الكاشير ويُشغَّل <code>run_agent.bat</code>.</p></div>
    <div class="card"><h3>الذكاء الاصطناعي</h3>
      <p class="small">الحالة: ${s.ai_ready ? '<span class="badge sale">مُفعّل</span>' : '<span class="badge low">غير مُفعّل</span>'} — النموذج: <code>${esc(s.ai_model)}</code></p>
      <label class="f">مفتاح Anthropic API<input id="s-key" type="password" autocomplete="off" placeholder="${s.ai_ready ? '•••••••• (محفوظ — اكتب مفتاحاً جديداً للتغيير)' : 'sk-ant-…'}"></label>
      <p class="small muted">احصل على المفتاح من console.anthropic.com. يُحفظ محلياً على جهازك فقط.</p></div>
    <div class="card" style="grid-column:1/-1"><h3><i class="i i-pulse"></i> فحص سلامة النظام<span class="sp"></span><button class="btn sm primary" id="hc-run">افحص الآن</button></h3>
      <div id="hc-out" class="small muted">يتحقق من توازن القيود، تطابق المخزون مع الحسابات، حداثة النسخ الاحتياطي، والتجهيزات.</div></div>
    <div class="card" style="grid-column:1/-1"><h3><i class="i i-save"></i> النسخ الاحتياطي والاستعادة</h3>
      <p class="small muted">نسخة تلقائية يومياً (آخر ١٤ نسخة) في مجلد <code>data/backups</code>، مع فحص سلامة كل نسخة.</p>
      <label class="f" style="max-width:520px">مجلد إضافي للنسخ (فلاشة، قرص آخر، أو مجلد OneDrive/Google Drive)
        <input id="s-bkdir" dir="ltr" value="${esc(s.backup_extra_dir || '')}" placeholder="E:\\AxisBackup"></label>
      <p class="small muted">ضبط هذا المجلد يحميك من ضياع البيانات إن تعطّل قرص الجهاز. يُحفظ مع «حفظ الإعدادات» أسفل الصفحة.</p>
      <div class="row"><a class="btn" href="/api/backup" download><i class="i i-download"></i> تنزيل نسخة الآن</a><button class="btn" id="bk-now"><i class="i i-box"></i> إنشاء نسخة محفوظة</button>
        <button class="btn" id="bk-upload"><i class="i i-upload"></i> استعادة من ملف…</button><input type="file" id="bk-file" accept=".db" hidden></div>
      <div id="bk-list" style="margin-top:10px"></div></div>
    <div class="card" style="grid-column:1/-1"><h3><i class="i i-file"></i> سجل الأخطاء<span class="sp"></span><button class="btn sm" id="lg-load">عرض آخر السجلات</button></h3>
      <pre id="lg-out" class="small muted" style="white-space:pre-wrap;direction:ltr;text-align:left;max-height:260px;overflow:auto;margin:0">يُسجَّل هنا كل خطأ غير متوقع برقم مرجعي يظهر للمستخدم.</pre></div>
  </div>
  <div class="row" style="margin-top:16px"><button class="btn primary" id="s-save"><i class="i i-save"></i> حفظ الإعدادات</button></div>`;
  $('#s-save').onclick = e => run(e.target, async () => {
    const body = { api_key: $('#s-key').value, vat_enabled: $('#s-vat').checked, vat_rate: +$('#s-vat-rate').value || 0,
      scale_prefix: $('#s-scale-prefix').value.trim(), scale_mode: $('#s-scale-mode').value,
      pos_print: $('#s-pos-print').checked ? '1' : '0', expiry_alert_days: +$('#s-expiry-days').value || 30,
      backup_extra_dir: $('#s-bkdir').value.trim() };
    ['company_name', 'tax_number', 'phone', 'address'].forEach(k => body[k] = $(`#s-${k}`).value);
    await put('/api/settings', body);
    toast('حُفظت الإعدادات');
    route();
  });
  const curForm = c => {
    const m = modal(c ? `تعديل ${esc(c.name)}` : 'عملة جديدة', `<div class="form-grid">
        <label class="f">الرمز (مثل USD)<input id="c-code" value="${esc(c?.code)}" ${c ? 'disabled' : ''}></label>
        <label class="f">الاسم<input id="c-name" value="${esc(c?.name)}"></label>
        <label class="f">الإشارة<input id="c-sym" value="${esc(c?.symbol)}"></label>
        ${c?.is_base ? '' : `<label class="f">سعر الصرف: 1 وحدة = كم ${esc(baseCur().symbol)}؟<input type="number" step="any" min="0" id="c-rate" value="${c?.rate ?? ''}"></label>`}</div>
      <div class="foot"><button class="btn primary" id="c-save">حفظ</button><button class="btn" data-close>إلغاء</button></div>`);
    $('#c-save').onclick = e => run(e.target, async () => {
      const body = { code: $('#c-code').value, name: $('#c-name').value, symbol: $('#c-sym').value, rate: +($('#c-rate')?.value || 1) };
      c ? await put(`/api/currencies/${c.code}`, body) : await post('/api/currencies', body);
      m.close(); toast('تم الحفظ'); route();
    });
  };
  // ── الأجهزة ──
  const dev = await api('/api/devices');
  const printerOpts = (sel, names = []) => '<option value="">— اختر —</option>'
    + [...new Set([sel, ...names].filter(Boolean))].map(n => `<option ${n === sel ? 'selected' : ''}>${esc(n)}</option>`).join('');
  // قائمة طابعات ويندوز تأتي لاحقاً (استعلامها بطيء) دون أن تعطّل بقية الصفحة
  api('/api/devices/printers').then(names => {
    if (!$('#d-pname')) return;
    const p1 = $('#d-pname').value || dev.printer_name, k1 = $('#d-kname').value || dev.kitchen_name;
    $('#d-pname').innerHTML = printerOpts(p1, names); $('#d-kname').innerHTML = printerOpts(k1, names);
  }).catch(() => {});
  $('#d-mode').value = DEV.mode; $('#d-agent').value = DEV.agent;
  $('#d-width').value = dev.printer_width || '80'; $('#d-footer').value = dev.receipt_footer || '';
  $('#d-pmode').value = dev.printer_mode || ''; $('#d-phost').value = dev.printer_host || ''; $('#d-pport').value = dev.printer_port || '';
  $('#d-kmode').value = dev.kitchen_mode || ''; $('#d-khost').value = dev.kitchen_host || ''; $('#d-kport').value = dev.kitchen_port || '';
  $('#d-pname').innerHTML = printerOpts(dev.printer_name); $('#d-kname').innerHTML = printerOpts(dev.kitchen_name);
  $('#d-scale').value = dev.scale_port || ''; $('#d-baud').value = dev.scale_baud || '';
  $('#d-cash-drawer').checked = dev.drawer_on_cash !== '0';
  const syncDev = () => {
    $('#d-agent-wrap').style.display = $('#d-mode').value === 'agent' ? '' : 'none';
    for (const [k, sel] of [['p', '#d-pmode'], ['k', '#d-kmode']]) {
      $$(`.d-${k}-net`).forEach(e => e.style.display = $(sel).value === 'network' ? '' : 'none');
      $$(`.d-${k}-win`).forEach(e => e.style.display = $(sel).value === 'windows' ? '' : 'none');
    }
  };
  ['#d-mode', '#d-pmode', '#d-kmode'].forEach(x => $(x).onchange = syncDev);
  syncDev();
  const saveDev = async () => {
    DEV.mode = $('#d-mode').value; DEV.agent = $('#d-agent').value.trim() || 'http://127.0.0.1:9777';
    await put('/api/devices', { printer_width: $('#d-width').value, receipt_footer: $('#d-footer').value,
      printer_mode: $('#d-pmode').value, printer_host: $('#d-phost').value.trim(), printer_port: $('#d-pport').value.trim(), printer_name: $('#d-pname').value,
      kitchen_mode: $('#d-kmode').value, kitchen_host: $('#d-khost').value.trim(), kitchen_port: $('#d-kport').value.trim(), kitchen_name: $('#d-kname').value,
      scale_port: $('#d-scale').value.trim(), scale_baud: $('#d-baud').value.trim(), drawer_on_cash: $('#d-cash-drawer').checked ? '1' : '0' });
  };
  $('#d-save').onclick = e => run(e.target, async () => { await saveDev(); toast('حُفظت إعدادات الأجهزة'); });
  $('#d-ptest').onclick = e => run(e.target, async () => { await saveDev(); await post('/api/devices/test', { which: 'printer' }); toast('أُرسلت صفحة الاختبار'); });
  $('#d-ktest').onclick = e => run(e.target, async () => { await saveDev(); await post('/api/devices/test', { which: 'kitchen' }); toast('أُرسلت لطابعة المطبخ'); });
  $('#d-drawer').onclick = e => run(e.target, async () => { await saveDev(); await openDrawer(); toast('فُتح الدرج'); });
  if (DEV.mode === 'agent') agentCall('/status').then(st => { $('#d-status').textContent = `الوكيل يعمل — طابعته: ${st.printer.name || st.printer.host || 'غير مضبوطة'}`; })
    .catch(() => { $('#d-status').textContent = 'الوكيل لا يعمل على هذا الجهاز'; });

  const afterRestore = info => {
    modal('تمت الاستعادة', `<p>استُعيدت البيانات: ${info.invoices} فاتورة و${info.journals} قيد${info.last_date ? `، آخر حركة ${info.last_date}` : ''}.</p>
      <p class="small muted">حُفظت حالة ما قبل الاستعادة في النسخة <code>${esc(info.safety_backup)}</code> إن احتجت التراجع.</p>
      <div class="foot"><button class="btn primary" onclick="location.reload()">إعادة تحميل البرنامج</button></div>`);
  };
  const loadBackups = async () => {
    const list = await api('/api/backups');
    $('#bk-list').innerHTML = list.length ? `<div class="table-wrap" style="max-height:220px;overflow-y:auto"><table class="small"><thead><tr><th>النسخة</th><th>التاريخ</th><th>الحجم</th><th></th></tr></thead><tbody>
      ${list.map(b => `<tr><td dir="ltr" style="text-align:right">${esc(b.name)}</td><td class="num">${b.at}</td><td class="num">${fmt(b.size / 1024)} ك.ب</td>
        <td class="act"><button class="btn sm" data-restore="${esc(b.name)}">استعادة</button></td></tr>`).join('')}</tbody></table></div>`
      : '<span class="small muted">لا توجد نسخ بعد</span>';
    $$('[data-restore]', $('#bk-list')).forEach(b => b.onclick = async () => {
      if (!await confirmBox(`استعادة النسخة «${b.dataset.restore}»؟ ستُستبدل كل البيانات الحالية بها (مع حفظ نسخة أمان من الحالة الحالية).`)) return;
      run(b, async () => afterRestore(await post('/api/backups/restore-local', { name: b.dataset.restore })));
    });
  };
  $('#bk-now').onclick = e => run(e.target, async () => {
    const r = await post('/api/backups');
    toast(r.offsite_error ? `حُفظت محلياً، لكن ${r.offsite_error}` : `${r.name}`, !!r.offsite_error);
    loadBackups();
  });
  $('#bk-upload').onclick = () => $('#bk-file').click();
  $('#bk-file').onchange = async () => {
    const f = $('#bk-file').files[0];
    if (!f || !await confirmBox(`استعادة البيانات من الملف «${f.name}»؟ ستُستبدل كل البيانات الحالية.`)) return;
    const fd = new FormData(); fd.append('file', f);
    run($('#bk-upload'), async () => afterRestore(await api('/api/backups/restore-upload', { method: 'POST', body: fd })));
  };
  $('#hc-run').onclick = e => run(e.target, async () => {
    const h = await api('/api/health');
    $('#hc-out').innerHTML = `<p class="${h.ok ? 'pos' : 'neg'}" style="font-size:15px"><b>${h.ok ? 'النظام سليم' : `${h.failed} بند يحتاج انتباهك`}</b></p>
      <table><tbody>${h.checks.map(c => `<tr><td style="width:30px">${c.ok ? '<i class="i i-ok"></i>' : c.optional ? '<i class="i i-minus"></i>' : '<i class="i i-no"></i>'}</td><td>${esc(c.name)}</td>
        <td class="${c.ok || c.optional ? 'muted' : 'neg'}">${esc(c.detail || '')}</td></tr>`).join('')}</tbody></table>`;
  });
  $('#lg-load').onclick = e => run(e.target, async () => {
    const r = await api('/api/logs?lines=150');
    $('#lg-out').textContent = r.lines.length ? r.lines.join('\n') : 'لا توجد سجلات.';
    $('#lg-out').scrollTop = $('#lg-out').scrollHeight;
  });
  loadBackups();
  $('#c-new').onclick = () => curForm(null);
  $('#c-list').onclick = async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.ed) curForm(s.currencies.find(c => c.code === b.dataset.ed));
    if (b.dataset.del && await confirmBox('حذف هذه العملة؟')) run(b, async () => { await del(`/api/currencies/${b.dataset.del}`); route(); });
  };
};

// ═════════════════════ التشغيل ═════════════════════
(function theme() {
  const get = () => { try { return localStorage.getItem('theme'); } catch { return null; } };
  const apply = t => { if (t) document.documentElement.setAttribute('data-theme', t); else document.documentElement.removeAttribute('data-theme'); };
  apply(get());
  $('#theme-btn').onclick = () => {
    const dark = document.documentElement.getAttribute('data-theme') === 'dark' || (!get() && matchMedia('(prefers-color-scheme: dark)').matches);
    const t = dark ? 'light' : 'dark';
    apply(t);
    try { localStorage.setItem('theme', t); } catch { }
  };
})();
$('#user-btn').onclick = userMenu;

/** يكتشف أن البرنامج حُدِّث بعد تحميل هذه الصفحة فيعيد تحميلها مرة واحدة (تبويب بقي مفتوحاً منذ ما قبل التحديث) */
const LOADED_VERSION = new URL([...document.scripts].find(x => x.src.includes('app.js')).src).searchParams.get('v');
async function checkVersion() {
  try {
    const { version } = await (await fetch('/api/version', { cache: 'no-store' })).json();
    let tried = null; try { tried = sessionStorage.getItem('reloadedFor'); } catch { }
    if (version && LOADED_VERSION !== version && tried !== version) {
      try { sessionStorage.setItem('reloadedFor', version); } catch { }
      location.reload();
    }
  } catch { }
}
async function start() {
  try { await loadSettings(); } catch { return; }
  route();
}
checkVersion();
setInterval(() => { if (!document.querySelector('.modal-bg')) checkVersion(); }, 300000);
showAuth();
