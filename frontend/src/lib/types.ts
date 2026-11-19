export interface User {
  id: string;
  email: string;
  name: string;
  provider_order: string[] | null;
  registration_open?: boolean;
}

export interface Job {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  message: string | null;
  error: string | null;
}

export interface Scores {
  overall: number;
  grade: string;
  categories: { maintainability: number; security: number; reliability: number };
}

export interface Metrics {
  total_files: number;
  source_files: number;
  parsed_files: number;
  files_with_syntax_errors: number;
  loc: number;
  functions: number;
  methods: number;
  classes: number;
  interfaces: number;
  traits: number;
  enums: number;
  endpoints: number;
  data_models: number;
  packages: number;
  edges: number;
  cycles: number;
  test_files: number;
  avg_complexity: number;
}

export interface LanguageStat {
  language: string;
  files: number;
  loc: number;
}

export interface SearchIndexStatus {
  status: string;
  chunks?: number;
  embedded?: number;
  reused?: number;
  error?: string;
}

export interface Project {
  id: string;
  name: string;
  source_type: "github" | "zip";
  source_url: string | null;
  branch: string | null;
  head_commit: string | null;
  status: "pending" | "importing" | "analyzing" | "ready" | "failed";
  error: string | null;
  local_ai_only: boolean;
  created_at: string;
  analyzed_at: string | null;
  frameworks: string[];
  languages: LanguageStat[];
  metrics: Metrics | null;
  score: Scores | null;
  search_index: SearchIndexStatus | null;
  job: Job | null;
}

export interface Issue {
  id?: number;
  rule: string;
  category: string;
  severity: "low" | "medium" | "high" | "critical";
  message: string;
  path: string | null;
  line: number | null;
}

export interface Dashboard {
  metrics: Metrics;
  frameworks: string[];
  languages: LanguageStat[];
  quality: {
    scores: Scores;
    total_issues: number;
    by_severity: Record<string, number>;
    by_category: Record<string, number>;
    top_rules: { rule: string; count: number }[];
  };
  complexity_distribution: { bucket: string; count: number }[];
  hotspots: {
    complex_functions: { name: string; path: string; line: number; complexity: number; loc: number }[];
    most_depended_on: { path: string; dependents: number }[];
    most_dependencies: { path: string; dependencies: number }[];
    largest_files: { path: string; loc: number; language: string }[];
  };
  search_index: SearchIndexStatus | null;
  generated_at: string;
  top_issues: Issue[];
}

export interface Architecture {
  frameworks: string[];
  languages: LanguageStat[];
  runtime: Record<string, string | null>;
  layers: { role: string; label: string; files: number }[];
  entry_points: string[];
  module_graph: {
    modules: { name: string; files: number; loc: number; language: string | null }[];
    links: { source: string; target: string; weight: number }[];
  };
  drupal_hooks: { module: string; hook: string; function: string; path: string; line: number }[];
  cycles: string[][];
}

export interface Page<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
}

export interface SymbolItem {
  id: number;
  kind: string;
  name: string;
  qualified_name: string;
  path: string;
  start_line: number;
  end_line: number;
  signature: string | null;
  docstring: string | null;
  complexity: number;
  loc: number;
}

export interface EndpointItem {
  id: number;
  method: string;
  path: string;
  handler: string | null;
  framework: string;
  file: string;
  line: number;
  middleware: string[];
}

export interface ModelItem {
  id: number;
  name: string;
  table_name: string | null;
  framework: string;
  file: string;
  line: number;
  fields: { name: string; type: string }[];
  relations: { name: string; type: string; target: string }[];
}

export interface PackageItem {
  id: number;
  name: string;
  version: string | null;
  ecosystem: string;
  manifest_path: string;
  is_dev: boolean;
}

export interface FileItem {
  path: string;
  language: string;
  role: string;
  loc: number;
  complexity: number;
  is_parsed: boolean;
}

export interface GraphNode {
  id: string;
  label: string;
  path: string;
  language: string | null;
  role: string;
  loc: number;
  complexity: number;
  fan_in: number;
  fan_out: number;
  module: string;
  kind?: string;
  line?: number;
  qualified_name?: string;
  files?: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kinds: Record<string, number>;
  weight: number;
}

