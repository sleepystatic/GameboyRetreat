// The landing page's demo Game Boy. It plays the real shop menu by itself -
// main menu, a category, a console, Add to Cart - with the D-pad and A lighting
// up as if someone were holding it. The data is live (hero_demo_data() in
// app.py). Clicking anywhere on it opens /play, straight to the console on
// screen while a listing is showing.
(function () {
    const link = document.querySelector('.hero-console');
    const source = document.getElementById('hero-demo-data');
    if (!link || !source) return;

    let data;
    try {
        data = JSON.parse(source.textContent);
    } catch (e) {
        return;     // the server-rendered main menu stays up as a still frame
    }
    const categories = data.categories || [];
    if (!categories.length) return;
    const listings = (data.listings || []).filter(
        l => categories.some(c => c.slug === l.category));

    const screen = link.querySelector('.demo-screen');
    const menu = link.querySelector('.demo-menu');
    const buttons = {
        down: link.querySelector('.demo-down'),
        a: link.querySelector('.demo-a'),
    };

    // Mirrors MENUS['main-menu'] in static/script.js.
    const MAIN_MENU = ['Shop', 'Cart', 'Sell To Us', 'Notify Me', 'About'];

    // Same as formatPrice() in static/script.js: "$175", or "$174.50".
    const price = value => {
        const n = Number(value) || 0;
        return '$' + (Number.isInteger(n) ? String(n) : n.toFixed(2));
    };

    // Milliseconds. Slow enough to follow, quick enough that one loop fits in
    // the time someone spends looking at the hero.
    const PAUSE = { look: 1300, step: 380, press: 240, settle: 450,
                    photo: 1800, cart: 1600, end: 1200 };

    // ---------- drawing ----------
    // Listing titles are typed in the admin panel, so everything goes in as
    // textContent - never as HTML.

    // rows: [{ text, meta, icon, strike, img, cls }]. A row with a meta value
    // is a row-split row like the real menu's: the value sits beside a short
    // title and right-aligned under a long one.
    function draw(rows, active, menuClass) {
        menu.className = 'demo-menu' + (menuClass ? ' ' + menuClass : '');
        menu.replaceChildren(...rows.map(row => {
            const li = document.createElement('li');
            if (row.cls) li.className = row.cls;
            if (row.img) {
                const img = document.createElement('img');
                img.src = row.img;
                img.alt = '';
                li.appendChild(img);
                return li;
            }
            const title = document.createElement('span');
            if (row.icon) {
                const icon = document.createElement('span');
                icon.className = 'icon';
                icon.textContent = row.icon;
                title.appendChild(icon);
            }
            const text = document.createElement(row.strike ? 's' : 'span');
            text.textContent = row.text;
            title.appendChild(text);
            li.appendChild(title);
            if (row.meta != null) {
                const meta = document.createElement('span');
                meta.className = 'meta';
                meta.textContent = row.meta;
                li.appendChild(meta);
                li.classList.add('row-split');
            }
            return li;
        }));
        screen.scrollTop = 0;
        select(active);
    }

    function select(index) {
        const rows = menu.children;
        for (let i = 0; i < rows.length; i++) {
            rows[i].classList.toggle('is-active', i === index);
        }
        const row = rows[index];
        if (!row) return;
        // Keep the cursor in view the way the real menu does, by scrolling the
        // screen itself - scrollIntoView would scroll the whole page as well.
        const top = row.offsetTop;
        const bottom = top + row.offsetHeight;
        if (top < screen.scrollTop) {
            screen.scrollTop = top;
        } else if (bottom > screen.scrollTop + screen.clientHeight) {
            screen.scrollTop = bottom - screen.clientHeight;
        }
    }

    function mainMenu(active) {
        draw(MAIN_MENU.map(text => ({ text })), active);
    }

    function shopMenu(active) {
        draw([{ text: 'Back' }].concat(categories.map(c => ({
            text: c.label,
            meta: c.count ? String(c.count) : '-',
            cls: c.count ? '' : 'is-empty',
        }))), active);
    }

    function categoryMenu(listing, active) {
        draw([{ text: 'Back' }].concat(listing.rows.map(r => r.in_stock
            ? { text: r.title,
                meta: price(r.price) + ' · ' + (r.service ? 'available' : r.left + ' left') }
            : { text: r.title, strike: true, cls: 'is-out',
                icon: r.coming_soon ? '◷' : '✖',
                meta: r.coming_soon ? 'Coming Soon' : 'Out of Stock' })), active);
    }

    // The cursor skips the title (row 0) and price (row 2), as in the real gallery.
    // The title is kept to one line here (see .demo-menu .title) so the name,
    // photo and price all fit on screen at once.
    function gallery(listing, active) {
        draw([
            { text: listing.title, cls: 'title' },
            { img: listing.image, cls: 'photo' },
            { text: 'Price: ' + price(listing.price) },
            { text: 'Add to Cart' },
            { text: 'Back' },
        ], active);
    }

    function cartMessage() {
        draw([
            { text: 'Added to Cart!', cls: 'notice' },
            { text: 'Cart: 1 item', cls: 'notice' },
        ], 0, 'is-message');
    }

    // ---------- timing ----------
    // A sleep that stops counting while the console is off screen or the tab
    // is hidden, so the demo picks up where it was instead of racing ahead.
    let running = true;

    function sleep(ms) {
        return new Promise(resolve => {
            let left = ms;
            (function tick() {
                const step = Math.min(left, 100);
                setTimeout(() => {
                    if (running) left -= step;
                    if (left > 0) tick(); else resolve();
                }, step);
            })();
        });
    }

    // Light a button, do what it does halfway through the press, let go.
    async function press(name, action) {
        buttons[name].classList.add('is-pressed');
        await sleep(PAUSE.press / 2);
        if (action) action();
        await sleep(PAUSE.press / 2);
        buttons[name].classList.remove('is-pressed');
    }

    async function walkDown(from, to) {
        for (let i = from + 1; i <= to; i++) {
            await press('down', () => select(i));
            await sleep(PAUSE.step);
        }
    }

    async function play() {
        for (let round = 0; ; round++) {
            const listing = listings.length ? listings[round % listings.length] : null;

            link.href = '/play';
            mainMenu(0);
            await sleep(PAUSE.look);

            await press('a', () => shopMenu(0));
            await sleep(PAUSE.settle);

            if (!listing) {
                // Nothing in stock with a photo: tour the categories instead.
                await walkDown(0, categories.length);
                await sleep(PAUSE.photo);
                continue;
            }

            new Image().src = listing.image;    // warm the cache for the gallery
            const row = categories.findIndex(c => c.slug === listing.category) + 1;
            await walkDown(0, row);
            await sleep(PAUSE.settle);

            await press('a', () => categoryMenu(listing, 0));
            await sleep(PAUSE.settle);
            await walkDown(0, listing.row + 1);
            await sleep(PAUSE.settle);

            await press('a', () => {
                gallery(listing, 1);
                link.href = '/play?listing=' + encodeURIComponent(listing.slug);
            });
            await sleep(PAUSE.photo);

            await press('down', () => select(3));
            await sleep(PAUSE.settle);
            await press('a', cartMessage);
            await sleep(PAUSE.cart);

            gallery(listing, 3);
            await sleep(PAUSE.end);
        }
    }

    // A still frame of the real menu for anyone who has asked for less motion.
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        shopMenu(1);
        return;
    }

    let onScreen = true;
    const update = () => { running = onScreen && !document.hidden; };
    if ('IntersectionObserver' in window) {
        new IntersectionObserver(entries => {
            onScreen = entries[0].isIntersecting;
            update();
        }).observe(link);
    }
    document.addEventListener('visibilitychange', update);

    play();
})();
