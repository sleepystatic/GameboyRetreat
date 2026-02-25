

    // Device-specific console detection - SIMPLIFIED
    (function() {
        function selectConsoleLayout() {
            const gameboyContainer = document.querySelector('.gameboy-container');
            const shellImage = gameboyContainer.querySelector('.shell');
            const screenWidth = window.innerWidth;

            if (screenWidth <= 768) {
                // Mobile - SP
                shellImage.src = '/static/gbasp.png';
                gameboyContainer.setAttribute('data-console', 'gbasp');
                console.log('Set to GBASP');
            } else {
                // Desktop - GBA
                shellImage.src = '/static/gba.png';
                gameboyContainer.setAttribute('data-console', 'gba');
                console.log('Set to GBA');
            }
        }

        window.addEventListener('DOMContentLoaded', selectConsoleLayout);
        window.addEventListener('resize', selectConsoleLayout);
    })();

    // Mobile scaling - apply immediately based on screen width
    (function() {
        function applyMobileScale() {
            const container = document.querySelector('.gameboy-container');
            if (!container) return;

            const screenWidth = window.innerWidth;
            let scale;

            if (screenWidth <= 400) {
                scale = 1.0;
            } else if (screenWidth <= 480) {
                scale = 1.0;
            } else if (screenWidth <= 768) {
                scale = 1.2;
            } else {
                scale = 1.0;
            }

            container.style.transform = `scale(${scale})`;
        }

        window.addEventListener('DOMContentLoaded', applyMobileScale);
        window.addEventListener('resize', applyMobileScale);
    })();

    document.addEventListener('DOMContentLoaded', () => {
        let currentMenu = document.querySelector('.menu.active-menu');
        let menuItems = currentMenu.querySelectorAll('.menu-item');
        let currentIndex = 0;
        let inGallery = false;
        let currentGalleryIndex = 0;
        let currentGalleryItems = [];
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

            //const aboutSubMenus = ['creator-menu', 'contact-menu', 'sleepystatic-menu'];
            currentMenu.classList.remove('active-menu');
            currentMenu.classList.add('hidden');

            currentMenu = document.getElementById(menuId);
            currentMenu.classList.remove('hidden');
            currentMenu.classList.add('active-menu');

            inGallery = false;
            currentGalleryIndex = 0;
            currentGalleryItems = [];

            menuItems = currentMenu.querySelectorAll('.menu-item');

            currentIndex = 0;
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
                img: item.img,
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
                    <li class="menu-item active">Back</li>
                    <li class="menu-item cart-empty">Cart is Empty</li>
                `;
            } else {
                let cartHTML = '<li class="menu-item">Back</li>';
                let total = 0;

                cart.forEach((item, index) => {
                    cartHTML += `<li class="menu-item" data-cart-index="${index}">${item.title} - $${item.price}</li>`;
                    total += item.price;
                });

                cartHTML += `<li class="menu-item cart-total">Total: $${total}</li>`;
                cartHTML += `<li class="menu-item">Checkout</li>`;
                cartHTML += `<li class="menu-item">Clear Cart</li>`;
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
                <li class="menu-item active">View Listing</li>
                <li class="menu-item">Remove Item</li>
                <li class="menu-item">Back to Cart</li>
            `;

            currentMenu.setAttribute('data-viewing-cart-index', itemIndex);

            menuItems = currentMenu.querySelectorAll('.menu-item');
            currentIndex = 3;
            updateActivate(currentIndex);
        }

        function enlargeImage() {
            const item = currentGalleryItems[currentGalleryIndex];
            const screen = document.querySelector('.screen');
            const originalContent = screen.innerHTML;
            let enlargedMode = true;

            document.removeEventListener('keydown', mainKeyHandler);

            screen.innerHTML = `
                <div class="enlarged-image">
                    <div class="menu-item">${item.title}</div>
                    <div class="menu-item enlarged-image-container">
                        <img src="${item.img}" alt="${item.title}" class="enlarged-product-image">
                    </div>
                    <div class="menu-item active">Back</div>
                </div>
            `;

            menuItems = document.querySelectorAll('.menu-item');



            function exitEnlargedMode() {
                if (!enlargedMode) return;
                enlargedMode = false;
                screen.innerHTML = originalContent;
                currentMenu = screen.querySelector('.menu.active-menu');
                menuItems = currentMenu.querySelectorAll('.menu-item');


                renderGalleryItem();
                document.removeEventListener('keydown', tempBackHandler);
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

            document.addEventListener('keydown', tempBackHandler);
        }

        function handleSelect(text) {
            switch (text) {
                case 'switch console':
                    const gameboyContainer = document.querySelector('.gameboy-container');
                    const currentLayout = gameboyContainer.getAttribute('data-console');
                    const newLayout = currentLayout === 'gba' ? 'gbasp' : 'gba';

                    const shellImage = gameboyContainer.querySelector('.shell');
                    shellImage.src = `/static/${newLayout}.png`;
                    gameboyContainer.setAttribute('data-console', newLayout);

                    switchMenu('main-menu');
                    break;

                case 'home':
                    switchMenu('main-menu');
                    break;
                case 'shop':
                    switchMenu('shop-menu');
                    break;
                case 'cart':
                    renderCart();
                    break;
                case 'about':
                    switchMenu('about-menu');
                    break;
                case 'creator':
                    switchMenu('creator-menu');
                    break;
                case 'sleepy static':
                    window.open('https://sleepystatic.com/', '_blank');
                    break;
                case 'contact':
                    switchMenu('contact-menu');
                    break;
                case 'privacy policy':
                    switchMenu('privacy-menu');
                    break;
                case 'back':
                    if (inGallery) {
                        inGallery = false;
                        switchMenu('shop-menu');

                    } else if (currentMenu.id === 'creator-menu' || currentMenu.id === 'contact-menu' || currentMenu.id === 'sleepystatic-menu') {
                        switchMenu('about-menu');

                    } else {
                        switchMenu('main-menu');

                    }
                    break;
                case 'checkout':
                    handleCheckout();
                    break;
                case 'clear cart':
                    const confirmClear = confirm('Clear all items from cart?');
                    if (confirmClear) {
                        cart = [];
                        localStorage.setItem('gameboyCart', JSON.stringify(cart));
                        renderCart();
                    }
                    break;
                case 'back to cart':
                    if (currentMenu.hasAttribute('data-viewing-cart-index')) {
                        currentMenu.removeAttribute('data-viewing-cart-index');
                    }
                    renderCart();
                    break;
                case 'remove item':
                    const indexToRemove = parseInt(currentMenu.getAttribute('data-viewing-cart-index'));
                    if (!isNaN(indexToRemove)) {
                        cart.splice(indexToRemove, 1);
                        localStorage.setItem('gameboyCart', JSON.stringify(cart));
                        currentMenu.removeAttribute('data-viewing-cart-index');
                        renderCart();
                    }
                    break;
                case 'view listing':
                    const viewIndex = parseInt(currentMenu.getAttribute('data-viewing-cart-index'));
                    if (!isNaN(viewIndex) && cart[viewIndex]) {
                        const cartItem = cart[viewIndex];
                        const category = cartItem.category;
                        if (listings[category]) {
                            inGallery = true;
                            viewingFromCart = true;
                            currentGalleryItems = listings[category];
                            currentGalleryItems.forEach(item => item.category = category);
                            currentGalleryIndex = currentGalleryItems.findIndex(item => item.title === cartItem.title);
                            if (currentGalleryIndex === -1) currentGalleryIndex = 0;
                            renderGalleryItem();
                        }
                    }
                    break;
                case 'add to cart':
                    if (inGallery && currentGalleryItems[currentGalleryIndex]) {
                        const item = currentGalleryItems[currentGalleryIndex];
                        addToCart(item);
                    }
                    break;
                default:
                    const selectedElement = menuItems[currentIndex];
                    if (selectedElement && selectedElement.hasAttribute('data-cart-index')) {
                        const cartIndex = parseInt(selectedElement.getAttribute('data-cart-index'));
                        showCartItemDetail(cartIndex);
                    }
            }
        }

        function handleKeyPress(key) {

            if (inGallery) {
                if (key === 'ArrowRight') {
                    currentGalleryIndex = (currentGalleryIndex + 1) % currentGalleryItems.length;
                    renderGalleryItem();
                    return;
                } else if (key === 'ArrowLeft') {
                    currentGalleryIndex = (currentGalleryIndex - 1 + currentGalleryItems.length) % currentGalleryItems.length;
                    renderGalleryItem();
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
                            switchMenu('shop-menu');
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

            if (key === 'ArrowUp') {
                if (currentMenu.hasAttribute('data-viewing-cart-index')) {
                    currentIndex = currentIndex === 3 ? 4 : 3;
                    updateActivate(currentIndex);
                } else {
                    do {
                        currentIndex = (currentIndex - 1 + menuItems.length) % menuItems.length;
                    } while (menuItems[currentIndex] && (
                        menuItems[currentIndex].classList.contains('cart-total') ||
                        menuItems[currentIndex].classList.contains('cart-empty') ||
                        menuItems[currentIndex].classList.contains('gallery-title') ||
                        menuItems[currentIndex].classList.contains('gallery-description') ||
                        menuItems[currentIndex].classList.contains('gallery-price') ||
                        menuItems[currentIndex].classList.contains('cart-item-title') ||
                        menuItems[currentIndex].classList.contains('cart-item-image') ||
                        menuItems[currentIndex].classList.contains('cart-item-price')
                    ));
                    updateActivate(currentIndex);
                }
            } else if (key === 'ArrowDown') {
                if (currentMenu.hasAttribute('data-viewing-cart-index')) {
                    currentIndex = currentIndex === 3 ? 4 : 3;
                    updateActivate(currentIndex);
                } else {
                    do {
                        currentIndex = (currentIndex + 1) % menuItems.length;
                    } while (menuItems[currentIndex] && (
                        menuItems[currentIndex].classList.contains('cart-total') ||
                        menuItems[currentIndex].classList.contains('cart-empty') ||
                        menuItems[currentIndex].classList.contains('gallery-title') ||
                        menuItems[currentIndex].classList.contains('gallery-description') ||
                        menuItems[currentIndex].classList.contains('gallery-price') ||
                        menuItems[currentIndex].classList.contains('cart-item-title') ||
                        menuItems[currentIndex].classList.contains('cart-item-image') ||
                        menuItems[currentIndex].classList.contains('cart-item-price')
                    ));
                    updateActivate(currentIndex);
                }
            } else if (key === 'Enter') {
                const selectedText = menuItems[currentIndex].textContent.trim().toLowerCase();

                const categoryMap = {
                    'gameboy color': 'gameboy-color',
                    'gameboy advance': 'gameboy-advance',
                    'gameboy advance sp': 'gameboy-advance-sp',
                    'nintendo ds lite': 'nintendo-ds-lite'
                };

                if (categoryMap[selectedText] && listings[categoryMap[selectedText]]) {
                    inGallery = true;
                    currentGalleryItems = listings[categoryMap[selectedText]];
                    currentGalleryItems.forEach(item => item.category = categoryMap[selectedText]);
                    currentGalleryIndex = 0;
                    renderGalleryItem();
                    return;
                }

                handleSelect(selectedText);
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
            const backText = viewingFromCart ? 'Back to Cart' : 'Back';

            currentMenu.innerHTML = `
                <li class="menu-item gallery-title">${item.title}</li>
                <li class="menu-item gallery-image active">
                    <img src="${item.img}" alt="${item.title}" class="product-image">
                    <div class="navigation-hint">←/→ browse items</div>
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

        const mainKeyHandler = (e) => {
            if (document.activeElement.id === 'chat-input') {
                return;
            }
            handleKeyPress(e.key);
        };

        document.addEventListener('keydown', mainKeyHandler);

        document.querySelectorAll('.button-zones .btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const key = e.target.getAttribute('data-key');
                const event = new KeyboardEvent('keydown', { key: key, bubbles: true });
                document.dispatchEvent(event);
            });
        });

        const listings = {
            'gameboy-color': [
                {
                    img: 'static/images/gbc1.png',
                    title: 'Atomic Purple GBC',
                    description: 'Transparent shell with new buttons. Good working condition.',
                    price: 175,
                    stock: 2
                },
                {
                    img: 'static/images/gbc2.png',
                    title: 'Teal Blue GBC',
                    description: 'Refurbished with backlit screen. Minor scratches on back.',
                    price: 200,
                    stock: 1
                }
            ],
            'gameboy-advance': [
                {
                    img: 'static/images/gba1.png',
                    title: 'Clear Purple GBA',
                    description: 'Fully refurbished with IPS screen upgrade.',
                    price: 160,
                    stock: 3
                }
            ],
            'gameboy-advance-sp': [
                {
                    img: 'static/images/gbasp1.png',
                    title: 'Cobalt Blue SP',
                    description: 'AGS-101 model with bright backlit screen.',
                    price: 185,
                    stock: 1
                }
            ],
            'nintendo-ds-lite': [
                {
                    img: 'static/images/dslite1.png',
                    title: 'Polar White DS Lite',
                    description: 'Pristine condition with new shell.',
                    price: 150,
                    stock: 2
                }
            ]
        };


        document.addEventListener('click', (e) => {
            // Find if we clicked a menu item
            const menuItem = e.target.closest('.menu-item');

            if (!menuItem) return;

            // Skip non-interactive items
            if (menuItem.classList.contains('cart-total') ||
                menuItem.classList.contains('cart-empty') ||
                menuItem.classList.contains('gallery-title') ||
                menuItem.classList.contains('gallery-description') ||
                menuItem.classList.contains('gallery-price') ||
                menuItem.classList.contains('cart-item-title') ||
                menuItem.classList.contains('cart-item-image') ||
                menuItem.classList.contains('cart-item-price') ||
                menuItem.classList.contains('creator-bio') ||
                menuItem.classList.contains('contact-bio') ||
                menuItem.classList.contains('sleepy-bio')) {
                return;
            }

            e.stopPropagation();

            // Find the index of this menu item
            const index = Array.from(menuItems).indexOf(menuItem);
            if (index === -1) return;


            // If clicking an already active item
            if (menuItem.classList.contains('active') && currentIndex === index) {
                const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
                document.dispatchEvent(event);
            } else {
                currentIndex = index;
                updateActivate(index);
            }
        });


        if (menuItems.length > 0) {
            updateActivate(0);
        }
    });