export interface GraphData {
  level: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  total_nodes: number;
  truncated: boolean;
}

export interface FileDetail {
  path: string;
  language: string;
  role: string;
  loc: number;
  complexity: number;
  content?: string;
  dependencies: { path: string; kinds: Record<string, number> }[];
  dependents: { path: string; kinds: Record<string, number> }[];
  symbols: {
    id: number;
    kind: string;
    name: string;
    qualified_name: string;
    start_line: number;
    end_line: number;
    complexity: number;
    signature: string | null;
  }[];
}

export interface Citation {
  path: string;
  start_line: number;
  end_line: number;
  /** The answer named this code without citing it, so LegacyLens found the reference. */
  inferred?: boolean;
}

export interface Source extends Citation {
  symbol: string | null;
  score: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  sources: Source[];
  provider: string | null;
  model: string | null;
  degraded: boolean;
  created_at: string;
}

export interface Commit {
  sha: string;
  short_sha: string;
  author: string;
  date: string;
  subject: string;
}

export type Severity = "low" | "medium" | "high" | "critical";

export interface Risk {
  rule: string;
  severity: Severity;
  title: string;
  detail: string;
  evidence: { path: string; line: number | null }[];
}

export interface ComparisonResult {
  base: string;
  head: string;
  graph_note: string | null;
  stats: {
    files_changed: number;
    additions: number;
    deletions: number;
    symbols_added: number;
    symbols_modified: number;
    symbols_removed: number;
    impacted_files: number;
  };
  files: { path: string; old_path: string | null; status: string; additions: number; deletions: number }[];
  symbols: {
    status: string;
    path: string;
    name: string;
    qualified_name: string;
    kind: string;
    line: number;
    signature: string | null;
    signature_changed?: boolean;
    complexity: number;
  }[];
  modules: { module: string; changed_files: number; impacted_files: number }[];
  impacted: { path: string; distance: number; via: string; role: string }[];
  endpoints: { method: string; path: string; handler: string | null; file: string; line: number; reason: string }[];
  risks: Risk[];
  risk: { score: number; level: Severity };
}

export interface Comparison {
  id: string;
  base_commit: string;
  head_commit: string;
  result: ComparisonResult;
  ai_summary: string | null;
  ai_error: string | null;
  provider: string | null;
  model: string | null;
  created_at: string;
}

export interface TestRun {
  id: string;
  test_id: string;
  status: "queued" | "running" | "passed" | "failed" | "error" | "timeout";
  install_dependencies: boolean;
  exit_code: number | null;
  output: string | null;
  results: {
    total: number;
    passed: number;
    failed: number;
    error: number;
    skipped: number;
    cases: { name: string; status: string; message: string | null; time: number }[];
  } | null;
  duration_seconds: number | null;
}

export interface GeneratedTest {
  id: string;
  target_path: string;
  target_symbol: string | null;
  language: string;
  framework: string;
  test_path: string;
  code: string;
  source: "ai" | "baseline" | "edited";
  provider: string | null;
  model: string | null;
  notice: string | null;
  created_at: string;
  latest_run?: TestRun | null;
}

export interface DocumentItem {
  id: string;
  title: string;
  status: "queued" | "running" | "ready" | "failed";
  commit: string | null;
  ai_enhanced: boolean;
  provider: string | null;
  model: string | null;
  error: string | null;
  created_at: string;
  content?: string | null;
}

export interface ProviderModel {
  model: string;
  state: "ready" | "cooldown" | "budget_exhausted" | "not_configured";
  reason: string | null;
  detail: string | null;
  available_at: string | null;
  requests_today: number;
  requests_last_minute: number;
  daily_budget: number | null;
  minute_budget: number | null;
  tokens_today: number;
}

export interface ProviderInfo {
  name: string;
  configured: boolean;
  is_local: boolean;
  privacy: string;
  models: ProviderModel[];
}

export interface ProvidersResponse {
  order: string[];
  default_order: string[];
  custom_order: boolean;
  providers: ProviderInfo[];
}
