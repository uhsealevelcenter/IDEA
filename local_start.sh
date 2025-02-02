#!/bin/bash

# Step 1: Bring down the Docker Compose stack
echo "Stopping Docker Compose stack..."
docker compose -f docker-compose-local.yml down -v

# Step 2: Bring up the Docker Compose stack in detached mode
echo "Starting Docker Compose stack..."
docker compose -f docker-compose-local.yml up -d --build

# Step 3: Tail the logs for a specific container
# Replace 'IDEA_web' with your actual image or service name from the docker-compose.yml
container_id=$(docker ps -qf "name=IDEA_container")

if [ -z "$container_id" ]; then
  echo "Error: No container found for the image 'IDEA'."
  exit 1
fi

# Step 4: Tail the logs for the identified container
echo "Tailing logs for container ID: $container_id"
docker logs -f "$container_id"