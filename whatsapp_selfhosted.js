// whatsapp_selfhosted.js
//
// Self-hosted WhatsApp bot that runs entirely on your computer.
// No third-party service has access to your WhatsApp.
//
// Setup:
// 1. npm install whatsapp-web.js qrcode-terminal express
// 2. node whatsapp_selfhosted.js
// 3. Scan the QR code with your spare WhatsApp number
// 4. Session saves locally in .whatsapp-session/
// 5. In another terminal: python alerter_selfhosted.py --watch

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const express = require('express');

// Create Express server for Python to communicate with
const app = express();
app.use(express.json());

let client;
let isReady = false;

// Initialize WhatsApp client with local authentication
client = new Client({
    authStrategy: new LocalAuth({
        dataPath: '.whatsapp-session-new'
    }),
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu'
        ]
    },
    qrMaxRetries: 5
});

// Show QR code in terminal
client.on('qr', (qr) => {
    console.log('\n===========================================');
    console.log('Scan this QR code with your spare WhatsApp:');
    console.log('===========================================\n');
    qrcode.generate(qr, { small: true });
    console.log('\nOpen WhatsApp on your phone:');
    console.log('Settings → Linked Devices → Link a Device');
});

// Client is ready
client.on('ready', () => {
    console.log('\n✓ WhatsApp is connected and ready!');
    console.log('✓ Session saved locally in .whatsapp-session/');
    console.log('✓ API server listening on http://localhost:3000');
    console.log('\nNow run: python alerter_selfhosted.py --watch\n');
    isReady = true;
});

// Handle disconnection
client.on('disconnected', (reason) => {
    console.log('WhatsApp disconnected:', reason);
    isReady = false;
});

// API endpoint: check if bot is ready
app.get('/status', (req, res) => {
    res.json({ ready: isReady });
});

// API endpoint: list groups
app.get('/groups', async (req, res) => {
    try {
        if (!isReady) {
            return res.status(503).json({ error: 'WhatsApp not ready yet' });
        }
        
        console.log('Fetching chats...');
        
        // Add retry logic with delay
        let chats = null;
        let lastError = null;
        
        for (let attempt = 1; attempt <= 3; attempt++) {
            try {
                console.log(`Attempt ${attempt} to fetch chats...`);
                chats = await Promise.race([
                    client.getChats(),
                    new Promise((_, reject) => 
                        setTimeout(() => reject(new Error('Timeout')), 15000)
                    )
                ]);
                break; // Success!
            } catch (err) {
                console.log(`Attempt ${attempt} failed:`, err.message);
                lastError = err;
                if (attempt < 3) {
                    console.log('Waiting 2 seconds before retry...');
                    await new Promise(resolve => setTimeout(resolve, 2000));
                }
            }
        }
        
        if (!chats) {
            throw new Error(`Failed after 3 attempts. Last error: ${lastError.message}`);
        }
        
        console.log(`Found ${chats.length} total chats`);
        
        const groups = chats
            .filter(chat => chat.isGroup)
            .map(chat => ({
                id: chat.id._serialized,
                name: chat.name
            }));
        
        console.log(`Found ${groups.length} groups`);
        res.json(groups);
    } catch (error) {
        console.error('Error getting groups:', error);
        res.status(500).json({ 
            error: error.message, 
            hint: 'Try restarting the bot or waiting a few minutes for WhatsApp to fully load'
        });
    }
});

// API endpoint: send message to group
app.post('/send', async (req, res) => {
    try {
        if (!isReady) {
            return res.status(503).json({ error: 'WhatsApp not ready yet' });
        }
        
        const { groupId, message } = req.body;
        
        if (!groupId || !message) {
            return res.status(400).json({ error: 'groupId and message are required' });
        }
        
        await client.sendMessage(groupId, message);
        res.json({ success: true });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

// Start Express server
app.listen(3000, () => {
    console.log('Starting WhatsApp bot...');
    console.log('API server ready on http://localhost:3000');
});

// Initialize WhatsApp client
client.initialize();
