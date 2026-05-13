# Serpilas Virtual Money Transfer - Project Context

## Overview
This project is a hybrid desktop/mobile application that acts as a local server for a virtual economy (e.g., for playing Monopoly).

### How it Works
1.  **The Host (Server):** Runs the Python application (on a Laptop or Android Phone).
    -   This starts a **Flask Web Server** on the local Wi-Fi network.
    -   It also opens a **Kivy GUI** window that displays the server's IP address and a QR code for easy connection.
2.  **The Clients (Players):** Use their own smartphones to scan the QR code or type the URL.
    -   They interact with the **Web Interface** served by Flask.
    -   They can send money, receive money, and view their balance.

## Codebase Map

### `main.py` (The Host GUI)
-   **Role:** Application Entry Point & GUI.
-   **Key Classes:**
    -   `ServerThread`: A `threading.Thread` subclass that runs `app.run()`.
    -   `MoneyTransferApp`: The Kivy App class.
    -   `ServerUI`: The main Kivy widget layout (displays IP, QR code, logs).
-   **Responsibilities:**
    -   Finding the local IP address (`get_ip_address()`).
    -   Generating the QR code for the server URL.
    -   Starting the Flask server in a background thread so the GUI doesn't freeze.

### `app.py` (The Web Server)
-   **Role:** The Backend logic and Web UI.
-   **Key Components:**
    -   **SQLAlchemy Models:**
        -   `User`: Stores username, balance, role (admin/banker).
        -   `Transaction`: History of money movement.
    -   **Routes:**
        -   `/`: Login/Dashboard.
        -   `/send`: Logic for transferring money between users.
        -   `/admin`: Dashboard for the "Banker" to manage users and reset the game.
-   **Templating:** Currently uses `render_template_string` with inline HTML constants (e.g., `LOGIN_TEMPLATE` in `app.py`).
    -   *Note:* The `templates/` directory exists but appears to be unused or legacy code in favor of the single-file distribution model for Android.

### `buildozer.spec`
-   **Role:** Android Build Configuration.
-   **Key Settings:**
    -   `requirements`: Must include `flask`, `kivy`, `sqlalchemy`, etc.
    -   `permissions`: Needs Internet access to serve the web pages.

## Key Workflows
1.  **Initialization:**
    -   `main.py` starts -> Checks for `currency.db` -> Spawns Flask Thread.
    -   Flask init -> `db.create_all()` (creates tables if missing).
2.  **Money Transfer:**
    -   User A (Client) POSTs to `/send`.
    -   Flask checks User A's balance.
    -   Updates User A and User B balances in `currency.db`.
    -   Records transaction.
    -   Returns success/fail message.

## Troubleshooting / Common Issues
-   **IP Address:** Sometimes the app picks the wrong network interface (e.g., Docker bridge instead of Wi-Fi). The `get_ip_address` function in `main.py` attempts to connect to `8.8.8.8` to find the real outward-facing IP.
-   **Android Permissions:** Ensure the app has permission to use the network.
-   **Database Locks:** Since SQLite is file-based, high concurrency might cause locks, though unlikely in a turn-based game setting.

## Recent Updates (2026-01-17)

### Context
- **Goal:** Host phone runs APK (Kivy + Flask on port 8080), other phones join via browser. Host also plays.
- **Current State:** APK runs, shows IP/QR. Flask routes exist. QR generates correct link. `buildozer.spec` configured.
- **Current Problem:** Second phone sees "site can't be reached". `get_ip()` likely returning wrong IP (cellular/localhost) or `hostname -I` failing.

### Tech Stack
- Kivy, Flask, Flask-SQLAlchemy, QRCode, Pillow.
- SQLite DB (`currency.db`).
- Android Permissions: INTERNET, WIFI_STATE, FOREGROUND_SERVICE.

### Files to Fix
1.  `main.py`: `get_ip()` -> return actual Wi-Fi LAN IP on Android.
2.  `app.py`: `get_local_ip()` -> same logic, avoid `hostname -I` on Android.

### Next Steps
1.  Fix IP detection (use `pyjnius` for Android Wi-Fi IP).
2.  Add logging to verify IP.
3.  Test locally and via `curl`.
4.  Rebuild APK (`buildozer android debug`) and test.