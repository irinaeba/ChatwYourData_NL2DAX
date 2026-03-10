/**
 * XMLA Worker Service — Node.js proxy for Power BI XMLA queries
 *
 * Connects to Power BI Premium XMLA endpoint via raw SOAP/HTTPS,
 * authenticates via service principal (client credentials),
 * and exposes REST endpoints for Python to call.
 *
 * Linux-compatible — no .NET / no ADOMD / no Windows dependencies.
 * Uses native Node.js fetch (Node 18+) — zero binary deps.
 *
 * Usage:
 *   node server.js                 # starts on port 3100
 *   PORT=8080 node server.js       # custom port
 *
 * Endpoints:
 *   GET  /health                   — health check
 *   GET  /tables                   — TMSCHEMA_TABLES
 *   GET  /columns                  — TMSCHEMA_COLUMNS
 *   GET  /measures                 — TMSCHEMA_MEASURES
 *   GET  /relationships            — TMSCHEMA_RELATIONSHIPS
 *   GET  /measures-dmv             — MDSCHEMA_MEASURES (expressions, folders)
 *   POST /query                    — arbitrary DAX/DMV { "query": "..." }
 */

import "dotenv/config";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";
import { ConfidentialClientApplication } from "@azure/msal-node";
import express from "express";

// ── Load .env from project root ─────────────────────────────
const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, "..", ".env");
import dotenv from "dotenv";
dotenv.config({ path: envPath });

// ── Config ──────────────────────────────────────────────────
const PORT = parseInt(process.env.PORT || "3100", 10);
const TENANT_ID = process.env.TENANT_ID;
const CLIENT_ID = process.env.CLIENT_ID_POWERBI_SCHEMA_EXTRACTION;
const CLIENT_SECRET = process.env.CLIENT_SECRET_POWERBI_SCHEMA_EXTRACTION;
const WORKSPACE_ID = process.env.WORKSPACE_ID;
const WORKSPACE_NAME = process.env.WORKSPACE_NAME;
const DATABASE_NAME = process.env.DATABASE_NAME;

if (!TENANT_ID || !CLIENT_ID || !CLIENT_SECRET || !WORKSPACE_NAME || !DATABASE_NAME) {
  console.error(
    "Missing required env vars: TENANT_ID, CLIENT_ID_POWERBI_SCHEMA_EXTRACTION, " +
      "CLIENT_SECRET_POWERBI_SCHEMA_EXTRACTION, WORKSPACE_NAME, DATABASE_NAME"
  );
  process.exit(1);
}

// ── MSAL client credentials ────────────────────────────────
const msalApp = new ConfidentialClientApplication({
  auth: {
    clientId: CLIENT_ID,
    authority: `https://login.microsoftonline.com/${TENANT_ID}`,
    clientSecret: CLIENT_SECRET,
  },
});

let cachedToken = null;
let tokenExpiry = 0;

async function getToken() {
  if (cachedToken && Date.now() < tokenExpiry - 60_000) return cachedToken;

  const result = await msalApp.acquireTokenByClientCredential({
    scopes: ["https://analysis.windows.net/powerbi/api/.default"],
  });
  if (!result?.accessToken) throw new Error("Token acquisition failed");

  cachedToken = result.accessToken;
  tokenExpiry = Date.now() + 55 * 60_000;
  return cachedToken;
}

// ── Discover actual XMLA cluster URL ────────────────────────
let clusterUrl = null;

async function resolveCluster(token) {
  if (clusterUrl) return clusterUrl;

  const resp = await fetch(
    `https://api.powerbi.com/v1.0/myorg/groups/${WORKSPACE_ID}/datasets`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!resp.ok) throw new Error(`Datasets API: ${resp.status}`);
  const data = await resp.json();
  const ctx = data["@odata.context"] || "";
  const m = ctx.match(/https:\/\/([^/]+)\//);
  if (m) {
    clusterUrl = `https://${m[1]}/xmla`;
    return clusterUrl;
  }
  throw new Error("Could not resolve XMLA cluster from datasets response");
}

// ── SOAP builder ────────────────────────────────────────────

function buildExecuteSoap(query, catalog) {
  const escaped = query
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  return `<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope
 xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:xmla="urn:schemas-microsoft-com:xml-analysis">
<soap:Body>
<xmla:Execute>
 <xmla:Command>
  <xmla:Statement>${escaped}</xmla:Statement>
 </xmla:Command>
 <xmla:Properties>
  <xmla:PropertyList>
   <xmla:Catalog>${catalog}</xmla:Catalog>
   <xmla:Format>Tabular</xmla:Format>
   <xmla:Content>SchemaData</xmla:Content>
  </xmla:PropertyList>
 </xmla:Properties>
</xmla:Execute>
</soap:Body>
</soap:Envelope>`;
}

// ── SOAP response parser ────────────────────────────────────

function parseSoapResponse(xmlText) {
  // Check for SOAP fault
  const faultMatch = xmlText.match(
    /<faultstring[^>]*>([\s\S]*?)<\/faultstring>/i
  );
  if (faultMatch) {
    throw new Error(`SOAP Fault: ${faultMatch[1].trim()}`);
  }

  // Check for XMLA error
  const errorMatch = xmlText.match(
    /<Error[^>]*Description="([^"]*)"[^>]*\/?>/i
  );
  if (errorMatch) {
    throw new Error(`XMLA Error: ${errorMatch[1]}`);
  }

  // Parse <row>...</row> elements
  const rows = [];
  const rowRegex = /<row\b[^>]*>([\s\S]*?)<\/row>/gi;
  let rowMatch;

  while ((rowMatch = rowRegex.exec(xmlText)) !== null) {
    const rowXml = rowMatch[1];
    const obj = {};

    // Extract <FieldName>value</FieldName>
    const fieldRegex =
      /<([A-Za-z_][A-Za-z0-9_.-]*?)(?:\s[^>]*)?>([^<]*)<\/\1>/g;
    let fieldMatch;

    while ((fieldMatch = fieldRegex.exec(rowXml)) !== null) {
      const key = fieldMatch[1];
      let val = fieldMatch[2]
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&apos;/g, "'");

      if (/^-?\d+$/.test(val)) obj[key] = parseInt(val, 10);
      else if (/^-?\d+\.\d+$/.test(val)) obj[key] = parseFloat(val);
      else if (val === "true") obj[key] = true;
      else if (val === "false") obj[key] = false;
      else obj[key] = val;
    }

    if (Object.keys(obj).length > 0) rows.push(obj);
  }

  return rows;
}

