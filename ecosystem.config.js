// ecosystem.config.js
//
// One-command start for both processes: `pm2 start ecosystem.config.js`
// PM2 auto-restarts either one if it crashes (e.g. the WhatsApp bot's
// occasional "detached Frame" break) instead of it sitting dead silently.
//
// One-time setup on a fresh box, before the command above works:
//   npm install -g pm2
//   npm install
//   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
//   npx puppeteer browsers install chrome
//   cp /path/to/.env .                (API key - never commit this file)
//   cp -r /path/to/old-session ~/NEWSAPP-whatsapp-session   (see whatsapp_selfhosted.js
//                                      WA_SESSION_DIR - matches the Linux default home-dir
//                                      fallback since LOCALAPPDATA doesn't exist on Linux)
//   cp /path/to/selfhosted_config.json .
//
// To survive a reboot too (not just a crash):
//   pm2 save
//   pm2 startup            (then run the sudo command it prints, once)

module.exports = {
  apps: [
    {
      name: "newsapp-whatsapp",
      script: "whatsapp_selfhosted.js",
      cwd: __dirname,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 20,
    },
    {
      name: "newsapp-flask",
      script: "app.py",
      interpreter: "./.venv/bin/python3",
      cwd: __dirname,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 20,
    },
  ],
};
