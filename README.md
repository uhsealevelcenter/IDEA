# Intelligent Data Exploring Assistant (IDEA)

## Disclaimer

**Warning:** This project allows an LLM to execute code and perform actions on the host machine, which can be potentially destructive and dangerous. Use this software at your own risk. The authors and contributors of this project are not responsible for any damage or data loss that may occur from using this software. It is strongly recommended to run this software in a controlled environment, such as a virtual machine or a container, to mitigate potential risks.

## Overview

This is a stripped down version of the [Station Explorer Assistant (SEA) project](https://github.com/uhsealevelcenter/slassi).
The core of it is OpenAI's GPT-4o model that runs a local code interpreter using [OpenInterpreter](https://github.com/OpenInterpreter/open-interpreter). It is essentially a web interface to the OpenInterpreter code interpreter.

![IDEA](https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExdDlpeXUzcTNuZjN0eTZjaGd2YmFwYXVhejBiZGhjZ25sbnJsbGk5NSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/ZqE51jnzWAFBCZBRUM/giphy.gif)
![IDEA2](https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExNHhscGFraWFpbzExcnN1NG01bG0zNGMxendnMjFrbWU4YWM1MWx4OCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/tuv0NGNP9mhsmhsTad/giphy.gif)

## Important Note About Usage

This is a single-user development tool, not a production-ready chat application. It is designed to run locally as a personal assistant and lacks features typically found in production systems such as:

- Multi-user support
- Authentication/Authorization
- Conversation history persistence
- Security guardrails
- Production-level error handling
- Enterprise support

**Security Warning:** This tool is intended for local development use only. Running it on a public server is strongly discouraged as it lacks the necessary security features. While the Docker container provides some isolation, it should not be considered a complete security solution.

This project serves as a starting point for developers looking to build their own AI-powered tools, but it requires significant additional development to be production-ready.

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
- **Frontend:** A static server (using Python's `http.server`) running on port **8000**. Useful for direct access and testing. http://localhost
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


## PaperQA2

- **Data Directory:** Contains subdirectories for benchmarks, metadata, altimetry, and papers. papers is the directory containing the peer reviewed papers that are indexed by PaperQA2. 
When develping locally, you can simply add new publications to the `data/papers` directory and newly added PDFs will be automatically indexed upon first relevant question that invokes the use of the `pqa` command (e.g. asking the AI to perform literature review).
- **Note**: In production, you cannot simply copy the data to `data/papers` on your local machine because that directory is not mounted in the container in production. You would have to copy the data to the production server and then copy the data directly to the container at the same location (e.g. `/app/data/papers`).

The settings for PaperQA2 indexing are in `data/.pqa/settings/my_fast.json` and `data/.pqa/settings/pqa_settings.py`. These files define the model and parameters used to index the papers. You can change the settings to use a different model or different parameters. And then in `custom_instructions.py`, you can change the system prompt to use the new settings (e.g. `my_fast` or `pqa_settings`).

## Note about the System Prompt

To replicate our results for the Mars InSight mission from our paper named Building an intelligent data exploring assistant for geoscientists, you must use the `system_prompt_InSight.py` file as your system prompt. To do that, you need to change the import in `app.py` from `from utils.system_prompt import sys_prompt` to `from utils.system_prompt_InSight import sys_prompt`.

## Environment Variables

The project behavior is controlled by several environment variables in the `.env` file:

This one is a secret and it should live in your .env file (not checked to the repo)
- `OPENAI_API_KEY`: Your API key provided by OpenAI.

These are not secrets but simply settings. You can define these in your docker-compose file
- `LOCAL_DEV`: Set to `1` for local development mode; set to `0` for production.
- `PQA_HOME`: Path to store Paper-QA settings, typically `/app/data`.
- `PAPER_DIRECTORY`: Path to the papers directory, typically `/app/data/papers`.

## Docker & Container Details

- **Dockerfile:** Uses multi-stage builds to install dependencies in a virtual environment and then copies only the necessary runtime files.
- **Volumes:** Ensure persistence—`persistent_data` for production and local bind-mounts (such as `./frontend` to `/app/frontend`) for rapid development.
- **NGINX Container:** Serves static files and acts as a reverse proxy on port 80. Its configuration is contained in `nginx.conf`. This is only used for local development and is set to mimic production.

## Contributing

Contributions, issue reports, and feature requests are welcome! Please open an issue or a pull request with your changes.

## License

This project is licensed under the [MIT License](LICENSE).
