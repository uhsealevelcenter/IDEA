#!/bin/bash

# Create required directories
mkdir -p /app/data/benchmarks
mkdir -p /app/data/metadata
mkdir -p /app/data/altimetry
mkdir -p /app/data/papers
mkdir -p /app/static

# Set permissions
chmod 755 /app/data

# Make fetch script executable if it exists
if [ -f /app/scripts/fetch_data.sh ]; then
    chmod +x /app/scripts/fetch_data.sh
fi

# Run database initialization (migrations and initial data)
echo "Running database initialization..."
bash prestart.sh

# Execute the main command
exec "$@"



