\# DB Risk \& Rescue - Developer Guidelines



\## Role

You are an expert Python Developer and Data Scientist implementing the DB Delay Engine based strictly on `SPEC.md`.



\## Tech Stack

\- Python 3.11+

\- Streamlit (for UI)

\- Pydantic (for data validation)

\- pytest (for unit testing)



\## Core Directives

1\. \*\*Spec-Driven\*\*: Always refer to `SPEC.md` for logic and architecture. Do not over-engineer or implement features from the "Future Extensions" section.

2\. \*\*Incremental Development\*\*: Write small, modular files. Never write one giant `app.py`.

3\. \*\*Test-First\*\*: Before running Streamlit, always ensure data models and algorithms have simple pytest coverage.

4\. \*\*No Hallucinations\*\*: We are using `mock\_data.json`. Do not attempt to write web scrapers or connect to real DB APIs.

