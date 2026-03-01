#!/usr/bin/env bash
set -euo pipefail

# Vidtools deployment setup script for Ubuntu
# Run as root or with sudo

INSTALL_DIR="/opt/vidtools"
DATA_DIR="${INSTALL_DIR}/data"
CONFIG_DIR="${INSTALL_DIR}/config"
SERVICE_USER="vidtools"

echo "=== Vidtools Setup ==="
echo ""

# Check running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: Run this script as root (sudo ./setup.sh)"
    exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq ffmpeg python3 python3-venv curl

# Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# Create service user
if ! id "${SERVICE_USER}" &>/dev/null; then
    echo "Creating user: ${SERVICE_USER}"
    useradd --system --shell /usr/sbin/nologin --home-dir "${INSTALL_DIR}" "${SERVICE_USER}"
fi

# Create directories
echo "Creating directories..."
mkdir -p "${INSTALL_DIR}" "${DATA_DIR}/uploads" "${DATA_DIR}/output" "${DATA_DIR}/temp" "${CONFIG_DIR}"

# Copy application files
echo "Installing application..."
if [ -d ".git" ] || [ -f "pyproject.toml" ]; then
    # Running from source directory
    cp -r vidtools/ "${INSTALL_DIR}/vidtools/"
    cp pyproject.toml "${INSTALL_DIR}/"
    cp -r presets/ "${INSTALL_DIR}/presets/" 2>/dev/null || true
else
    echo "Error: Run this script from the vidtools source directory"
    exit 1
fi

# Create venv and install
echo "Setting up Python environment..."
cd "${INSTALL_DIR}"
uv venv .venv
uv sync --no-dev

# Set ownership
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

# Install systemd service
echo "Installing systemd service..."
cp deploy/vidtools.service /etc/systemd/system/vidtools.service 2>/dev/null || \
    cp "${OLDPWD}/deploy/vidtools.service" /etc/systemd/system/vidtools.service

systemctl daemon-reload
systemctl enable vidtools

# Password setup prompt
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Start the service:"
echo "  sudo systemctl start vidtools"
echo ""
echo "View logs:"
echo "  sudo journalctl -u vidtools -f"
echo ""
echo "Access the web UI at:"
echo "  http://<server-ip>:8910"
echo ""
echo "On first visit, you will be prompted to set a password."
echo ""
