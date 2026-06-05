# README: Docker Shutdown in WSL

This guide provides simple steps to stop Docker containers and restart the Docker service in Windows Subsystem for Linux (WSL).

## 1. Stop a Running Container
To stop a specific Docker container, first, find its ID or name:

```sh
docker ps
```

Then, stop it using:

```sh
docker stop <container_id_or_name>
```

## 2. Stop All Running Containers
To stop all running containers at once:

```sh
docker stop $(docker ps -q)
```

## 3. Force Kill a Container
If a container does not stop normally, you can forcefully terminate it:

```sh
docker kill <container_id_or_name>
```

## 4. Restart the Docker Service
If Docker becomes unresponsive, restart the service inside WSL:

```sh
sudo service docker restart
```

Alternatively, restart WSL from PowerShell:

```powershell
wsl --shutdown
```
Then restart Docker Desktop.

## 5. Verify Docker is Running Again
After restarting, ensure Docker is running:

```sh
docker ps
```

If you encounter issues, try restarting Docker Desktop or your system.