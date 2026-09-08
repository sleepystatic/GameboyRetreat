// Look up a shell image path from the container's data-shell-* attributes so the
// filenames live in the template only. Falls back to the old convention if the
// attribute is missing.
function shellSrcFor(container, layout) {
    return container.getAttribute(`data-shell-${layout}`) || `/static/${layout}.png`;
}

// A phone is anything narrow in its *portrait* dimension, so the check has to look
// at the shorter edge rather than innerWidth - in landscape innerWidth is the long one.
function isHandheld() {
    return Math.min(window.innerWidth, window.innerHeight) <= 500
        || window.matchMedia('(max-width: 900px)').matches;
}

function isLandscape() {
    return window.innerWidth > window.innerHeight;
}

// Applies a console layout without touching menu state.
function setConsoleLayout(layout) {
    const container = document.querySelector('.gameboy-container');
    if (!container) return;
    if (container.getAttribute('data-console') === layout) return;

    container.querySelector('.shell').src = shellSrcFor(container, layout);
    container.setAttribute('data-console', layout);
}

// On a phone the console follows the device orientation - portrait is the SP,
// landscape is the GBA. Desktop stays on the GBA and switches via the menu.
(function() {
    function syncConsoleToDevice() {
        if (!isHandheld()) return;
        setConsoleLayout(isLandscape() ? 'gba' : 'gbasp');
    }

    window.addEventListener('DOMContentLoaded', syncConsoleToDevice);
    window.addEventListener('resize', syncConsoleToDevice);
    window.addEventListener('orientationchange', syncConsoleToDevice);
})();

// Mobile scaling - apply immediately based on screen width and console type
(function() {
    // Visible shell size inside the image's transparent padding. Scaling against the
    // raw image size would be far too conservative - the GBA artwork is only 633px
    // tall inside a 1254px square.
    function contentSize(container, layout) {
        const raw = container.getAttribute(`data-content-${layout}`);
        if (!raw) return null;
        const [w, h] = raw.trim().split(/\s+/).map(Number);
        return (w && h) ? { w, h } : null;
    }

    function applyMobileScale() {
        const container = document.querySelector('.gameboy-container');
        if (!container) return;

        // Breathing room so the shell never kisses the edge of the viewport
        const MARGIN = 0.94;

        const consoleType = container.getAttribute('data-console');

        if (!isHandheld()) {
            container.style.zoom = '';
            container.style.transform = '';
            return;
        }

        const content = contentSize(container, consoleType);
        if (!content) return;

        // No page rotation any more, so the axes are the real ones.
        const scale = Math.min(
            window.innerWidth / content.w,
            window.innerHeight / content.h
        ) * MARGIN;

        // `zoom` rather than `transform: scale()`. transform resamples pixels that
        // have already been rendered and, worse, puts the whole subtree into
        // grayscale antialiasing - both of which blur the small menu text. `zoom`
        // scales at layout time so glyphs rasterize at their final size.
        container.style.transform = '';
        container.style.zoom = scale;
    }

    window.addEventListener('DOMContentLoaded', applyMobileScale);
    window.addEventListener('resize', applyMobileScale);
    window.addEventListener('orientationchange', applyMobileScale);

    // Also reapply when console switches
    const observer = new MutationObserver(applyMobileScale);
    const container = document.querySelector('.gameboy-container');
    if (container) {
        observer.observe(container, { attributes: true, attributeFilter: ['data-console'] });
    }
})();

