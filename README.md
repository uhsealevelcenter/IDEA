# Intelligent Data Exploring Assistant (IDEA)

## Overview

This is a stripped down version of the [Station Explorer Assistant (SEA) project](https://github.com/uhsealevelcenter/slassi).
The core of it is OpenAI's GPT-4o model that runs a local code interpreter using [OpenInterpreter](https://github.com/OpenInterpreter/open-interpreter). It is essentially a web interface to the OpenInterpreter code interpreter.

![IDEA](https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExdDlpeXUzcTNuZjN0eTZjaGd2YmFwYXVhejBiZGhjZ25sbnJsbGk5NSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/ZqE51jnzWAFBCZBRUM/giphy.gif)
![IDEA2](https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExNHhscGFraWFpbzExcnN1NG01bG0zNGMxendnMjFrbWU4YWM1MWx4OCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/tuv0NGNP9mhsmhsTad/giphy.gif)

## What this project is NOT
This is not a full-fledged chat assistant. Ther is no notion of users, authenticaton or authorization. The conversation history is not stored. In it is current form, 
it is primarily a tool to be used as a personal assistant, served locally on a personal computer. There are no guardrails
to prevent malicious use (you should not run this on a public server). But it is a good starting point for building your own chat assistant 
and it runs in a docker container which prevents you from messing up your system.

## Features

- **Data Exploration:** Easily search and filter any data
- **Data Visualization:** Generate plots and tables to visualize results.
- **Data Download:** Export data in any format for further study.
- **Data Analysis:** Automatically run analysis routines to generate and validate results.
- **Data Upload:** Upload data files for analysis.

## Prerequisites

- **Docker & Docker Compose:** Ensure Docker is installed on your system.
- **OpenAI API Key:** You need an API key from OpenAI.

## Getting Started Locally

### 1. Clone the Repository

```bash
git clone https://github.com/uhsealevelcenter/IDEA.git
cd IDEA
```

### 2. Set Up Environment Variables

Create a `.env` file in the project root. You have two options:

- **Option A:** Rename the provided `example.env` to `.env` and add your OpenAI API key:
  ```ini
  OPENAI_API_KEY=YOUR_API_KEY_HERE
  ```
- **Option B:** Manually create a `.env` file with the necessary variables.

### 3. Prepare the Frontend Configuration

Inside the `frontend` directory, create a `config.js` file. You can either copy from `config.example.js` or create one manually. (This file does not contain any secrets; it simply sets environment parameters.) This file is not checked in to the repo to avoid confusion with the production environment. It is a hacky solution but it works for now. The most important thing is that on the actual production server, the environment field in the config.js file is set to "production" and local is set to "local".

### 4. Start the Local Environment

For local development, the repository includes a Docker Compose file (`docker-compose-local.yml`) and a helper script (`local_start.sh`). This setup supports live code reloading on the backend and mounts the source for immediate feedback (any code changes on the backend will be reflected immediately).

Run the helper script:
```bash
./local_start.sh
```


This script will:
1. Stop any running Docker containers defined in `docker-compose-local.yml`.
2. Build and start the containers (including backend, frontend, nginx, and redis).
3. Tail the logs of the backend container for quick debugging.

Note: The first time you run this, it will take a while because it has to download the docker image and install the dependencies.

#### **Local Services Breakdown:**

- **Backend (web):** Runs the API with hot-reload enabled (`uvicorn app:app --reload`).
- **Frontend:** A static server (using Python’s `http.server`) running on port **8000**. Useful for direct access and testing. http://localhost
- **NGINX:** Reverse-proxy and static file server available on port **80**.
- **Redis:** In-memory store for caching, running on port **6379**.

### 5. That's it!

You should now be able to run the SEA locally and make changes to the code. Visit [http://localhost](http://localhost) to interact with the SEA.

## Deploying to Production

The production setup uses a separate Docker Compose configuration (`docker-compose.yml`) along with the `production_start.sh` script.

   
The `production_start.sh` script will:
- Stop any running services defined in `docker-compose.yml`.
- Build and run the new containers in detached mode.


## Project Structure
```
.
├── app.py # Main application entry point (backend)
├── Dockerfile # Docker container build configuration
├── docker-compose.yml # Production Docker Compose configuration
├── docker-compose-local.yml # Local Docker Compose configuration
├── local_start.sh # Local development startup script
├── production_start.sh # Production deployment script for the backend
├── requirements.txt # Python dependencies
├── data/ # Directory storing datasets, benchmarks, and additional data
├── frontend/ # Frontend static assets (HTML, CSS, JS)
├── nginx.conf # NGINX configuration for reverse proxy and static files, used only for local development and set to mimic production
└── utils/
    └── system_prompt.py # Configuration file for the system prompt (LLM)
```

system_prompt.py is the system prompt for the LLM. It is used to set the behavior of the LLM. It is probably the most important file in the project. You can alter the behavior of the LLM by editing this file and adjust it to your own needs.

## Environment Variables

The project behavior is controlled by several environment variables in the `.env` file:

- `OPENAI_API_KEY`: Your API key provided by OpenAI.
- `LOCAL_DEV`: Set to `1` for local development mode; set to `0` for production.

## Docker & Container Details

- **Dockerfile:** Uses multi-stage builds to install dependencies in a virtual environment and then copies only the necessary runtime files.
- **Volumes:** Ensure persistence—`persistent_data` for production and local bind-mounts (such as `./frontend` to `/app/frontend`) for rapid development.
- **NGINX Container:** Serves static files and acts as a reverse proxy on port 80. Its configuration is contained in `nginx.conf`. This is only used for local development and is set to mimic production.

## Contributing

Contributions, issue reports, and feature requests are welcome! Please open an issue or a pull request with your changes.

## License

This project is licensed under the [MIT License](LICENSE).
