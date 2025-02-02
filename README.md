# Station Explorer Assistant (SEA)

## Overview

The Station Explorer Assistant (SEA), allows researchers to ask questions in everyday language and receive clear explanations and data analyses in response. SEA uses artificial intelligence (AI) to analyze sea level data, compare water levels to normal conditions, and predict potential flooding, drawing on the UHSLC’s extensive database. It even writes and runs its own analysis software to ensure its results are accurate. The core of it is OpenAI's GPT-4o model that runs code locally using [OpenInterpreter](https://github.com/OpenInterpreter/open-interpreter).

## Features

- **Data Exploration:** Easily search and filter data by tide gauge station, time range, and other parameters.
- **Data Visualization:** Generate plots and tables to visualize results.
- **Data Download:** Export data in any format for further study.
- **Data Analysis:** Automatically run analysis routines to generate and validate results.
- **Data Upload:** Upload data files to the SEA for analysis.
- **Literature Review:** Search for relevant literature to answer questions using [PaperQA2](https://github.com/Future-House/paper-qa)

## Prerequisites

- **Docker & Docker Compose:** Ensure Docker is installed on your system.
- **OpenAI API Key:** You need an API key from OpenAI.
- **Unix/Linux Environment:** The provided bash scripts are Unix/Linux based (Windows support can be added in the future).
- **Data Files:** Large datasets (benchmarks and sea level data) must be downloaded separately.

## Getting Started Locally

### 1. Clone the Repository

```bash
git clone https://github.com/uhsealevelcenter/slassi.git
cd slassi
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

For local development, the repository includes a Docker Compose file (`docker-compose-local.yml`) and a helper script (`restart_local.sh`). This setup supports live code reloading on the backend and mounts the source for immediate feedback (any code changes on the backend will be reflected immediately).

Run the helper script:
```bash
./restart_local.sh
```


This script will:
1. Stop any running Docker containers defined in `docker-compose-local.yml`.
2. Build and start the containers (including backend, frontend, nginx, and redis).
3. Tail the logs of the backend container for quick debugging.

Note: The first time you run this, it will take a while because it has to download the docker image and install the dependencies.

#### **Local Services Breakdown:**

- **Backend (web):** Runs the API with hot-reload enabled (`uvicorn app:app --reload`). Accessible at `http://localhost:8001`.
- **Frontend:** A static server (using Python’s `http.server`) running on port **8000**. Useful for direct access and testing.
- **NGINX:** Reverse-proxy and static file server available on port **80**.
- **Redis:** In-memory store for caching, running on port **6379**.

#### **Handling CORS Issues**

When working with the frontend, you might encounter CORS issues because the frontend retrieves station data from the backend.

- **Safari:** 
  - Go to `Safari` > `Preferences` > `Advanced` and check **"Show Develop menu in menu bar"**.
  - Then in the `Develop` menu, select **"Disable cross-origin restrictions"**.
  
- **Chrome:**
  - Refer to [this guide](https://stackoverflow.com/questions/3102819/disable-same-origin-policy-in-chrome) or install the [Allow CORS: Access-Control-Allow-Origin extension](https://chromewebstore.google.com/detail/allow-cors-access-control/lhobafahddgcelffkeicbaginigeejlf?utm_source=ext_app_menu).

### 4. Local Data Setup

Most of the data is stored in the `data` directory. For local development:

- Make sure that the docker container is running. The following command should return a list of running containers:
```bash
docker ps
```
There should be a container named `SEA_container`. 

Run the following scripr to download the data (you must be in root of the repo and have python installed):
```bash
./scripts/fetch_data.sh
```

### 5. That's it!

You should now be able to run the SEA locally and make changes to the code. Visit [http://localhost](http://localhost) to interact with the SEA.

## Deploying to Production

The production setup uses a separate Docker Compose configuration (`docker-compose.yml`) along with the `start_server.sh` script.

### Backend Deployment

1. **Push Your Changes:** Make sure that all backend changes are committed and pushed to the `main` branch.
2. **Access the Server:** Log in to the production server (e.g., `kaana`).
3. **Navigate to the Project Directory:**  
   ```bash
   cd /srv/apps/slassi
   ```
4. **Pull Latest Changes:**  
   ```bash
   git pull origin main
   ```
5. **Start the Server:** Execute the startup script:
   ```bash
   ./start_server.sh
   ```
   
The `start_server.sh` script will:
- Stop any running services defined in `docker-compose.yml`.
- Build and run the new containers in detached mode.


### Frontend Deployment

Frontend deployment is handled manually (until we set up CI/CD):

1. **Push Changes:** Commit and push any updates from the `frontend` directory.
2. **Log in to the Server:** Access the production server (e.g., `kaana` or `wyrtky`).
3. **Copy Files:** Copy the contents of the `frontend` directory from the main branch to:
   ```
   /srv/htdocs/uhslc.soest.hawaii.edu/research/SEA
   ```
4. Ensure that the `config.js` file exists and environment is set to "production" (this will already be the case because it is already set up but leaving this here for clarity in case we ever need to set it up from scratch).
   > **Note:** In the future, the `start_server.sh` script may be enhanced to automate frontend deployment.


## Project Structure
```
.
├── app.py # Main application entry point (backend)
├── Dockerfile # Docker container build configuration
├── docker-compose.yml # Production Docker Compose configuration
├── docker-compose-local.yml # Local Docker Compose configuration
├── restart_local.sh # Local development startup script
├── start_server.sh # Production deployment script for the backend
├── requirements.txt # Python dependencies
├── data/ # Directory storing datasets, benchmarks, and additional data
├── frontend/ # Frontend static assets (HTML, CSS, JS)
├── nginx.conf # NGINX configuration for reverse proxy and static files, used only for local development and set to mimic production
└── utils/
    └── system_prompt.py # Configuration file for the system prompt (LLM)
    └── custom_instructions.py # Configuration file for the custom instructions (LLM)
```


## Data Management and PaperQA2

- **Data Directory:** Contains subdirectories for benchmarks, metadata, altimetry, and papers. papers is the directory containing the peer reviewed papers that are indexed by PaperQA2.
- **Note**: The data has to be copied into the docker container from the production server. This is done in the `fetch_data.sh` script. You cannot simply copy the data to `data/papers` because the docker container needs to be running to do this. You can study the script to see how it is done and can use the same approach to copy new PDFs to the `papers` directory in the container. Newly added PDFs will be automatically indexed. The settings for PaperQA2 indexing are in `data/.pqa/settings/my_fast.json`.

## Environment Variables

The project behavior is controlled by several environment variables in the `.env` file:

- `OPENAI_API_KEY`: Your API key provided by OpenAI.
- `LOCAL_DEV`: Set to `1` for local development mode; set to `0` for production.
- `PQA_HOME`: Path to store Paper-QA settings, typically `/app/data`.
- `PAPER_DIRECTORY`: Path to the papers directory, typically `/app/data/papers`.

## Docker & Container Details

- **Dockerfile:** Uses multi-stage builds to install dependencies in a virtual environment and then copies only the necessary runtime files.
- **Volumes:** Ensure persistence—`persistent_data` for production and local bind-mounts (such as `./frontend` to `/app/frontend`) for rapid development.
- **NGINX Container:** Serves static files and acts as a reverse proxy on port 80. Its configuration is contained in `nginx.conf`. This is only used for local development and is set to mimic production.

## Troubleshooting

- **Container Startup Issues:**  
  If `restart_local.sh` fails to locate a container for the image `slassi`, confirm that the build completed successfully. Remove unused images or rebuild with docker pruning if necessary.
  
- **CORS Errors:**  
  Follow the browser-specific instructions above to disable cross-origin restrictions during development.
  
- **Data-Related Problems:**  
  Ensure that data directories exist and data is correctly downloaded or generated. Check container logs for more details.
  
- **Performance and Caching:**  
  Docker’s caching mechanisms speed up dependency installations. If you update `requirements.txt`, consider rebuilding without cache if issues arise.

## Contributing

Contributions, issue reports, and feature requests are welcome! Please open an issue or a pull request with your changes.

## License

This project is licensed under the [MIT License](LICENSE).
