"""promethica_mcp.py - FastMCP server
================================================
Python rewrite of the JS UniProt MCP server, using
`fastmcp.FastMCP` instead of an HTTP web-server.  The
server is intended to be launched by an MCP-aware LLM
client and communicates over stdio.

Implemented tools (async):
    • search_proteins
    • get_protein_info
    • search_by_gene
    • get_protein_sequence
    • get_protein_features

All remaining tool names are registered but currently
raise `NotImplementedError` so that the metadata is
complete and can be implemented incrementally.

Run:
    python promethica_mcp.py   # launches stdio transport

Requirements:
    pip install fastmcp httpx cachetools pydantic
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal

import httpx, re, signal, sys, logging
from cachetools import TTLCache
from pydantic import BaseModel, Field, ValidationError, conint, constr, root_validator
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Constants & shared client
# ---------------------------------------------------------------------------

UP_API_BASE = "https://rest.uniprot.org"
REACTOME_API_BASE = "https://reactome.org/ContentService/"
RCSB_PDB_API_BASE = "https://search.rcsb.org/rcsbsearch/v2/query"
GO_API_BASE = "http://api.geneontology.org/"
USER_AGENT = "promethica/0.1.0"
TIMEOUT = 30.0
CACHE_TTL_SEC = 3600

logger = logging.getLogger("promethica")
logging.basicConfig(level=logging.INFO)

class UniProtServer:
    def __init__(self):
        self.server = Server(
            {
                "name": "promethica",
                "version": "0.1.0"
            },
            {
                "capabilities": {
                    "resources": {},
                    "tools": {}
                }
            }
        )

        # Initialize UniProt API client
        self.api_client = httpx.Client(
            up_api_base=UP_API_BASE,
            timeout=30.0,
            headers={
                "User-Agent": USER_AGENT
            }
        )

        self._setup_resource_handlers()
        self._setup_tool_handlers()

        # Error handling
        def handle_error(exc: Exception):
            logger.error("[MCP Error] %s", exc)

        self.server.onerror = handle_error

        # Graceful shutdown on SIGINT
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _setup_resource_handlers(self):
        self.server.setRequestHandler(
            
        )

    def _setup_tool_handlers(self):
        # TODO: Implement tool handlers
        pass

    def _handle_sigint(self, signum, frame):
        logger.info("SIGINT received, closing server...")
        self.server.close()
        sys.exit(0)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

cache: TTLCache[str, Any] = TTLCache(maxsize=2048, ttl=CACHE_TTL_SEC)

_http = httpx.AsyncClient(base_url=UP_API_BASE, timeout=TIMEOUT, headers={
    "User-Agent": USER_AGENT,
})

async def _fetch(endpoint: str, params: Dict[str, Any]) -> Any:
    """GET with simple in-memory TTL cache."""
    key = f"{endpoint}|{repr(sorted(params.items()))}"
    if key in cache:
        return cache[key]

    r = await _http.get(endpoint, params=params)
    r.raise_for_status()
    data = r.json() if params.get("format") == "json" else r.text
    cache[key] = data
    return data

# ---------------------------------------------------------------------------
# UniProt API interfaces
# ---------------------------------------------------------------------------

class ValueHolder(BaseModel):
    value: str

class RecommendedName(BaseModel):
    fullName: ValueHolder

class SubmissionName(BaseModel):
    fullName: ValueHolder

class GeneName(BaseModel):
    value: str

class Gene(BaseModel):
    geneName: GeneName

class Organism(BaseModel):
    scientificName: str
    taxonId: int

class OrganismWithCommonName(BaseModel):
    scientificName: str
    taxonId: int
    commonName: Optional[str] = None

class ProteinDescription(BaseModel):
    recommendedName: Optional[RecommendedName] = None
    submissionNames: Optional[List[SubmissionName]] = None

class Sequence(BaseModel):
    value: str
    length: int
    molWeight: float

class ProteinSearchResult(BaseModel):
    primaryAccession: str
    uniProtkbId: str
    entryType: str
    organism: Organism
    proteinDescription: ProteinDescription
    genes: Optional[List[Gene]] = None

class ProteinInfo(BaseModel):
    primaryAccession: str
    uniProtkbId: str
    entryType: str
    organism: OrganismWithCommonName
    proteinDescription: Any
    genes: Optional[List[Any]] = None
    comments: Optional[List[Any]] = None
    features: Optional[List[Any]] = None
    keywords: Optional[List[Any]] = None
    references: Optional[List[Any]] = None
    sequence: Sequence

# ---------------------------------------------------------------------------
# Argument schemas (strict, Pydantic)
# ---------------------------------------------------------------------------

class SearchArgs(BaseModel):
    query: str
    organism: Optional[str] = None
    size: Optional[int] = None
    format: Optional[Literal['json', 'tsv', 'fasta', 'xml']] = None

    @root_validator
    def check_size_range(cls, values):
        size = values.get('size')
        if size is not None and not (0 < size <= 500):
            raise ValueError('size must be between 1 and 500')
        return values


class ProteinInfoArgs(BaseModel):
    accession: str
    format: Optional[Literal['json', 'tsv', 'fasta', 'xml']] = None

    @root_validator
    def check_accession(cls, values):
        accession = values.get('accession')
        if not accession:
            raise ValueError('accession must be a non-empty string')
        return values


class GeneSearchArgs(BaseModel):
    gene: str
    organism: Optional[str] = None
    size: Optional[int] = None

    @root_validator
    def check_size(cls, values):
        size = values.get('size')
        if size is not None and not (0 < size <= 500):
            raise ValueError('size must be between 1 and 500')
        return values


class SequenceArgs(BaseModel):
    accession: str
    format: Optional[Literal['fasta', 'json']] = None

    @root_validator
    def check_accession(cls, values):
        acc = values.get('accession')
        if not acc:
            raise ValueError('accession must be a non-empty string')
        return values

# ---------------------------------------------------------------------------
# Utilities (Validation)
# ---------------------------------------------------------------------------

def is_valid_search_args(args: dict) -> bool:
    try:
        SearchArgs(**args)
        return True
    except ValidationError:
        return False


def is_valid_protein_info_args(args: dict) -> bool:
    try:
        ProteinInfoArgs(**args)
        return True
    except ValidationError:
        return False


def is_valid_gene_search_args(args: dict) -> bool:
    try:
        GeneSearchArgs(**args)
        return True
    except ValidationError:
        return False


def is_valid_sequence_args(args: dict) -> bool:
    try:
        SequenceArgs(**args)
        return True
    except ValidationError:
        return False

# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP("promethica")

# ---------------------------------------------------------------------------
# Helper to register tool with validation wrapper
# ---------------------------------------------------------------------------

def _register(name: str, arg_model: type[BaseModel]):
    def decorator(fn):
        @mcp.tool(name=name)
        async def _inner(**kwargs):
            try:
                args = arg_model(**kwargs)
            except ValidationError as e:
                return f"Parameter validation error: {e}"
            return await fn(args)
        return _inner
    return decorator

# ---------------------------------------------------------------------------
# Implemented tool functions
# ---------------------------------------------------------------------------

@_register("search_proteins", SearchProteinsArgs)
async def _search_proteins(args: SearchProteinsArgs):
    query = args.query
    if args.organism:
        query += f' AND organism_name:"{args.organism}"'
    data = await _fetch("/uniprotkb/search", {
        "query": query,
        "format": args.format,
        "size": args.size,
    })
    return data

@_register("get_protein_info", ProteinInfoArgs)
async def _get_protein_info(args: ProteinInfoArgs):
    data = await _fetch(f"/uniprotkb/{args.accession}", {"format": args.format})
    return data

@_register("search_by_gene", GeneSearchArgs)
async def _search_by_gene(args: GeneSearchArgs):
    query = f'gene:"{args.gene_name}"'
    if args.organism:
        query += f' AND organism_name:"{args.organism}"'
    data = await _fetch("/uniprotkb/search", {
        "query": query,
        "format": "json",
        "size": args.size,
    })
    return data

@_register("get_protein_sequence", SequenceArgs)
async def _get_protein_sequence(args: SequenceArgs):
    data = await _fetch(f"/uniprotkb/{args.accession}", {"format": args.format})
    return data

@_register("get_protein_features", ProteinInfoArgs)
async def _get_protein_features(args: ProteinInfoArgs):
    json_data = await _fetch(f"/uniprotkb/{args.accession}", {"format": "json"})
    if isinstance(json_data, str):
        return "Unexpected non-JSON response."
    feats = {
        "accession": json_data.get("primaryAccession"),
        "features": json_data.get("features", []),
        "comments": json_data.get("comments", []),
    }
    return feats

# ---------------------------------------------------------------------------
# Stub generator for remaining tools
# ---------------------------------------------------------------------------

_remaining_tool_names = [
    "compare_proteins", "get_protein_homologs", "get_protein_orthologs",
    "get_phylogenetic_info", "get_protein_structure", "get_protein_domains_detailed",
    "get_protein_variants", "analyze_sequence_composition", "get_protein_pathways",
    "get_protein_interactions", "search_by_function", "search_by_localization",
    "batch_protein_lookup", "advanced_search", "search_by_taxonomy",
    "get_external_references", "get_literature_references", "get_annotation_confidence",
    "export_protein_data", "validate_accession", "get_taxonomy_info",
]

for _nm in _remaining_tool_names:
    @_register(_nm, BaseModel)
    async def _todo(_: BaseModel, _name=_nm):  # keep default to capture loop var
        return f"Tool '{_name}' not implemented yet."

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
