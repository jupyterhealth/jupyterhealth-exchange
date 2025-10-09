# PostgreSQL MCP for JHE (Local)

This guide shows how to run the **PostgreSQL MCP server** locally against your **JHE** database and connect it to **Claude Desktop** so you can query JHE data in natural language.

## Prerequisites

* JHE database is **migrated** and **seeded**
* PostgreSQL reachable locally (e.g., `localhost:5432`)
* Claude Desktop installed

---

## 1) Clone & Set Up the MCP Server

```bash
git clone https://github.com/gldc/mcp-postgres.git
cd mcp-postgres

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## 2) Run the Server (stdio transport)

Use your JHE connection string. Example:

```bash
python postgres_server.py --conn "postgresql://username:password@host:port/database"
```

**Example (your local values):**

```bash
python postgres_server.py --conn "postgresql://jhedbuser:strongpassword@localhost:5432/jhemcpdb"
```


---

## 3) Register the Server in Claude Desktop

1. Open **Claude Desktop**
2. Go to **Settings → Developer** (or MCP/Extensions)
3. Click **Edit config**
4. Paste the config below and update **paths** & **connection string** if yours differ
5. Save & **restart** Claude Desktop

```json
{
  "mcpServers": {
    "jhe-postgres": {
      "command": "/ABS/PATH/TO/mcp-postgres/venv/bin/python",
      "args": [
        "/ABS/PATH/TO/mcp-postgres/postgres_server.py"
      ],
      "env": {
        "PYTHONUNBUFFERED": "1",
        "POSTGRES_CONNECTION_STRING": "postgresql://username:password@localhost:5432/jhe",
        "POSTGRES_READONLY": "true",
        "POSTGRES_STATEMENT_TIMEOUT_MS": "15000"
      }
    }
  }
}
```

---

## 4) Verify the Connection

In a new Claude chat, run the tool:

* **db_identity** — confirms DB name, user, version, and search_path.

If it returns your details (e.g., `jhemcpdb`, `jhedbuser`, `PostgreSQL 14.x`), you’re good.

---

## 5) Use It (Examples)

You can ask in natural language and Claude will use the MCP tools to generate SQL, or you can call typed tools directly.

### Natural Language Prompts

* “From JHE, show last 10 Observations with `id, code, transaction_time`.”
* “Count Patients per Study and show the top 5.”
