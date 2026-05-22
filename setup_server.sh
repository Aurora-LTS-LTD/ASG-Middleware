#!/bin/bash
# ===========================================
# ASG-Solutions — Parrot OS Server Setup
# Run this on the Alienware (10.0.0.2)
# ===========================================

set -e  # Stop immediately if any command fails

echo "=============================="
echo "  ASG-Solutions Server Setup"
echo "=============================="

# Step 1: Create project directory
echo ""
echo "[1/5] Creating project directory at ~/asg_platform..."
mkdir -p ~/asg_platform
cd ~/asg_platform
echo "  ✓ Directory created: ~/asg_platform"

# Step 2: Create folder structure
echo ""
echo "[2/5] Creating folder structure..."
mkdir -p app/routers
mkdir -p app/services
mkdir -p app/database
mkdir -p app/static
touch app/__init__.py
touch app/routers/__init__.py
touch app/services/__init__.py
touch app/database/__init__.py
echo "  ✓ Folders created:"
echo "    app/routers/    — endpoint handlers (WhatsApp, Telegram, etc.)"
echo "    app/services/   — business logic (Make.com, tax, ITA)"
echo "    app/database/   — models and DB connection"
echo "    app/static/     — HTML dashboards, CSS, JS"

# Step 3: Set up Python virtual environment
echo ""
echo "[3/5] Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
echo "  ✓ Virtual environment created and activated"

# Step 4: Install dependencies
echo ""
echo "[4/5] Installing dependencies..."
pip install --upgrade pip
pip install fastapi uvicorn[standard] sqlalchemy pydantic requests python-multipart python-dotenv httpx
echo "  ✓ All dependencies installed"

# Step 5: Create .env file
echo ""
echo "[5/5] Creating .env configuration file..."
cat > .env << 'EOF'
# ASG-Solutions Server Configuration
HOST=0.0.0.0
PORT=8000

# Future: add these when ready
# WHATSAPP_VERIFY_TOKEN=
# WHATSAPP_ACCESS_TOKEN=
# MAKE_WEBHOOK_URL=
# DATABASE_URL=sqlite:///./asg_platform.db
EOF
echo "  ✓ .env file created"

# Final summary
echo ""
echo "=============================="
echo "  Setup Complete!"
echo "=============================="
echo ""
echo "  Project:  ~/asg_platform"
echo "  Python:   $(python3 --version)"
echo "  Packages: fastapi, uvicorn, sqlalchemy, pydantic,"
echo "            requests, python-multipart, python-dotenv, httpx"
echo ""
echo "  Structure:"
find app -type f -o -type d | sort | head -20
echo ""
echo "  To start working:"
echo "    cd ~/asg_platform"
echo "    source venv/bin/activate"
echo ""
