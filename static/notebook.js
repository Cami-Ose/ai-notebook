(function () {
    var data = null;         // 最近一次拿到的本子数据
    var cur = null;          // 前端正在看的页（0 基）
    var curBook = null;      // 正在看的本 id（null = 跟随当前写作本）
    var lastCurrent = null;  // 上一轮它的写作页
    var total = 0;
    var size = { w: 900, h: 600 };
    var el = document.getElementById('elements');
    var shelf = document.getElementById('shelf');
    var marks = document.getElementById('marks');

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function pageKey() {
        return 'notebook_page_' + ((data && data.book && data.book.id) || 'cur');
    }
    function savePage() { try { localStorage.setItem(pageKey(), String(cur)); } catch (e) {} }

    function fitSvg(box) {
        var sv = box.querySelector('svg');
        if (!sv) return;
        sv.removeAttribute('width');
        sv.removeAttribute('height');
        sv.setAttribute('preserveAspectRatio', 'xMidYMid meet');
        sv.style.width = '100%';
        sv.style.height = '100%';
        sv.style.display = 'block';
    }

    function fitPaper() {
        var paper = document.getElementById('paper');
        var wrap = document.getElementById('bookWrap');
        // 封面页：纸只剩右半边（左边那半整块不要），所以按半张纸算缩放
        var pw = (cur === 0) ? Math.round(size.w / 2) : size.w;
        paper.classList.toggle('cover-mode', cur === 0);
        paper.style.width = pw + 'px';
        paper.style.height = size.h + 'px';
        var scale = Math.min(1, (window.innerWidth - 24) / pw, (window.innerHeight - 300) / size.h);
        scale = Math.max(0.3, scale);
        paper.style.transform = 'scale(' + scale + ')';
        wrap.style.width = (pw * scale) + 'px';
        wrap.style.height = (size.h * scale) + 'px';
    }
    window.addEventListener('resize', fitPaper);

    function renderShelf() {
        var sh = (data && data.shelf) || [];
        shelf.innerHTML = '';
        sh.forEach(function (b) {
            var d = document.createElement('div');
            d.className = 'bk' + (data.book && b.id === data.book.id ? ' on' : '');
            var cov = document.createElement('div');
            cov.className = 'bk-cover';
            if (b.cover && b.cover.kind === 'svg') {
                cov.innerHTML = b.cover.data || '';
                fitSvg(cov);
            } else if (b.cover && b.cover.kind === 'image') {
                var im = document.createElement('img');
                im.src = b.cover.data || '';
                im.alt = '';
                im.onerror = function () { this.style.display = 'none'; };
                cov.appendChild(im);
            } else {
                cov.classList.add('bk-blank');
            }
            var t = document.createElement('div');
            t.className = 'bk-title';
            t.textContent = b.title || '';
            d.appendChild(cov);
            d.appendChild(t);
            if (b.closed_ts) {
                var a = document.createElement('div');
                a.className = 'bk-arch';
                a.textContent = '写满归档';
                d.appendChild(a);
            }
            d.title = (b.title || '') + ' · ' + b.page_count + '/' + b.pages_per_book + ' 页' + (b.current ? '（正在写这本）' : '');
            d.addEventListener('click', function () { switchBook(b.id); });
            shelf.appendChild(d);
        });
    }

    function renderMarks() {
        var bms = (data && data.bookmarks) || [];
        marks.innerHTML = '';
        bms.forEach(function (m) {
            var d = document.createElement('span');
            d.className = 'mk';
            d.title = '跳到第 ' + ((m.page || 0) + 1) + ' 页';
            var lab = document.createElement('span');
            lab.textContent = '🔖 第 ' + ((m.page || 0) + 1) + ' 页';
            var x = document.createElement('span');
            x.className = 'x';
            x.textContent = '×';
            x.title = '摘掉这个书签';
            d.appendChild(lab);
            d.appendChild(x);
            d.addEventListener('click', function () { gotoPage((m.page || 0) + 1); });
            x.addEventListener('click', function (ev) {
                ev.stopPropagation();
                sendBookmark({ action: 'remove', ref: String(m.id || '') });
            });
            marks.appendChild(d);
        });
    }

    function renderPage() {
        if (!data || data.error) {
            el.innerHTML = '<div class="empty err">服务没连上</div>';
            document.getElementById('pageIndicator').textContent = '第 - / - 页';
            return;
        }
        var pages = data.pages || [];
        total = pages.length;
        fitPaper();                                 // 封面页纸只剩右半边，缩放跟着变
        if (cur === 0) { renderCover(); return; }   // 第 0 页是封面页
        var page = pages[cur - 1] || null;
        if (!page) {
            el.innerHTML = '<div class="empty">这一页还空着</div>';
        } else {
            var items = page.elements || [];
            el.innerHTML = '';
            if (!items.length) {
                var e = document.createElement('div');
                e.className = 'empty';
                e.textContent = '这一页还空着';
                el.appendChild(e);
            }
            items.forEach(function (it) {
                var d = document.createElement('div');
                d.className = 'sn-el';
                d.style.left = (it.x || 0) + 'px';
                d.style.top = (it.y || 0) + 'px';
                if (it.rotation) d.style.transform = 'rotate(' + it.rotation + 'deg)';
                if (it.type === 'text') {
                    d.className += ' sn-text';
                    d.style.fontSize = (it.font_size || 17) + 'px';
                    d.style.color = it.color || '#3a2a1a';
                    d.textContent = it.text || '';
                    if (it.struck) d.classList.add('sn-struck');
                } else if (it.type === 'image') {
                    d.className += ' sn-img';
                    var img = document.createElement('img');
                    img.src = it.src || '';
                    img.width = it.w || 180;
                    img.height = it.h || 130;
                    img.style.objectFit = 'cover';
                    img.onerror = function () { this.style.opacity = 0.4; this.alt = '[图加载失败]'; };
                    d.appendChild(img);
                } else if (it.type === 'svg') {
                    d.className += ' sn-svg';
                    d.innerHTML = it.svg || '';
                    d.style.width = (it.w || 160) + 'px';
                    d.style.height = (it.h || 110) + 'px';
                    fitSvg(d);
                } else if (it.type === 'tape') {
                    d.className += ' sn-tape';
                    d.style.width = (it.w || 140) + 'px';
                    d.style.height = (it.h || 26) + 'px';
                    d.style.background = it.color || '#e6d3a3';
                    d.style.opacity = (typeof it.opacity === 'number') ? it.opacity : 0.55;
                }
                el.appendChild(d);
            });
        }
        var cap = (data.book && data.book.pages_per_book) || total;
        paintNav('第 ' + cur + ' / ' + cap + ' 页');
    }

    // 页码 + 几颗键的状态：封面页和正文页共用（cur=0 是封面，1..total 是正文）
    function paintNav(text) {
        var head = text;
        if (data && data.book && data.book.total > 1) head += '（第 ' + data.book.no + ' 本）';
        document.getElementById('pageIndicator').textContent = head;
        document.getElementById('prev').disabled = cur <= 0;
        document.getElementById('next').disabled = cur >= total;
        document.getElementById('mark').disabled = (cur === 0);   // 封面不用夹书签
        document.getElementById('toCover').disabled = (cur === 0);
    }

    // 封面页：整张纸就放他画的那张封面（书架那张小图看不清，翻到这儿看大的）
    function renderCover() {
        el.innerHTML = '';
        var cov = data.book && data.book.cover;
        var box = document.createElement('div');
        box.className = 'cover-page';
        if (cov && cov.kind === 'svg') {
            box.innerHTML = cov.data || '';
            var sv = box.querySelector('svg');
            if (sv) {
                sv.removeAttribute('width');
                sv.removeAttribute('height');
                sv.setAttribute('preserveAspectRatio', 'xMidYMid slice');   // 铺满右半边（多出来的裁掉）
            }
        } else if (cov && cov.kind === 'image') {
            var im = document.createElement('img');
            im.src = cov.data || '';
            im.alt = '';
            im.onerror = function () {
                box.className = 'cover-page cover-empty';
                box.textContent = '封面这张图加载不出来';
            };
            box.appendChild(im);
        } else {
            box.className = 'cover-page cover-empty';
            box.textContent = '这本还没画封面';
        }
        el.appendChild(box);
        paintNav('封面');
    }

    function renderBook(d) {
        data = d;
        if (!d || d.error) { renderPage(); return; }
        var pages = d.pages || [];
        total = pages.length;
        if (d.book && d.book.size) size = { w: d.book.size.w || 900, h: d.book.size.h || 600 };
        if (cur === null) {
            var saved = parseInt(localStorage.getItem(pageKey()) || '-1', 10);
            cur = (saved >= 0 && saved <= total) ? saved
                : (typeof d.current === 'number' && d.current >= 0 && d.current < total ? d.current + 1 : (total ? total : 1));
        } else if (cur > total) {
            cur = total ? total : 1;
        }
        // 它翻了页，而你正停在它之前那一页 → 跟着走（只看当前写作本时）
        // 注意：cur 是显示页（0=封面），他的 d.current 是正文页下标，差 1
        if (d.book && d.book.current && typeof lastCurrent === 'number'
            && cur === lastCurrent + 1 && typeof d.current === 'number' && d.current !== lastCurrent) {
            cur = d.current + 1;
        }
        lastCurrent = (d.book && d.book.current && typeof d.current === 'number') ? d.current : lastCurrent;
        savePage();
        renderShelf();
        renderMarks();
        fitPaper();
        renderPage();
    }

    function loadBook() {
        var url = '/api/notebook' + (curBook ? ('?book=' + encodeURIComponent(curBook)) : '');
        fetch(url, { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                // 记下他实际返回的是哪一本（可能跟我们要的不是同一本，比如那本没了）
                if (d && d.book && d.book.id) curBook = d.book.id;
                renderBook(d);
            })
            .catch(function () { renderBook({ error: 'offline' }); });
    }

    function switchBook(id) {
        if (id === curBook) return;
        curBook = id;
        try { localStorage.setItem('notebook_book_id', id); } catch (e) {}
        cur = null;
        lastCurrent = null;
        loadBook();
    }

    function gotoPage(p) {
        if (p < 0 || p > total) return;   // 0 = 封面页，1..total = 正文页
        cur = p;
        savePage();
        renderPage();
    }

    function sendBookmark(body) {
        if (curBook) body.book = curBook;
        fetch('/api/notebook/bookmark', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d && d.ok) { renderBook(d); }
                else { document.getElementById('foot').textContent = (d && d.msg) || '书签没弄成'; }
            })
            .catch(function () {});
    }

    document.getElementById('prev').addEventListener('click', function () { gotoPage(cur - 1); });
    document.getElementById('next').addEventListener('click', function () { gotoPage(cur + 1); });
    document.getElementById('toCover').addEventListener('click', function () { gotoPage(0); });
    document.getElementById('mark').addEventListener('click', function () {
        if (cur === 0) return;                       // 封面不用夹书签
        sendBookmark({ action: 'add', page: cur - 1 });   // 书签记的是正文页下标
    });

    try {
        var _b = localStorage.getItem('notebook_book_id');
        if (_b) curBook = _b;   // 上次看的是哪一本（那本没了的话，服务端会返回当前写作本并纠正）
    } catch (e) {}

    fitPaper();
    loadBook();
    setInterval(function () {
        if (document.hidden) return;
        loadBook();
    }, 60000);

    // —— 让它自己写一条（配了模型接口才有这颗键）——
    var knock = document.getElementById('knock');
    var foot = document.getElementById('foot');
    knock.addEventListener('click', function () {
        if (knock.disabled) return;
        knock.disabled = true;
        var old = knock.textContent;
        knock.textContent = '…在想';
        fetch('/api/notebook/knock', { method: 'POST', credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d && d.book) renderBook(d);
                foot.textContent = (d && (d.said || d.msg)) || '这次没写';
            })
            .catch(function () { foot.textContent = '没写成（服务没连上）'; })
            .then(function () { knock.disabled = false; knock.textContent = old; });
    });
    fetch('/api/config', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (c) { if (!c || !c.llm) knock.style.display = 'none'; })
        .catch(function () { knock.style.display = 'none'; });
})();