// ============================================
// MENU DEFINITIONS
// ============================================
// Every static menu lives here, so adding a screen is one entry rather than an
// edit to index.html plus a new `case` in a switch that fails silently when you
// forget it. Menus built at runtime (cart, gallery, category listings) stay in
// code because their contents depend on data.
//
//   label  - what the player sees
//   action - key into ACTIONS; unknown keys warn instead of doing nothing
//   info   - true for read-only text: not selectable, not highlightable
const MENUS = {
    'main-menu': [
        { label: 'Shop', action: 'menu:shop-menu' },
        { label: 'Cart', action: 'cart' },
        { label: 'Sell To Us', action: 'sell' },
        { label: 'About', action: 'menu:about-menu' },
        { label: 'Switch Console', action: 'switchConsole', desktopOnly: true },
    ],
    'shop-menu': [
        { label: 'Back', action: 'menu:main-menu' },
        { label: 'Gameboy Color', action: 'category:gameboy-color' },
        { label: 'Gameboy Advance', action: 'category:gameboy-advance' },
        { label: 'Gameboy Advance SP', action: 'category:gameboy-advance-sp' },
        { label: 'Nintendo DS Lite', action: 'category:nintendo-ds-lite' },
    ],
    'about-menu': [
        { label: 'Back', action: 'menu:main-menu' },
        { label: 'Creator', action: 'menu:creator-menu' },
        { label: 'Contact', action: 'menu:contact-menu' },
        { label: 'Static Designs', action: 'external:https://staticdesigns.dev/' },
        { label: 'Privacy Policy', action: 'menu:privacy-menu' },
    ],
    'creator-menu': [
        { label: 'A private platform to purchase professionally refurbished handhelds and other games!', info: true },
        { label: 'Back', action: 'menu:about-menu' },
    ],
    'contact-menu': [
        { label: 'Email: t.bryan.dev@gmail.com', info: true },
        { label: 'Location: Stockton, CA', info: true },
        { label: 'Chat: Use the chat bot in the corner!', info: true },
        { label: 'Response Time: Within 24 hours', info: true },
        { label: 'Back', action: 'menu:about-menu' },
    ],
    'privacy-menu': [
        { label: 'We collect minimal information: your email when you submit items to sell, '
               + 'and shipping info during checkout (processed securely by Stripe). We never '
               + 'sell your data. Emails are used only to respond to your inquiries. By using '
               + 'this site, you agree to these terms. Questions? Contact us at '
               + 't.bryan.dev@gmail.com', info: true },
        { label: 'Back', action: 'menu:about-menu' },
    ],
    'sell-menu': [
        { label: 'Got a console to sell? Tell us about it and we will make an offer.', info: true },
        { label: 'Start Submission', action: 'openSellChat' },
        { label: 'Back', action: 'menu:main-menu' },
    ],
};

// Menus whose contents are generated at runtime - rendered by their own functions.
const DYNAMIC_MENUS = ['cart-menu', 'gallery-menu', 'category-listings-menu'];

// A single source of truth for "the cursor cannot land here". Replaces the three
// copies of this list that had already drifted apart.
const NON_SELECTABLE = [
    'info', 'cart-total', 'cart-empty', 'out-of-stock',
    'gallery-title', 'gallery-description', 'gallery-price',
    'cart-item-title', 'cart-item-image', 'cart-item-price',
];

function isSelectable(el) {
    return !!el && !NON_SELECTABLE.some(c => el.classList.contains(c));
}

// Index of the first row the cursor may occupy, or 0 if a menu is all text.
function firstSelectableIndex(items) {
    for (let i = 0; i < items.length; i++) {
        if (isSelectable(items[i])) return i;
    }
    return 0;
}

// Build the static menus into the screen from MENUS.
function renderMenus() {
    const screen = document.querySelector('.screen');
    if (!screen) return;

    const desktop = !isHandheld();
    const html = [];

    Object.entries(MENUS).forEach(([id, items], menuIndex) => {
        const classes = menuIndex === 0 ? 'menu active-menu' : 'menu hidden';
        html.push(`<ul class="${classes}" id="${id}">`);

        items
            .filter(item => !(item.desktopOnly && !desktop))
            .forEach(item => {
                const cls = item.info ? 'menu-item info' : 'menu-item';
                const action = item.action ? ` data-action="${item.action}"` : '';
                html.push(`<li class="${cls}"${action}>${item.label}</li>`);
            });

        html.push('</ul>');
    });

    DYNAMIC_MENUS.forEach(id => {
        html.push(`<ul class="menu hidden" id="${id}"></ul>`);
    });

    screen.innerHTML = html.join('');
}

// Load inventory from database
let listings = {}; // Start empty

