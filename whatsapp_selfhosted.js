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

const path = require('path');
const os = require('os');
const fs = require('fs');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcodeTerminal = require('qrcode-terminal');
const qrcode = require('qrcode');
const express = require('express');

// Keep session off OneDrive so Chrome/db files are not locked during sync.
// On Linux/VPS: export WA_SESSION_DIR=/var/lib/newsapp-session
const WA_SESSION_DIR = process.env.WA_SESSION_DIR || path.join(
    process.env.LOCALAPPDATA || os.homedir(),
    'NEWSAPP-whatsapp-session'
);

process.on('unhandledRejection', (err) => {
    console.error('WhatsApp client error (API stays up):', err && err.message ? err.message : err);
});

// Create Express server for Python to communicate with
const app = express();
app.use(express.json());

let client;
let isReady = false;
let latestQr = null; // data URL of the current QR image, or null when not needed
let cachedGroups = null;
let reinitTimer = null;
let reinitDelayMs = 3000;
const REINIT_DELAY_MAX_MS = 5 * 60 * 1000; // cap at 5 minutes between attempts

// Doubles after every failed attempt, reset to 3s once 'ready' fires again.
// A fixed 3s retry, hit repeatedly during a genuine outage (e.g. the EBUSY
// loop this was written to fix), hammers WhatsApp's servers with rapid
// repeated device-pairing/session-init attempts - exactly the kind of
// pattern their abuse detection flags an account for, separate from
// anything about the message-sending itself.
function scheduleReinit(reason) {
    if (reinitTimer) return;
    isReady = false;
    cachedGroups = null;
    const delay = reinitDelayMs;
    console.log(`WhatsApp page broke (${reason}). Reconnecting in ${Math.round(delay / 1000)}s...`);
    reinitTimer = setTimeout(async () => {
        reinitTimer = null;
        // Tear down the old Puppeteer browser first - without this, its
        // still-open Chromium process keeps the session folder's files
        // locked, so the new initialize() below fails with EBUSY / "browser
        // already running" in a loop that never reaches a fresh QR code.
        try {
            await client.destroy();
        } catch (err) {
            console.error('destroy() before reinit failed (continuing anyway):', err.message);
        }
        reinitDelayMs = Math.min(reinitDelayMs * 2, REINIT_DELAY_MAX_MS);
        client.initialize().catch((err) => {
            console.error('Re-init failed:', err.message);
        });
    }, delay);
}

