# Income check: web page

React + Vite. A loan officer picks a client, connects all banks, and sees the income estimate with its range, confidence,
reasons and warnings. It talks to the income API (`API/service.py`, port 9000) through the `/api` proxy in `vite.config.js`.

```bash
make frontend-setup     # once: npm install (needs Node.js)
make demo               # starts the APIs and this page; or `make frontend` for the page alone
```

Open http://localhost:5173. `http://localhost:5173/?client=<personal id>` runs the whole flow from a link.
Animation timings are in `src/components/ConnectingPanel.jsx` and `Progress.jsx`; they are switched off when the system asks for reduced motion.
