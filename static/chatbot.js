// Chat Bot Logic
document.addEventListener('DOMContentLoaded', () => {
    const chatIcon = document.getElementById('chat-icon');
    const chatBox = document.getElementById('chat-box');
    const chatClose = document.getElementById('chat-close');
    const chatMessages = document.getElementById('chat-messages');
    const chatInput = document.getElementById('chat-input');
    const chatSend = document.getElementById('chat-send');
    const chatInputContainer = document.getElementById('chat-input-container');

    let conversationStep = 0;
    let userData = {
        item: '',
        condition: '',
        price: '',
        shipping: '',
        email: '',
        timestamp: ''
    };

    // Toggle chat box
    chatIcon.addEventListener('click', () => {
        chatBox.classList.toggle('hidden');
        if (!chatBox.classList.contains('hidden')) {
            chatInput.focus();
        }
    });

    chatClose.addEventListener('click', () => {
        chatBox.classList.add('hidden');
    });

    // Add message to chat
    function addMessage(text, isBot = true) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `chat-message ${isBot ? 'bot-message' : 'user-message'}`;
        messageDiv.textContent = text;
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // Add button options
    function addButtons(options) {
        const buttonsDiv = document.createElement('div');
        buttonsDiv.className = 'chat-buttons';

        options.forEach(option => {
            const button = document.createElement('button');
            button.className = 'chat-button';
            button.textContent = option;
            button.addEventListener('click', () => {
                handleUserResponse(option);
                buttonsDiv.remove();
            });
            buttonsDiv.appendChild(button);
        });

        chatMessages.appendChild(buttonsDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // Handle conversation flow
    function handleUserResponse(response) {
        // Add user message
        addMessage(response, false);

        switch(conversationStep) {
            case 0: // Initial response
                conversationStep = 1;
                setTimeout(() => {
                    addMessage("Awesome! What are you looking to sell?");
                }, 500);
                break;

            case 1: // Item name
                userData.item = response;
                conversationStep = 2;
                setTimeout(() => {
                    addMessage("What condition is it in?");
                    addButtons(['Mint', 'Good', 'Fair', 'For Parts']);
                    hideInput();
                }, 500);
                break;

            case 2: // Condition
                userData.condition = response;
                conversationStep = 3;
                setTimeout(() => {
                    addMessage("What price are you hoping for? (e.g., $80)");
                    showInput();
                }, 500);
                break;

            case 3: // Price
                userData.price = response;
                conversationStep = 4;
                setTimeout(() => {
                    addMessage("Are you local to Modesto for pickup, or prefer shipping?");
                    addButtons(['Local Pickup', 'Shipping']);
                    hideInput();
                }, 500);
                break;

            case 4: // Shipping preference
                userData.shipping = response;
                conversationStep = 5;
                setTimeout(() => {
                    addMessage("Great! What's your email so we can reach you?");
                    showInput();
                }, 500);
                break;

            case 5: // Email
                userData.email = response;
                userData.timestamp = new Date().toISOString();
                conversationStep = 6;

                // Submit to backend
                submitData();

                setTimeout(() => {
                    addMessage("Thanks! We'll reach out to you shortly! 🎮");
                    hideInput();
                }, 500);
                break;
        }
    }

    // Submit data to backend
    async function submitData() {
        try {
            const response = await fetch('/submit-seller', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(userData),
            });

            const data = await response.json();

            if (data.error) {
                console.error('Submission error:', data.error);
            } else {
                console.log('Submission successful:', data);
            }
        } catch (error) {
            console.error('Error submitting data:', error);
        }
    }

    // Show/hide input
    function hideInput() {
        chatInputContainer.style.display = 'none';
    }

    function showInput() {
        chatInputContainer.style.display = 'flex';
        chatInput.focus();
    }

    // Send message on button click
    chatSend.addEventListener('click', () => {
        const message = chatInput.value.trim();
        if (message) {
            handleUserResponse(message);
            chatInput.value = '';
        }
    });

    // Send message on Enter key
    chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            const message = chatInput.value.trim();
            if (message) {
                handleUserResponse(message);
                chatInput.value = '';
            }
        }
    });
});