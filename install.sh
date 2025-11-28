#!/bin/bash

# Global variables for installation path and service name
INSTALL_DIR="/opt/babelmate"
SERVICE_NAME="babelmate"

echo "--- Starting BabelMate Installation (Root Mode) ---"

# 1. Root Check
if [ "$(id -u)" != "0" ]; then
   echo "Error: This script must be run as root or with sudo."
   exit 1
fi

# 2. Install Dependencies (Attempting to support major distros)
echo "Installing python3-venv..."
if command -v apt &> /dev/null; then
    apt update && apt install python3-venv -y
elif command -v dnf &> /dev/null; then
    dnf install python3-venv -y
elif command -v yum &> /dev/null; then
    yum install python3-venv -y
else
    echo "Warning: Venv package not found via apt/dnf/yum. Proceeding..."
fi

# 3. Setup Project Directory and Copy Files
echo "Creating installation directory and copying files to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
# Copy the required files from the current directory
cp bot.py requirements.txt config.json "${INSTALL_DIR}/"

# 4. Create and Setup Virtual Environment
cd "${INSTALL_DIR}" || { echo "Error: Failed to enter installation directory."; exit 1; }
echo "Setting up virtual environment and installing Python packages..."

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# 5. Create Systemd Service File (Directly writing to /etc/systemd/system/babelmate.service)
echo "Creating Systemd service file: /etc/systemd/system/${SERVICE_NAME}.service"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<- EOF
[Unit]
Description=BabelMate Translator Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/bot.py
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 6. Enable and Start the Service
echo "Reloading systemd daemon and starting the service..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"


echo "--------------------------------------------------------"
echo "✅ Installation complete."
echo "you need to add the google credentials file specified in config.json to ${INSTALL_DIR} before starting the bot."
echo "View logs with: journalctl -xeu ${SERVICE_NAME}.service"
echo "run bot with : systemctl start "${SERVICE_NAME}.service"
echo "--------------------------------------------------------"