// Initialize WhatsApp client with local authentication
client = new Client({
    authStrategy: new LocalAuth({
        dataPath: WA_SESSION_DIR
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
    qrMaxRetries: 20
});

// Show QR code in terminal, and keep a scannable image version for the web UI
client.on('qr', async (qr) => {
    console.log('\n===========================================');
    console.log('Scan this QR code with your spare WhatsApp:');
    console.log('===========================================\n');
    qrcodeTerminal.generate(qr, { small: true });
    console.log('\nOpen WhatsApp on your phone:');
    console.log('Settings → Linked Devices → Link a Device');
    console.log('\n(Or scan it from the "Connect WhatsApp" tab in the web app instead.)');
    try {
        latestQr = await qrcode.toDataURL(qr);
    } catch (err) {
        console.error('Failed to render QR image for the web UI:', err.message);
    }
});

// A code got scanned and WhatsApp accepted it - about to finish loading
client.on('authenticated', () => {
    console.log('\n✓ QR scan accepted by WhatsApp - finishing setup...');
});

// A scan was attempted but WhatsApp rejected it (wrong reason shows up here,
// e.g. device limit, expired code, account restriction)
client.on('auth_failure', (msg) => {
    console.error('\n✗ WhatsApp REJECTED the link attempt:', msg);
    console.error('This usually means the linked-devices limit (max 4) was hit,');
    console.error('or the phone lost internet mid-scan. Check Settings -> Linked');
    console.error('Devices on the phone, remove anything unused, then try again.');
});

// Client is ready
client.on('ready', () => {
    console.log('\n✓ WhatsApp is connected and ready!');
    console.log(`✓ Session saved locally in ${WA_SESSION_DIR}`);
    console.log('✓ API server listening on http://localhost:3000');
    isReady = true;
    latestQr = null;
    cachedGroups = null;
    reinitDelayMs = 3000; // healthy again - drop back to the fast retry for next time
});

// Handle disconnection. Reconnect regardless of reason - LOGOUT, max QR
// retries exhausted, or anything else - so an unwatched bot recovers on
// its own instead of sitting dead until someone notices and restarts it.
client.on('disconnected', (reason) => {
    console.log('WhatsApp disconnected:', reason, '- reconnecting...');
    latestQr = null;
    scheduleReinit(reason || 'disconnected');
});

// API endpoint: check if bot is ready
app.get('/status', (req, res) => {
    res.json({ ready: isReady });
});

// API endpoint: current QR code image (data URL), for the web UI to display
app.get('/qr', (req, res) => {
    res.json({ ready: isReady, qr: isReady ? null : latestQr });
});

// API endpoint: list groups. ?refresh=1 bypasses the cache - needed after
// the bot's number gets added to a new WhatsApp group, since cachedGroups
// otherwise only clears on a reconnect, not on a normal request.
app.get('/groups', async (req, res) => {
    try {
        if (!isReady) {
            return res.status(503).json({ error: 'WhatsApp not ready yet' });
        }

        const forceRefresh = req.query.refresh === '1';
        if (cachedGroups && !forceRefresh) {
            return res.json(cachedGroups);
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
        cachedGroups = groups;
        res.json(groups);
    } catch (error) {
        console.error('Error getting groups:', error);
        if (String(error.message || '').includes('detached Frame')) {
            scheduleReinit('detached Frame');
        }
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
        console.error('Error sending message:', error);
        if (String(error.message || '').includes('detached Frame')) {
            scheduleReinit('detached Frame');
        }
        res.status(500).json({ error: error.message });
    }
});

// API endpoint: resolve a Google News redirect link to its real destination.
// Only ever called once per message, right before it's actually sent - not
// for every extracted candidate. Google's redirect is client-side JS, so a
// plain HTTP request can't follow it; this reuses the browser this bot
// already has open for WhatsApp itself rather than adding a second one.
// Any failure (timeout, page error, missing browser handle) falls back to
// the original Google link so a slow/broken resolve never blocks a send.
app.post('/resolve-link', async (req, res) => {
    const { url } = req.body;
    if (!url) {
        return res.status(400).json({ error: 'url is required' });
    }
    if (!url.includes('news.google.com') || !client.pupBrowser) {
        return res.json({ url });
    }
    let page;
    try {
        page = await client.pupBrowser.newPage();
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 8000 });
        await page.waitForFunction(
            () => !location.href.includes('news.google.com'),
            { timeout: 6000 }
        ).catch(() => {});
        const finalUrl = page.url();
        res.json({ url: finalUrl.includes('news.google.com') ? url : finalUrl });
    } catch (err) {
        console.error('Link resolve failed, using original link:', err.message);
        res.json({ url });
    } finally {
        if (page) {
            try { await page.close(); } catch (_) {}
        }
    }
});

// API endpoint: fully unlink the current number so a different one can be
// scanned in. Deliberately skips client.logout() - calling it can itself
// fire the 'disconnected' event and race with this handler's own reconnect,
// triggering client.initialize() twice concurrently. Instead this does a
// direct hard reset (destroy + wipe the saved session folder, then reuse
// the same guarded reconnect path as every other recovery in this file) so
// a stale-but-technically-valid session can't silently keep reconnecting
// to the OLD number. The number itself isn't cleanly unlinked from
// WhatsApp's own Linked Devices list this way - that's a cosmetic leftover
// the person can clear from their phone if they want to, not a functional
// problem for us since we're wiping our own local session either way.
app.post('/disconnect', async (req, res) => {
    console.log('Disconnect requested: unlinking current number...');
    try {
        await client.destroy();
    } catch (err) {
        console.error('destroy() failed (continuing anyway):', err.message);
    }
    try {
        fs.rmSync(WA_SESSION_DIR, { recursive: true, force: true });
    } catch (err) {
        console.error('Could not clear session folder:', err.message);
    }

    res.json({ success: true, message: 'Disconnected. A new QR code will appear shortly - scan it with the new number.' });
    scheduleReinit('manual disconnect');
});

// Start Express server
app.listen(3000, () => {
    console.log('Starting WhatsApp bot...');
    console.log('API server ready on http://localhost:3000');
});

// Initialize WhatsApp client
client.initialize();