async function loadInventory() {
    try {
        const response = await fetch('/api/stock');
        const data = await response.json();
        // A failing /api/stock still returns valid JSON, so an unchecked assignment
        // here leaves `listings` as an error object and the shop menu silently dead.
        if (!response.ok || data.error) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }
        listings = data;
        console.log('✅ Inventory loaded from database:', listings);
    } catch (error) {
        console.error('❌ Failed to load inventory, using fallback:', error);
        // Fallback to hardcoded if API fails
        listings = {
            'gameboy-color': [
                {
                    images: ['static/images/gbc1.png'],
                    title: 'Atomic Purple GBC',
                    description: 'Transparent shell with new buttons. Good working condition.',
                    price: 175,
                    stock: 2
                },
                {
                    images: ['static/images/gbc2.png'],
                    title: 'Teal Blue GBC',
                    description: 'Refurbished with backlit screen. Minor scratches on back.',
                    price: 200,
                    stock: 1
                }
            ],
            'gameboy-advance': [
                {
                    images: ['static/images/gba1.png'],
                    title: 'Clear Purple GBA',
                    description: 'Fully refurbished with IPS screen upgrade.',
                    price: 160,
                    stock: 3
                }
            ],
            'gameboy-advance-sp': [
                {
                    images: ['static/images/gbasp-rayquaza/gbasp1.jpg', 'static/images/gbasp-rayquaza/gbasp2.jpg', 'static/images/gbasp-rayquaza/gbasp3.jpg'],
                    title: 'Rayquaza Edition',
                    description: 'AGS-001 model fully refurbished and functioning like new.',
                    price: 185,
                    stock: 1
                }
            ],
            'nintendo-ds-lite': [
                {
                    images: ['static/images/dslite1.png'],
                    title: 'Polar White DS Lite',
                    description: 'Pristine condition with new shell.',
                    price: 150,
                    stock: 2
                }
            ]
        };
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    // Build the static menus from MENUS before anything queries the DOM for them.
    renderMenus();

    // Load inventory FIRST before allowing interaction
    await loadInventory();

    let currentMenu = document.querySelector('.menu.active-menu');
    let menuItems = currentMenu.querySelectorAll('.menu-item');
    let currentIndex = 0;
    let inGallery = false;
    let currentGalleryIndex = 0;
    let currentGalleryItems = [];
    let currentImageIndex = 0;
    let cart = JSON.parse(localStorage.getItem('gameboyCart')) || [];
    let viewingFromCart = false;

    // Initialize Stripe (key will be injected by Flask template)
    const stripeKey = document.body.getAttribute('data-stripe-key');
    const stripe = stripeKey ? Stripe(stripeKey) : null;

    function updateActivate(index) {
        menuItems.forEach((item) => {
            item.classList.remove('active');
        });
        if (menuItems[index]) {
            menuItems[index].classList.add('active');
            menuItems[index].scrollIntoView({
                behavior: 'smooth',
                block: 'nearest'
            });
        }
    }

    function switchMenu(menuId) {
        currentMenu.classList.remove('active-menu');
        currentMenu.classList.add('hidden');

        currentMenu = document.getElementById(menuId);
        currentMenu.classList.remove('hidden');
        currentMenu.classList.add('active-menu');

        inGallery = false;
        currentGalleryIndex = 0;
        currentGalleryItems = [];

        menuItems = currentMenu.querySelectorAll('.menu-item');

        // Start on the first row the cursor is allowed to occupy - menus that open
        // with a paragraph of text would otherwise highlight the paragraph.
        currentIndex = firstSelectableIndex(menuItems);
        updateActivate(currentIndex);
    }

    function addToCart(item) {
        const itemsInCart = cart.filter(cartItem => cartItem.id === `${item.category}-${currentGalleryIndex}`).length;

        if (itemsInCart >= item.stock) {
            const screen = document.querySelector('.screen');
            const originalContent = screen.innerHTML;

            screen.innerHTML = `
                <div class="cart-message">
                    <div class="menu-item active">Out of Stock!</div>
                    <div class="menu-item">Only ${item.stock} available</div>
                </div>
            `;

            setTimeout(() => {
                screen.innerHTML = originalContent;
                currentMenu = screen.querySelector('.menu.active-menu');
                menuItems = currentMenu.querySelectorAll('.menu-item');
                if (inGallery) {
                    renderGalleryItem();
                } else {
                    updateActivate(currentIndex);
                }
            }, 1500);
            return;
        }

        const cartItem = {
            id: `${item.category}-${currentGalleryIndex}`,
            title: item.title,
            price: item.price,
            img: item.images[0],
            category: item.category
        };

        cart.push(cartItem);
        localStorage.setItem('gameboyCart', JSON.stringify(cart));
        showCartMessage();
    }

    function showCartMessage() {
        const screen = document.querySelector('.screen');
        const originalContent = screen.innerHTML;

        screen.innerHTML = `
            <div class="cart-message">
                <div class="menu-item active">Added to Cart!</div>
                <div class="menu-item">Cart: ${cart.length} items</div>
            </div>
        `;

        setTimeout(() => {
            screen.innerHTML = originalContent;
            currentMenu = screen.querySelector('.menu.active-menu');
            menuItems = currentMenu.querySelectorAll('.menu-item');
            if (inGallery) {
                renderGalleryItem();
            } else {
                updateActivate(currentIndex);
            }
        }, 1000);
    }

    async function handleCheckout() {
        if (cart.length === 0) {
            alert('Your cart is empty!');
            return;
        }

        if (!stripe) {
            alert('Payment system not configured');
            return;
        }

        const screen = document.querySelector('.screen');
        const originalContent = screen.innerHTML;

        screen.innerHTML = `
            <div class="cart-message">
                <div class="menu-item active">Processing...</div>
                <div class="menu-item">Redirecting to checkout</div>
            </div>
        `;

        try {
            const response = await fetch('/create-checkout-session', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ cart: cart }),
            });

            const data = await response.json();

            if (data.error) {
                throw new Error(data.error);
            }

            const result = await stripe.redirectToCheckout({
                sessionId: data.sessionId,
            });

            if (result.error) {
                throw new Error(result.error.message);
            }

        } catch (error) {
            screen.innerHTML = `
                <div class="cart-message">
                    <div class="menu-item active">Error!</div>
                    <div class="menu-item">${error.message}</div>
                </div>
            `;

            setTimeout(() => {
                screen.innerHTML = originalContent;
                currentMenu = screen.querySelector('.menu.active-menu');
                menuItems = currentMenu.querySelectorAll('.menu-item');
                renderCart();
            }, 2000);
        }
    }

    function renderCart() {
        currentMenu.classList.remove('active-menu');
        currentMenu.classList.add('hidden');

        currentMenu = document.getElementById('cart-menu');
        currentMenu.classList.remove('hidden');
        currentMenu.classList.add('active-menu');

        currentMenu.removeAttribute('data-viewing-cart-index');
        inGallery = false;
        currentGalleryIndex = 0;
        currentGalleryItems = [];
        viewingFromCart = false;

        if (cart.length === 0) {
            currentMenu.innerHTML = `
                <li class="menu-item active" data-action="back">Back</li>
                <li class="menu-item cart-empty">Cart is Empty</li>
            `;
        } else {
            let cartHTML = '<li class="menu-item" data-action="back">Back</li>';
            let total = 0;

            cart.forEach((item, index) => {
                cartHTML += `<li class="menu-item" data-cart-index="${index}">${item.title} - $${item.price}</li>`;
                total += item.price;
            });

            cartHTML += `<li class="menu-item cart-total">Total: $${total}</li>`;
            cartHTML += `<li class="menu-item" data-action="checkout">Checkout</li>`;
            cartHTML += `<li class="menu-item" data-action="clearCart">Clear Cart</li>`;
            currentMenu.innerHTML = cartHTML;
        }

        menuItems = currentMenu.querySelectorAll('.menu-item');
        currentIndex = 0;
        updateActivate(currentIndex);
    }

    function showCartItemDetail(itemIndex) {
        const item = cart[itemIndex];

        currentMenu.innerHTML = `
            <li class="menu-item cart-item-title">${item.title}</li>
            <li class="menu-item cart-item-image">
                <img src="${item.img}" alt="${item.title}" class="product-image">
            </li>
            <li class="menu-item cart-item-price">Price: $${item.price}</li>
            <li class="menu-item active" data-action="viewListing">View Listing</li>
            <li class="menu-item" data-action="removeCartItem">Remove Item</li>
            <li class="menu-item" data-action="backToCart">Back to Cart</li>
        `;

        currentMenu.setAttribute('data-viewing-cart-index', itemIndex);

        menuItems = currentMenu.querySelectorAll('.menu-item');
        currentIndex = 3;
        updateActivate(currentIndex);
    }

    function enlargeImage() {
        const item = currentGalleryItems[currentGalleryIndex];
        const currentImage = item.images[currentImageIndex] || item.images[0];
        const screen = document.querySelector('.screen');
        const originalContent = screen.innerHTML;
        let enlargedMode = true;

        document.removeEventListener('keydown', mainKeyHandler);

        screen.innerHTML = `
            <div class="enlarged-image">
                <div class="menu-item">${item.title}</div>
                <div class="menu-item enlarged-image-container">
                    <img src="${currentImage}" alt="${item.title}" class="enlarged-product-image">
                </div>
                <div class="menu-item active" id="enlarged-back-button">Back</div>
            </div>
        `;

        function exitEnlargedMode() {
            if (!enlargedMode) return;
            enlargedMode = false;
            screen.innerHTML = originalContent;
            currentMenu = screen.querySelector('.menu.active-menu');
            menuItems = currentMenu.querySelectorAll('.menu-item');

            renderGalleryItem();
            document.removeEventListener('keydown', tempBackHandler);

            const backButton = document.getElementById('enlarged-back-button');
            if (backButton) {
                backButton.removeEventListener('click', handleBackClick);
                backButton.removeEventListener('touchend', handleBackClick);
            }

            document.addEventListener('keydown', mainKeyHandler);
        }

        const tempBackHandler = (e) => {
            if (!enlargedMode) return;

            if (e.key === 'Escape' || e.key === 'Enter') {
                e.preventDefault();
                e.stopPropagation();
                exitEnlargedMode();
            }
        };

        const handleBackClick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            exitEnlargedMode();
        };

        document.addEventListener('keydown', tempBackHandler);

        const backButton = document.getElementById('enlarged-back-button');
        if (backButton) {
            backButton.addEventListener('click', handleBackClick);
            backButton.addEventListener('touchend', handleBackClick);
        }
    }

    // Action table. Keys come from data-action on each menu item, so a typo shows
    // up as a console warning instead of a button that silently does nothing.
    const ACTIONS = {
        switchConsole() {
            // Desktop only - on a phone the orientation drives this.
            const container = document.querySelector('.gameboy-container');
            const next = container.getAttribute('data-console') === 'gba' ? 'gbasp' : 'gba';
            setConsoleLayout(next);
            switchMenu('main-menu');
        },

        cart() { renderCart(); },

        sell() { switchMenu('sell-menu'); },

        openSellChat() {
            const box = document.getElementById('chat-box');
            const input = document.getElementById('chat-input');
            if (box) box.classList.remove('hidden');
            if (input) input.focus();
        },

        checkout() { handleCheckout(); },

        addToCart() {
            if (inGallery && currentGalleryItems[currentGalleryIndex]) {
                addToCart(currentGalleryItems[currentGalleryIndex]);
            }
        },

        clearCart() {
            if (confirm('Clear all items from cart?')) {
                cart = [];
                localStorage.setItem('gameboyCart', JSON.stringify(cart));
                renderCart();
            }
        },

        backToCart() {
            currentMenu.removeAttribute('data-viewing-cart-index');
            renderCart();
        },

        removeCartItem() {
            const i = parseInt(currentMenu.getAttribute('data-viewing-cart-index'));
            if (!isNaN(i)) {
                cart.splice(i, 1);
                localStorage.setItem('gameboyCart', JSON.stringify(cart));
                currentMenu.removeAttribute('data-viewing-cart-index');
                renderCart();
            }
        },

        viewListing() {
            const i = parseInt(currentMenu.getAttribute('data-viewing-cart-index'));
            if (isNaN(i) || !cart[i]) return;
            const cartItem = cart[i];
            const category = cartItem.category;
            if (!listings[category]) return;

            inGallery = true;
            viewingFromCart = true;
            currentGalleryItems = listings[category];
            currentGalleryItems.forEach(item => item.category = category);
            currentGalleryIndex = currentGalleryItems.findIndex(x => x.title === cartItem.title);
            if (currentGalleryIndex === -1) currentGalleryIndex = 0;
            currentImageIndex = 0;
            renderGalleryItem();
        },

        back() {
            if (inGallery) {
                inGallery = false;
                switchMenu('shop-menu');
            } else if (currentMenu.id === 'category-listings-menu') {
                switchMenu('shop-menu');
            } else {
                switchMenu('main-menu');
            }
        },
    };

    // Dispatch one action string. Handles the prefixed forms (menu:, category:,
    // external:) plus anything in ACTIONS.
    function runAction(action) {
        if (!action) return false;

        if (action.startsWith('menu:')) {
            switchMenu(action.slice(5));
            return true;
        }
        if (action.startsWith('category:')) {
            const category = action.slice(9);
            if (listings[category]) {
                showCategoryListings(category, category);
            } else {
                console.warn(`No listings loaded for category "${category}"`);
            }
            return true;
        }
        if (action.startsWith('external:')) {
            window.open(action.slice(9), '_blank', 'noopener');
            return true;
        }
        if (typeof ACTIONS[action] === 'function') {
            ACTIONS[action]();
            return true;
        }

        console.warn(`Unknown menu action: "${action}"`);
        return false;
    }

    // Runtime-generated rows (cart entries, category listings) carry data
    // attributes instead of an action, so they are resolved here.
    function handleGeneratedItem(el) {
        if (!el) return false;

        if (el.hasAttribute('data-cart-index')) {
            showCartItemDetail(parseInt(el.getAttribute('data-cart-index')));
            return true;
        }

        if (el.hasAttribute('data-listing-category') && el.hasAttribute('data-listing-index')) {
            const category = el.getAttribute('data-listing-category');
            inGallery = true;
            currentGalleryItems = listings[category];
            currentGalleryItems.forEach(item => item.category = category);
            currentGalleryIndex = parseInt(el.getAttribute('data-listing-index'));
            currentImageIndex = 0;
            renderGalleryItem();
            return true;
        }

        return false;
    }

    function handleKeyPress(key) {
        if (inGallery) {
            if (key === 'ArrowRight') {
                const item = currentGalleryItems[currentGalleryIndex];
                if (item.images.length > 1) {
                    currentImageIndex = (currentImageIndex + 1) % item.images.length;
                    renderGalleryItem();
                }
                return;
            } else if (key === 'ArrowLeft') {
                const item = currentGalleryItems[currentGalleryIndex];
                if (item.images.length > 1) {
                    currentImageIndex = (currentImageIndex - 1 + item.images.length) % item.images.length;
                    renderGalleryItem();
                }
                return;
            } else if (key === 'ArrowUp') {
                if (currentIndex === 5) {
                    currentIndex = 4;
                } else if (currentIndex === 4) {
                    currentIndex = 1;
                } else {
                    currentIndex = 5;
                }
                updateActivate(currentIndex);
                return;
            } else if (key === 'ArrowDown') {
                if (currentIndex === 1) {
                    currentIndex = 4;
                } else if (currentIndex === 4) {
                    currentIndex = 5;
                } else {
                    currentIndex = 1;
                }
                updateActivate(currentIndex);
                return;
            } else if (key === 'Enter') {
                if (currentIndex === 1) {
                    enlargeImage();
                } else if (currentIndex === 4) {
                    const item = currentGalleryItems[currentGalleryIndex];
                    addToCart(item);
                } else if (currentIndex === 5) {
                    inGallery = false;
                    if (viewingFromCart) {
                        viewingFromCart = false;
                        renderCart();
                    } else {
                        // Go back to category listings instead of shop menu
                        const category = currentGalleryItems[0]?.category;
                        if (category) {
                            showCategoryListings(category, category);
                        } else {
                            switchMenu('shop-menu');
                        }
                    }
                }
                return;
            } else if (key === 'Escape') {
                inGallery = false;
                if (viewingFromCart) {
                    viewingFromCart = false;
                    renderCart();
                } else {
                    switchMenu('shop-menu');
                }
                return;
            }
        }

        if (key === 'ArrowUp' || key === 'ArrowDown') {
            const step = key === 'ArrowUp' ? -1 : 1;

            if (currentMenu.hasAttribute('data-viewing-cart-index')) {
                currentIndex = currentIndex === 3 ? 4 : 3;
                updateActivate(currentIndex);
                return;
            }

            // Walk past anything unselectable. The guard stops an infinite loop if
            // a menu somehow contains nothing selectable at all.
            const count = menuItems.length;
            for (let hops = 0; hops < count; hops++) {
                currentIndex = (currentIndex + step + count) % count;
                if (isSelectable(menuItems[currentIndex])) break;
            }
            updateActivate(currentIndex);

        } else if (key === 'Enter') {
            const selectedElement = menuItems[currentIndex];
            if (!isSelectable(selectedElement)) return;

            // Static menus declare an action; generated rows carry data attributes.
            if (!runAction(selectedElement.getAttribute('data-action'))) {
                handleGeneratedItem(selectedElement);
            }

        } else if (key === 'Escape') {
            if (currentMenu.id !== 'main-menu') {
                switchMenu('main-menu');
            }
        }
    }

    function renderGalleryItem() {
        currentMenu.classList.remove('active-menu');
        currentMenu.classList.add('hidden');

        currentMenu = document.getElementById('gallery-menu');
        currentMenu.classList.remove('hidden');
        currentMenu.classList.add('active-menu');

        const item = currentGalleryItems[currentGalleryIndex];

        // Determine back text based on where we came from
        let backText = 'Back';
        if (viewingFromCart) {
            backText = 'Back to Cart';
        }

        const currentImage = item.images[currentImageIndex] || item.images[0];
        const imageCount = item.images.length;

        const imageNavHint = imageCount > 1
            ? `<div class="navigation-hint">Image ${currentImageIndex + 1}/${imageCount} - ←/→ to change</div>`
            : '';

        currentMenu.innerHTML = `
            <li class="menu-item gallery-title">${item.title}</li>
            <li class="menu-item gallery-image active">
                <img src="${currentImage}" alt="${item.title}" class="product-image">
                <div class="navigation-hint">↑/↓ navigate menu</div>
                ${imageNavHint}
            </li>
            <li class="menu-item gallery-description">${item.description}</li>
            <li class="menu-item gallery-price">Price: $${item.price}</li>
            <li class="menu-item">Add to Cart</li>
            <li class="menu-item">${backText}</li>
        `;

        menuItems = currentMenu.querySelectorAll('.menu-item');
        currentIndex = 1;
        updateActivate(currentIndex);
    }

    function showCategoryListings(category, categoryDisplayName) {
        currentMenu.classList.remove('active-menu');
        currentMenu.classList.add('hidden');

        currentMenu = document.getElementById('category-listings-menu');
        currentMenu.classList.remove('hidden');
        currentMenu.classList.add('active-menu');

        const items = listings[category];

        let listingsHTML = `<li class="menu-item" data-action="back">Back</li>`;

        items.forEach((item, index) => {
            if (item.stock > 0) {
                // In stock - normal listing
                listingsHTML += `<li class="menu-item" data-listing-category="${category}" data-listing-index="${index}">
                    ${item.title} - $${item.price}
                </li>`;
            } else {
                // Out of stock - grayed out with label
                listingsHTML += `<li class="menu-item out-of-stock">
                    <span class="strikethrough">${item.title} - $${item.price}</span>
                    <span class="stock-label">Out of Stock</span>
                </li>`;
            }
        });

        currentMenu.innerHTML = listingsHTML;
        currentMenu.setAttribute('data-current-category', category);

        menuItems = currentMenu.querySelectorAll('.menu-item');
        currentIndex = 0;
        updateActivate(currentIndex);
    }

    const mainKeyHandler = (e) => {
        if (document.activeElement.id === 'chat-input') {
            return;
        }
        handleKeyPress(e.key);
    };

    document.addEventListener('keydown', mainKeyHandler);

    document.querySelectorAll('.button-zones .btn').forEach(btn => {
        const handleButtonPress = (e) => {
            e.preventDefault();
            const key = e.target.getAttribute('data-key');
            const event = new KeyboardEvent('keydown', { key: key, bubbles: true });
            document.dispatchEvent(event);
        };

        btn.addEventListener('click', handleButtonPress);
        btn.addEventListener('touchend', handleButtonPress);
    });


    function handleMenuItemInteraction(e) {
        const menuItem = e.target.closest('.menu-item');

        if (!menuItem) return;

        if (!isSelectable(menuItem)) return;

        e.preventDefault();
        e.stopPropagation();

        const index = Array.from(menuItems).indexOf(menuItem);
        if (index === -1) return;

        if (menuItem.classList.contains('active') && currentIndex === index) {
            const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
            document.dispatchEvent(event);
        } else {
            currentIndex = index;
            updateActivate(index);
        }
    }

    document.addEventListener('click', handleMenuItemInteraction);
    document.addEventListener('touchend', handleMenuItemInteraction);

    if (menuItems.length > 0) {
        updateActivate(0);
    }
});

