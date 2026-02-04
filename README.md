# Intelligent Data Exploring Assistant (IDEA)

IDEA is a tool-using AI assistant for scientific data exploration. It is designed to help researchers go from question to analysis and figures quickly while keeping results transparent and reproducible. IDEA is a framework for building domain-focused assistants that run code, generate plots, save outputs, work directly with uploaded datasets, and pull data from the web via its internet-connected environment.

## IDEA vs. SEA

- **IDEA** is the general-purpose framework for creating and working with custom data analysis assistants.
- **SEA (Station Explorer Assistant)** is a special-purpose IDEA configured for sea level data analysis.

**Web access:**
- **SEA (no login required):** https://uhslc.soest.hawaii.edu/research/SEA
- **IDEA (login required):** https://uhslc.soest.hawaii.edu/research/IDEA
- **Account requests:** idea-dev-grp@hawaii.edu

https://github.com/user-attachments/assets/7bea7a70-b72b-484a-a75f-f466cd547e7c

## Why IDEA (vs. a chat-only assistant)

IDEA is action-oriented. It can execute code, inspect data, and produce artifacts you can download. Results are backed by runnable code and intermediate outputs, which supports scientific transparency and reproducibility.

## Core Capabilities

- **Data ingestion:** Load CSV, NetCDF, text, and other common formats; summarize variables, dimensions, ranges, and missingness.
- **Exploratory analysis:** Time series resampling, anomalies, seasonal cycles, trend estimates, and comparisons across stations or regions.
- **Visualization:** Publication-ready plots, quick-look figures, and exportable figure packs.
- **Mapping:** Interactive maps (folium) and static maps (matplotlib/cartopy).
- **Domain workflows:** Sea level and tide-gauge analysis, station lookup, extremes, trends, and climate index context (e.g., El Niño-Southern Oscillation).
- **Reproducible outputs:** Saved plots, tables, and derived datasets with traceable steps.
- **Literature RAG:** Optional literature review using [PaperQA2](https://github.com/Future-House/paper-qa), with locally indexed PDFs for retrieval-augmented answers (via user uploads to their Knowledge base in IDEA or a limited archive of journal articles in SEA).

<p align="center">
  <img src="https://uhslc.soest.hawaii.edu/research/SEAinfo/EngineeringSchematic_details.png" alt="IDEAschematic_details" width="600" />
</p>
Engineering plan of IDEA. Figure 1 from: Widlansky, M. J., & Komar, N. (2025). Building an intelligent data exploring assistant for geoscientists. *JGR: Machine Learning and Computation*, 2, e2025JH000649. https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2025JH000649

## Example Workflow
1) Suggest a topic and ask IDEA to show you how it can help.
2) Propose a research direction, or let IDEA guide you.
3) Check methods and results carefully, and ask for clarification or revision when necessary.
[Sample conversation with IDEA correcting its mistake](https://uhslc.soest.hawaii.edu/idea-api/share/fAIaXflp1JrC_7lttLhdactuoxvxEUBWQHZYlCmAowY)

### Prompting ideas
- “I uploaded a NetCDF—what’s inside?”
- “Plot monthly mean sea level for Honolulu and compare to an El Niño index.”
- “Analyze trends and extremes in the time series.”
- “Generate a self-contained web page showing the methods and results of this analysis.”

## Build Your Own IDEA

IDEA is built to be customized. You can tailor behavior by adding domain instructions, preferred methods, and datasets. The **Instructions** panel enables:

- Custom roles (e.g., “Station Explorer Assistant (SEA)” for analyzing tide gauge data)
- Standardized lab workflows and QA/QC rules
- Consistent output styles across a team
- Reuse of local knowledge and reference datasets via online sources or upload

## How It Works (Conceptual)

IDEA combines:

- A conversational interface with a multimodal large language model (e.g., gpt-5.2 from OpenAI; AI model updates to the latest state-of-the-art)
- Information and data context (provide custom "Instruction" manuals, "Knowledge" documents, and Data files)
- Tool use for real actions (file I/O, code execution, plotting, and reporting)
- Human-driven and reproducible science workflows (code reviews and "Conversation" sharing)

Internally, IDEA uses Open Interpreter to execute Python and system commands in a controlled environment (https://github.com/openinterpreter/open-interpreter). This means results are inspectable and reproducible rather than “black box” outputs.

## Limitations and Scientific Caution

IDEA is powerful but not infallible. It can:

- Misinterpret ambiguous requests
- Choose suboptimal methods if assumptions are unclear [Example](https://uhslc.soest.hawaii.edu/idea-api/share/fAIaXflp1JrC_7lttLhdactuoxvxEUBWQHZYlCmAowY)
- Produce results that require domain judgment to validate

Always verify critical results, especially for publication or operational decisions. For example, when conducting a sea level analysis, be mindful of datum shifts, QC flags, record length, and local effects (subsidence/uplift). When necessary, prompt IDEA to check its work.

## Getting Started Locally (requires Docker)

### 1. Clone the Repository

```bash
git clone https://github.com/uhsealevelcenter/IDEA.git
cd IDEA
```

### 2. Configure Environment Variables

Create a `.env` file in the project root (copy `example.env` and edit values). At minimum you should set:

```ini
OPENAI_API_KEY=YOUR_API_KEY_HERE
POSTGRES_DB=idea_db
POSTGRES_USER=idea_user
POSTGRES_PASSWORD=change_this
SECRET_KEY=change_this
FIRST_SUPERUSER=admin@idea.com
FIRST_SUPERUSER_PASSWORD=change_this
```

IDEA has been tested with several LLM inference providers, including OpenAI (https://platform.openai.com/), Anthropic (https://claude.com/platform/api), and Jetstream2 (https://docs.jetstream-cloud.org/inference-service/overview/).

### 3. Configure the Frontend

If `frontend/config.js` does not exist, copy `frontend/config.example.js` to `frontend/config.js` and edit as needed. This file contains environment parameters and no secrets.

### 4. Start Local Services

Run:

```bash
./local_start.sh
```

This uses `docker compose` with `docker-compose.yml` plus `docker-compose.override.yml` to enable live reload and local mounts.

### 5. Access the App

- Main app: http://localhost
- Login: http://localhost/login.html

Login with the credentials from your `.env` file.

## Deploying to Production (requires Docker)

Use:

```bash
./production_start.sh
```

This runs `docker compose -f docker-compose.yml` to build and start the production services.

### Security and Deployment Notes

- **Code execution:** IDEA allows an AI model to generate computer code that executes in the environment where IDEA is installed.
- **Local development (`./local_start.sh`):** Intended for **single-user development**. The local Docker configuration bind-mounts the project directory for live reload, which means the model can read/write parts of the host filesystem.
- **Production (`./production_start.sh`):** Intended for **multi-user deployment** using the production Docker stack.
- **Isolation tip (production):** Keep the IDEA compute container isolated from the front-end web interface. A common pattern is to run the web UI on a separate host or network segment and only expose the backend API through a controlled reverse proxy.

Docker provides isolation, but it is not a complete security solution for sensitive environments. Treat the IDEA compute container as an execution environment and design your deployment accordingly.

## Project Structure

```
.
├── app.py                         # FastAPI backend and Open Interpreter integration
├── auth.py                        # Authentication utilities
├── Dockerfile                     # Container build configuration
├── docker-compose.yml             # Production Docker Compose configuration
├── docker-compose.override.yml    # Local development overrides
├── local_start.sh                 # Local development startup
├── production_start.sh            # Production startup
├── requirements.txt               # Python dependencies
├── data/                          # Datasets, benchmarks, metadata, papers
├── frontend/                      # Static frontend assets (HTML/CSS/JS)
├── nginx.conf                     # Local dev reverse-proxy/static server config
├── static/                        # User artifacts and generated outputs
└── utils/                          
    └── system_prompt.py           # System prompt for the assistant
```

## Citation

Widlansky, M. J., & Komar, N. (2025). Building an intelligent data exploring assistant for geoscientists. *JGR: Machine Learning and Computation*, 2, e2025JH000649. https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2025JH000649

## Contributing

Contributions, issue reports, and feature requests are welcome! Please open an issue or a pull request with your changes. General feedback or questions can be emailed to idea-dev-grp@hawaii.edu

## Release

![image](https://github.com/user-attachments/assets/4fe5d3e7-5c1a-4fcd-9274-998e841fb860)

Prototype (v0.1.0) https://doi.org/10.5281/zenodo.15605301

## License

This project is licensed under the MIT License. See `LICENSE`.