// ── Core XMLA request ──────────────────────────────────────

async function executeXmla(query) {
  const token = await getToken();
  const xmlaBase = await resolveCluster(token);
  const url = `${xmlaBase}?vs=sobe_wowvirtualserver&db=${encodeURIComponent(DATABASE_NAME)}`;
  const soapBody = buildExecuteSoap(query, DATABASE_NAME);

  console.log(`  XMLA → ${url}`);
  console.log(`  Query: ${query.substring(0, 100)}${query.length > 100 ? "..." : ""}`);

  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "text/xml; charset=utf-8",
      Authorization: `Bearer ${token}`,
      SOAPAction: "urn:schemas-microsoft-com:xml-analysis:Execute",
    },
    body: soapBody,
  });

  const text = await resp.text();

  if (!resp.ok) {
    console.error(`  HTTP ${resp.status}`);
    if (text) console.error(`  Body: ${text.substring(0, 500)}`);
    throw new Error(
      `XMLA HTTP ${resp.status}: ${text.substring(0, 300) || "(empty)"}`
    );
  }

  return parseSoapResponse(text);
}

// ── Express app ────────────────────────────────────────────
const app = express();
app.use(express.json());

app.get("/health", (_req, res) => {
  res.json({
    status: "ok",
    workspace: WORKSPACE_NAME,
    database: DATABASE_NAME,
    cluster: clusterUrl || "(not resolved yet)",
  });
});

// Pre-built DMV routes
const DMV_QUERIES = {
  tables: "SELECT * FROM $SYSTEM.TMSCHEMA_TABLES",
  columns: "SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS",
  measures: "SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES",
  relationships: "SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS",
  "measures-dmv": `SELECT [MEASURE_NAME],[MEASURE_CAPTION],[DESCRIPTION],[EXPRESSION],[MEASURE_DISPLAY_FOLDER],[DEFAULT_FORMAT_STRING],[MEASUREGROUP_NAME],[DATA_TYPE],[MEASURE_IS_VISIBLE] FROM $SYSTEM.MDSCHEMA_MEASURES`,
};

for (const [route, query] of Object.entries(DMV_QUERIES)) {
  app.get(`/${route}`, async (_req, res) => {
    try {
      console.log(`\n[${new Date().toISOString()}] GET /${route}`);
      const rows = await executeXmla(query);
      console.log(`  ✅ ${rows.length} rows`);
      res.json({ rows, count: rows.length });
    } catch (err) {
      console.error(`  ❌ ${err.message}`);
      res.status(500).json({ error: err.message });
    }
  });
}

app.post("/query", async (req, res) => {
  const { query } = req.body;
  if (!query) return res.status(400).json({ error: "Missing 'query' in body" });

  try {
    console.log(`\n[${new Date().toISOString()}] POST /query`);
    const rows = await executeXmla(query);
    console.log(`  ✅ ${rows.length} rows`);
    res.json({ rows, count: rows.length });
  } catch (err) {
    console.error(`  ❌ ${err.message}`);
    res.status(500).json({ error: err.message });
  }
});

// ── Start ──────────────────────────────────────────────────
app.listen(PORT, async () => {
  console.log(`\n🚀 XMLA Worker on http://localhost:${PORT}`);
  console.log(`   Workspace: ${WORKSPACE_NAME}`);
  console.log(`   Database:  ${DATABASE_NAME}`);
  console.log(`   Routes:    /tables /columns /measures /relationships /measures-dmv /query\n`);

  try {
    const token = await getToken();
    console.log(`   ✅ Token acquired`);
    await resolveCluster(token);
    console.log(`   ✅ Cluster: ${clusterUrl}`);
  } catch (err) {
    console.error(`   ❌ Startup: ${err.message}`);
  }
});