// ============================================
// EASTER EGGS
// ============================================

// Konami code: up up down down left right left right B A Start
// The on-screen buttons dispatch synthetic keydown events on document, so this
// listener picks up both physical keys and taps without any extra wiring.
(function() {
    const KONAMI = [
        'ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown',
        'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight',
        'Escape', 'Enter', ' '
    ];

    let progress = 0;
    let unlocked = false;

    function showBanner(text) {
        const existing = document.querySelector('.konami-banner');
        if (existing) existing.remove();

        const banner = document.createElement('div');
        banner.className = 'konami-banner';
        banner.textContent = text;
        document.body.appendChild(banner);

        setTimeout(() => banner.remove(), 2600);
    }

    function triggerKonami() {
        const container = document.querySelector('.gameboy-container');
        if (!container) return;

        unlocked = !unlocked;
        container.classList.toggle('rare-shell', unlocked);
        showBanner(unlocked ? '★ RARE SHELL UNLOCKED ★' : 'SHELL RESTORED');
    }

    document.addEventListener('keydown', (e) => {
        // Don't let the chat box eat the sequence, and ignore stray modifier repeats
        if (document.activeElement && document.activeElement.id === 'chat-input') {
            progress = 0;
            return;
        }

        if (e.key === KONAMI[progress]) {
            progress++;
            if (progress === KONAMI.length) {
                progress = 0;
                triggerKonami();
            }
        } else {
            // Allow a wrong key to still start a fresh attempt
            progress = (e.key === KONAMI[0]) ? 1 : 0;
        }
    });
})();

