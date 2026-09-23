// ============ FLASH → ТОСТЫ ============
document.addEventListener('DOMContentLoaded', () => {
    const flash = document.getElementById('flash-data');
    if (flash) {
        flash.querySelectorAll('span').forEach(el => {
            showToast(el.dataset.msg, el.dataset.cat);
        });
    }
});

function showToast(msg, category = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const t = document.createElement('div');
    t.className = 'toast-msg ' + category;
    t.textContent = msg;
    container.appendChild(t);
    setTimeout(() => {
        t.style.transition = 'opacity .3s, transform .3s';
        t.style.opacity = '0';
        t.style.transform = 'translateX(40px)';
        setTimeout(() => t.remove(), 300);
    }, 3500);
}

// ============ SCROLL PROGRESS ============
const progressBar = document.getElementById('scroll-progress');
if (progressBar) {
    window.addEventListener('scroll', () => {
        const h = document.documentElement;
        const scrolled = (h.scrollTop / (h.scrollHeight - h.clientHeight)) * 100;
        progressBar.style.width = scrolled + '%';
    });
}

// ============ КНОПКА НАВЕРХ ============
const toTop = document.getElementById('to-top');
if (toTop) {
    window.addEventListener('scroll', () => {
        toTop.classList.toggle('visible', window.scrollY > 400);
    });
    toTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
}

// ============ АНИМАЦИЯ ПОЯВЛЕНИЯ ============
const observer = new IntersectionObserver((entries) => {
    entries.forEach((e, i) => {
        if (e.isIntersecting) {
            setTimeout(() => e.target.classList.add('visible'), i * 40);
            observer.unobserve(e.target);
        }
    });
}, { threshold: 0.1 });

document.querySelectorAll('.reveal').forEach(el => observer.observe(el));

// ============ ЖИВОЙ ПОИСК ============
const searchInput = document.getElementById('live-search');
const searchResults = document.getElementById('search-results');

if (searchInput && searchResults) {
    let timer = null;
    searchInput.addEventListener('input', () => {
        clearTimeout(timer);
        const q = searchInput.value.trim();
        if (q.length < 2) {
            searchResults.classList.remove('open');
            return;
        }
        timer = setTimeout(async () => {
            const r = await fetch('/api/search?q=' + encodeURIComponent(q));
            const data = await r.json();
            if (!data.items.length) {
                searchResults.innerHTML = '<div style="padding:16px;color:#999;">Ничего не найдено</div>';
                searchResults.classList.add('open');
                return;
            }
            searchResults.innerHTML = data.items.map(p => `
                <a class="search-item" href="/product/${p.id}">
                    ${p.image
                        ? `<img src="${p.image}" alt="">`
                        : `<div class="search-thumb">☕</div>`}
                    <div>
                        <div style="font-weight:600;">${p.name}</div>
                        <div style="font-size:.85rem;color:#7a8570;">${p.category} · ${p.price}</div>
                    </div>
                </a>
            `).join('');
            searchResults.classList.add('open');
        }, 200);
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('.search-wrapper')) {
            searchResults.classList.remove('open');
        }
    });
}

// ============ БЫСТРЫЙ ПРОСМОТР ============
function openQuickView(id) {
    fetch('/product/' + id)
        .then(r => r.text())
        .then(html => {
            const doc = new DOMParser().parseFromString(html, 'text/html');
            const main = doc.querySelector('main');
            let contentHTML = '';

            if (main) {
                const clone = main.cloneNode(true);
                clone.querySelectorAll('.breadcrumb').forEach(el => el.remove());
                clone.querySelectorAll('h2.category-title').forEach(el => {
                    const next = el.nextElementSibling;
                    if (next && next.classList.contains('row')) next.remove();
                    el.remove();
                });
                contentHTML = clone.innerHTML;
            }

            let modal = document.getElementById('quick-view');
            if (!modal) {
                modal = document.createElement('div');
                modal.id = 'quick-view';
                modal.className = 'modal-backdrop-custom';
                modal.innerHTML = `
                    <div class="modal-box">
                        <button class="modal-close" onclick="closeQuickView()">✕</button>
                        <div id="quick-view-content" style="padding:30px;"></div>
                    </div>`;
                document.body.appendChild(modal);
                modal.addEventListener('click', (e) => {
                    if (e.target === modal) closeQuickView();
                });
            }
            document.getElementById('quick-view-content').innerHTML = contentHTML;
            modal.classList.add('open');
            document.body.style.overflow = 'hidden';
        });
}

function closeQuickView() {
    const modal = document.getElementById('quick-view');
    if (modal) modal.classList.remove('open');
    document.body.style.overflow = '';
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeQuickView();
});

// ============ ТЁМНАЯ ТЕМА ============
const themeBtn = document.getElementById('theme-toggle');
const savedTheme = localStorage.getItem('theme') || 'light';

if (savedTheme === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
    if (themeBtn) themeBtn.textContent = '☀️';
}

if (themeBtn) {
    themeBtn.addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('theme', next);
        themeBtn.textContent = next === 'dark' ? '☀️' : '🌙';
    });
}

// ============ ИЗБРАННОЕ ============
async function toggleFav(btn) {
    const pid = btn.dataset.pid;
    const r = await fetch('/api/favorite/' + pid, { method: 'POST' });
    if (r.status === 401 || r.redirected) {
        window.location.href = '/login';
        return;
    }
    const data = await r.json();
    if (data.status === 'added') {
        btn.textContent = '❤️';
        btn.classList.add('active');
        showToast('Добавлено в избранное', 'success');
    } else {
        btn.textContent = '🤍';
        btn.classList.remove('active');
        showToast('Удалено из избранного', 'info');
    }
}

// ============ ИЗБРАННОЕ НА СТРАНИЦЕ ТОВАРА ============
async function toggleFavOnPage(btn) {
    if (btn.disabled) return;
    btn.disabled = true;
    const pid = btn.dataset.pid;
    try {
        const r = await fetch('/api/favorite/' + pid, { method: 'POST' });
        if (r.status === 401 || r.redirected) {
            window.location.href = '/login';
            return;
        }
        const data = await r.json();
        if (data.status === 'added') {
            btn.className = 'btn btn-outline-danger mt-3';
            btn.innerHTML = '❤️ Убрать из избранного';
            btn.dataset.state = 'in';
            showToast('Добавлено в избранное', 'success');
        } else {
            btn.className = 'btn btn-outline-green mt-3';
            btn.innerHTML = '🤍 В избранное';
            btn.dataset.state = 'out';
            showToast('Убрано из избранного', 'info');
        }
    } catch (e) {
        showToast('Ошибка связи', 'danger');
    } finally {
        btn.disabled = false;
    }
}