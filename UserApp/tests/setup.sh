#!/bin/bash
set -e

# Setup test environment by creating users
# Usage: ./setup.sh

BASE_URL="http://localhost:80"

echo "ðŸ§ª Setting up test environment..."
echo "ðŸ“ Target: $BASE_URL"
echo ""

# Check if API is reachable
if ! curl -s -f "$BASE_URL/api/v1/health-inputs" > /dev/null 2>&1; then
    echo "âš ï¸  Warning: API not yet responding at $BASE_URL"
    echo "   Make sure Docker containers are running:"
    echo "   docker compose up -d"
    echo ""
fi

# Create users via admin CLI
cd "$(dirname "$0")/.."

echo "Creating test users..."

# Function to create user with proper error handling
create_user() {
    local email="$1"
    local password="$2"
    local name="$3"

    output=$(python3 admin.py provision-user "$email" "$password" "$name" 2>&1)
    exit_code=$?

    if [ $exit_code -eq 0 ]; then
        echo "  âœ“ Created: $email"
    elif echo "$output" | grep -q "already registered"; then
        echo "  âš ï¸  Already exists: $email"
    else
        echo "  âœ— ERROR creating $email:"
        echo "$output" | sed 's/^/    /'
        return 1
    fi
}

create_user test@example.com password "Test User"
create_user owner1@test.local Owner1Pass123! "Owner One"
create_user owner2@test.local Owner2Pass123! "Owner Two"
create_user owner3@test.local Owner3Pass123! "Owner Three"

echo ""
echo "âœ… Test environment ready!"
echo ""
echo "Next: Run tests with:"
echo "  pytest tests/ -v"