// ============================================
// CALIBRATION MODE  -  http://localhost:5000/?calibrate=1
// ============================================
// Outlines every hit zone and prints the CSS coordinates of wherever you click on
// the shell, so button positions can be read off the artwork instead of guessed.
// Clicking reports offsetX/offsetY on the shell <img>, which is the element's own
// untransformed coordinate space - the same space the CSS top/left values use, and
// unaffected by the container scale or the mobile page rotation.
(function() {
    const params = new URLSearchParams(window.location.search);
    if (!params.has('calibrate')) return;

    window.addEventListener('DOMContentLoaded', () => {
        document.body.classList.add('calibrate');

        const shell = document.querySelector('.shell');
        const container = document.querySelector('.gameboy-container');
        if (!shell || !container) return;

        const readout = document.createElement('div');
        readout.className = 'calibrate-readout';
        readout.textContent = 'CALIBRATE\nclick the shell for coordinates';
        document.body.appendChild(readout);

        shell.addEventListener('click', (e) => {
            const x = Math.round(e.offsetX);
            const y = Math.round(e.offsetY);
            const layout = container.getAttribute('data-console');
            const block =
                `CALIBRATE  [${layout}]\n` +
                `image ${shell.naturalWidth}x${shell.naturalHeight}\n` +
                `\n` +
                `    top: ${y}px;\n` +
                `    left: ${x}px;`;
            readout.textContent = block;
            console.log(`[calibrate ${layout}]  top: ${y}px;  left: ${x}px;`);
        });
    });
})();

// Select cycles the shell through alternate colorways
(function() {
    const COLORWAYS = ['', 'shell-indigo', 'shell-berry', 'shell-lime', 'shell-ice'];
    let index = 0;

    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Shift') return;
        if (document.activeElement && document.activeElement.id === 'chat-input') return;

        const container = document.querySelector('.gameboy-container');
        if (!container) return;

        container.classList.remove(...COLORWAYS.filter(Boolean));
        index = (index + 1) % COLORWAYS.length;
        if (COLORWAYS[index]) {
            container.classList.add(COLORWAYS[index]);
        }
    });
})();